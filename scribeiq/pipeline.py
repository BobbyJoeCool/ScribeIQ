"""Core transcription pipeline: audio in, timestamped (and optionally
speaker-labeled) text out. Runs entirely on-device.

- Transcription: MLX Whisper (Metal-accelerated on Apple Silicon).
- Speaker identification: pyannote.audio (needs a free Hugging Face token).

Models are loaded one at a time and released between steps, so a long file
never holds Whisper and pyannote in memory together.
"""

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

AUDIO_CACHE = Path(tempfile.gettempdir()) / "scribeiq_audio"
STALL_TIMEOUT = 60      # seconds with no data before treating a download as dropped
MAX_ATTEMPTS = 8        # download attempts (each resumes where the last stopped)
PROGRESS_EVERY = 10     # seconds between download progress lines

MODEL_REPOS = {
    "large-v3": "mlx-community/whisper-large-v3-mlx",   # best accuracy, ~10GB, needs 16GB+
    "turbo": "mlx-community/whisper-large-v3-turbo",    # 6-9x faster, ~1.6GB, tiny accuracy hit
}


class PipelineError(RuntimeError):
    """A problem the user can fix (missing file, missing token, bad URL...)."""


def is_url(source: str) -> bool:
    return str(source).startswith(("http://", "https://"))


def source_name(url_or_path: str) -> str:
    """Default output name: the audio's filename without its extension.
    ".../My%20Talk.mp3?x=1" -> "My Talk", "~/clip.m4a" -> "clip"."""
    if is_url(url_or_path):
        name = unquote(Path(urlparse(url_or_path).path).name)
    else:
        name = Path(url_or_path).name
    return Path(name).stem or "transcript"


def cached_audio_path(url: str) -> Path:
    return AUDIO_CACHE / (Path(url.split("?")[0]).name or "audio.mp3")


def clear_cached_audio(url_or_path: str) -> None:
    """Delete a downloaded file once its transcript is written. Local files
    the user passed in are never touched."""
    if is_url(url_or_path):
        cached_audio_path(url_or_path).unlink(missing_ok=True)


def fetch_url(url: str) -> Path:
    """Download url into AUDIO_CACHE and return the file path.

    Large audio hosts can be slow or stall mid-file, so this treats
    STALL_TIMEOUT seconds of silence as a dropped connection and resumes with an
    HTTP Range request instead of hanging or starting over.
    """
    AUDIO_CACHE.mkdir(parents=True, exist_ok=True)
    done = cached_audio_path(url)
    if done.exists():
        print(f"Using cached audio {done}")
        return done
    part = done.with_name(done.name + ".part")

    print(f"Downloading audio from {url} ...", flush=True)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with requests.get(url, stream=True, headers=headers,
                              timeout=(15, STALL_TIMEOUT)) as resp:
                if resp.status_code == 416:  # range starts at the end: already complete
                    break
                resp.raise_for_status()
                content_type = resp.headers.get("Content-Type", "")
                if not content_type.startswith("audio/") and "octet-stream" not in content_type:
                    raise PipelineError(
                        f"Expected an audio file but the URL returned '{content_type}'. "
                        "The link probably points to a web page with a player on it "
                        "rather than the audio file itself.")
                if have and resp.status_code != 206:
                    have = 0  # server ignored the Range header; start over
                total = have + int(resp.headers.get("Content-Length", 0)) or None
                got, started = have, time.time()
                last = started
                with open(part, "ab" if have else "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 16):
                        f.write(chunk)
                        got += len(chunk)
                        now = time.time()
                        if now - last >= PROGRESS_EVERY:
                            last = now
                            print(download_progress(got, total, got - have, now - started),
                                  flush=True)
                if total is None or got >= total:
                    break
                print(f"Connection closed early at {got / 1e6:.1f} MB; retrying...", flush=True)
        except (requests.ConnectionError, requests.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            print(f"Download interrupted ({type(e).__name__}); retrying...", flush=True)
        if attempt == MAX_ATTEMPTS:
            raise PipelineError(f"Download failed after {MAX_ATTEMPTS} attempts. Try again "
                                f"to resume; the partial file is kept at {part}")
        time.sleep(min(2 ** attempt, 30))

    part.rename(done)
    return done


def download_progress(got, total, fetched, elapsed):
    speed = fetched / elapsed if elapsed else 0
    line = f"  {got / 1e6:.1f}"
    line += f" / {total / 1e6:.1f} MB ({100 * got / total:.0f}%)" if total else " MB"
    line += f" at {speed / 1e3:.0f} KB/s"
    if total and speed:
        m, s = divmod(int((total - got) / speed), 60)
        line += f", about {m}m{s:02d}s left"
    return line


def prepare_audio(url_or_path: str) -> Path:
    """Return a 16kHz mono WAV copy of the audio (downloading it first if it's
    a URL) in a fresh temp file. The caller deletes it when done.

    Transcoding matters beyond format: pyannote's own decoder honors an MP3's
    gapless-playback header and trims ~1105 samples that pyannote's chunk math
    doesn't expect, which makes it error out. A plain WAV sidesteps that.
    """
    raw = fetch_url(url_or_path) if is_url(url_or_path) else Path(url_or_path).expanduser()
    if not raw.exists():
        raise PipelineError(f"File not found: {raw}")
    if not shutil.which("ffmpeg"):
        raise PipelineError("ffmpeg not found. Install it with: brew install ffmpeg")

    fd, tmp = tempfile.mkstemp(prefix="scribeiq_", suffix=".wav")
    os.close(fd)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(raw), "-ar", "16000", "-ac", "1", tmp],
        check=True, stdout=subprocess.DEVNULL,
    )
    return Path(tmp)


