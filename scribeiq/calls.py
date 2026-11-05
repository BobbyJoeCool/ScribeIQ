"""Turn a recorded call (two synced tracks) into one speaker-labeled transcript.

The microphone track only contains the user, so it never needs speaker
identification. The other track ("them") gets diarized only when more than one
person is on the other end.
"""

import gc
import json
from pathlib import Path

from . import pipeline


def label_my_segments(segments: list, my_name: str) -> list:
    return [{**seg, "speaker": my_name} for seg in segments]


def label_their_segments(segments: list, turns: list | None, single_name: str = "Them") -> list:
    """No turns (one person on the other end) -> everyone is single_name.
    Otherwise use the diarization turns, renamed to Speaker 1, Speaker 2, ..."""
    if not turns:
        return [{**seg, "speaker": single_name} for seg in segments]
    return pipeline.friendly_speaker_names(pipeline.assign_speakers(segments, turns))


def pause_markers(pauses: list) -> list:
    """Session pauses ([{"at": seconds, "length": seconds}]) as transcript markers."""
    return [
        {"start": p["at"], "end": p["at"], "text": "", "speaker": "",
         "marker": f"recording paused for {format_duration(p['length'])}"}
        for p in pauses
    ]


def merge_tracks(mine: list, theirs: list, markers: list = ()) -> list:
    """Interleave both sides (and any markers) by start time. Ties keep the
    order mine, theirs, markers so output is stable."""
    tagged = ([(seg["start"], 0, i, seg) for i, seg in enumerate(mine)]
              + [(seg["start"], 1, i, seg) for i, seg in enumerate(theirs)]
              + [(seg["start"], 2, i, seg) for i, seg in enumerate(markers)])
    return [seg for *_, seg in sorted(tagged, key=lambda t: t[:3])]


def rename_speakers(merged: list, mapping: dict) -> list:
    """Apply user-chosen names, e.g. {"Speaker 1": "Dave"}."""
    return [{**seg, "speaker": mapping.get(seg["speaker"], seg["speaker"])} for seg in merged]


def parse_rename_text(text: str) -> dict:
    """"Speaker 1 = Dave, Them = Sarah" (or one per line) -> mapping."""
    mapping = {}
    for part in text.replace("\n", ",").split(","):
        if "=" in part:
            old, new = (s.strip() for s in part.split("=", 1))
            if old and new:
                mapping[old] = new
    return mapping


def format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    m, s = divmod(seconds, 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


def load_session(session_path: Path) -> dict:
    if session_path and Path(session_path).exists():
        return json.loads(Path(session_path).read_text())
    return {}


def transcribe_call(me_wav: Path, them_wav: Path, *, my_name: str, their_count: int,
                    model: str, session_path: Path | None = None, progress=None) -> list:
    """Full call pipeline. their_count is how many people are on the other end
    (1 skips diarization, so no Hugging Face token is needed for 1:1 calls)."""
    progress = progress or (lambda fraction, desc: None)
    if their_count > 1:
        pipeline.require_hf_token()  # fail before the slow part, not after

    me_audio = them_audio = None
    try:
        progress(0.05, "Preparing audio...")
        me_audio = pipeline.prepare_audio(str(me_wav))
        them_audio = pipeline.prepare_audio(str(them_wav))

        progress(0.10, f"Transcribing {my_name}...")
        mine = pipeline.transcribe(me_audio, model, call_track=True)
        gc.collect()

        progress(0.45, "Transcribing the other side...")
        theirs = pipeline.transcribe(them_audio, model, call_track=True)
        gc.collect()

        turns = None
        if their_count > 1:
            progress(0.80, "Identifying speakers...")
            turns = pipeline.diarize(them_audio, their_count)
            gc.collect()

        progress(0.95, "Merging...")
        session = load_session(session_path)
        return merge_tracks(label_my_segments(mine, my_name),
                            label_their_segments(theirs, turns),
                            pause_markers(session.get("pauses", [])))
    finally:
        for tmp in (me_audio, them_audio):
            if tmp:
                Path(tmp).unlink(missing_ok=True)