def transcribe(audio_path: Path, model: str = "large-v3", call_track: bool = False) -> list:
    """Transcribe with MLX Whisper. Returns [{"start", "end", "text"}, ...].

    call_track=True is for one side of a recorded call, which is mostly
    silence while the other person talks. Whisper tends to invent text in long
    silences, so this skips silent stretches (hallucination_silence_threshold)
    and stops each segment from being primed by the previous one.
    """
    import mlx_whisper

    options = {}
    if call_track:
        options = {"word_timestamps": True, "hallucination_silence_threshold": 2.0,
                   "condition_on_previous_text": False}
    result = mlx_whisper.transcribe(str(audio_path), path_or_hf_repo=MODEL_REPOS[model],
                                    **options)
    return [
        {"start": seg["start"], "end": seg["end"], "text": seg["text"].strip()}
        for seg in result["segments"]
        if seg["text"].strip()
    ]


def load_waveform(wav_path: Path) -> dict:
    """Read a 16-bit PCM WAV into the in-memory form pyannote accepts.

    Passing pyannote a path makes it decode with torchcodec, whose macOS wheels
    often can't find Homebrew's FFmpeg libraries. A preloaded waveform skips it.
    """
    import wave

    import numpy as np
    import torch

    with wave.open(str(wav_path), "rb") as w:
        channels = w.getnchannels()
        sample_rate = w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    samples = pcm.reshape(-1, channels).T.astype(np.float32) / 32768.0
    return {"waveform": torch.from_numpy(samples), "sample_rate": sample_rate}


def require_hf_token() -> str:
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise PipelineError("Identifying speakers needs HF_TOKEN set in .env or your "
                            "shell (see README).")
    return token


def diarize(audio_path: Path, num_speakers=None) -> list:
    """Speaker turns from pyannote: [{"start", "end", "speaker"}, ...]."""
    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1",
                                        token=require_hf_token())
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    pipeline.to(device)

    kwargs = {"num_speakers": num_speakers} if num_speakers else {}
    result = pipeline(load_waveform(audio_path), **kwargs)
    # pyannote.audio 4.x wraps the result; older versions return the Annotation.
    annotation = getattr(result, "speaker_diarization", result)
    return [
        {"start": turn.start, "end": turn.end, "speaker": speaker}
        for turn, _, speaker in annotation.itertracks(yield_label=True)
    ]


def assign_speakers(segments: list, turns: list) -> list:
    """Give each segment the speaker whose turn overlaps it most."""
    merged = []
    for seg in segments:
        best_speaker, best_overlap = "UNKNOWN", 0.0
        for turn in turns:
            overlap = min(seg["end"], turn["end"]) - max(seg["start"], turn["start"])
            if overlap > best_overlap:
                best_overlap, best_speaker = overlap, turn["speaker"]
        merged.append({**seg, "speaker": best_speaker})
    return merged


def friendly_speaker_names(merged: list, prefix: str = "Speaker") -> list:
    """Replace pyannote's SPEAKER_00 labels with Speaker 1, Speaker 2, ... in
    order of who talks first."""
    names = {}
    for seg in merged:
        label = seg["speaker"]
        if label not in names and label.startswith("SPEAKER_"):
            names[label] = f"{prefix} {len(names) + 1}"
    return [{**seg, "speaker": names.get(seg["speaker"], seg["speaker"])} for seg in merged]


def format_timestamp(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_transcript(merged: list) -> str:
    """One timestamped header per speaker turn, with that speaker's
    consecutive segments flowing together underneath it. Segments marked
    {"marker": text} render as a standalone note (e.g. a pause)."""
    out, current = [], None
    for seg in merged:
        if seg.get("marker"):
            out.append(f"\n\n[{format_timestamp(seg['start'])}] ({seg['marker']})")
            current = None
            continue
        if seg["speaker"] != current:
            out.append(f"\n\n[{format_timestamp(seg['start'])}] {seg['speaker']}:\n")
            current = seg["speaker"]
        out.append(f"{seg['text']} ")
    lines = "".join(out).strip().split("\n")
    return "\n".join(line.rstrip() for line in lines) + "\n"


def format_markdown(merged: list, title: str) -> str:
    """Same layout as format_transcript, as Markdown."""
    out, current = [f"# {title}\n"], None
    for seg in merged:
        if seg.get("marker"):
            out.append(f"\n*[{format_timestamp(seg['start'])}] {seg['marker']}*\n")
            current = None
            continue
        if seg["speaker"] != current:
            out.append(f"\n**{seg['speaker']}** `{format_timestamp(seg['start'])}`\n\n")
            current = seg["speaker"]
        out.append(f"{seg['text']} ")
    return "".join(out).strip() + "\n"


def format_plain_transcript(segments: list) -> str:
    """No speaker labels: one timestamped line per segment."""
    return "".join(f"[{format_timestamp(seg['start'])}] {seg['text']}\n" for seg in segments)


def unique_path(folder: Path, name: str, suffix: str) -> Path:
    """folder/<name><suffix>, adding " (2)", " (3)", ... instead of overwriting."""
    name = name.strip().replace("/", "-").replace("\\", "-") or "transcript"
    folder.mkdir(parents=True, exist_ok=True)
    path, n = folder / f"{name}{suffix}", 2
    while path.exists():
        path, n = folder / f"{name} ({n}){suffix}", n + 1
    return path


def transcribe_source(source: str, *, model: str = "large-v3", identify_speakers: bool = True,
                      num_speakers: int | None = None, progress=None) -> list:
    """Transcribe one audio file or URL. With identify_speakers, segments carry
    Speaker 1, Speaker 2, ... labels; otherwise they have no "speaker" key.
    The downloaded copy of a URL is deleted afterwards."""
    import gc

    progress = progress or (lambda fraction, desc: None)
    if identify_speakers:
        require_hf_token()  # fail before the slow part, not after
    progress(0.05, "Preparing audio...")
    wav = prepare_audio(source)
    try:
        progress(0.15, f"Transcribing with {model}...")
        segments = transcribe(wav, model)
        gc.collect()
        if identify_speakers:
            progress(0.70, "Identifying speakers...")
            turns = diarize(wav, num_speakers or None)
            segments = friendly_speaker_names(assign_speakers(segments, turns))
    finally:
        wav.unlink(missing_ok=True)
    clear_cached_audio(source)
    return segments
