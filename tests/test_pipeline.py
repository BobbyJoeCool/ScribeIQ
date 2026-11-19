"""Tests for scribeiq/pipeline.py: audio prep, speaker assignment and the
transcript formats. Whisper, pyannote and the network are stubbed; no model is
loaded and nothing is downloaded.

Run with:  python -m unittest discover tests
"""

import os
import shutil
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

from scribeiq import pipeline

GOLDEN = Path(__file__).with_name("golden")


def write_wav(path: Path, seconds: float = 0.1, rate: int = 8000, channels: int = 2) -> Path:
    """A short silent 16-bit WAV, made at test time (WAVs are gitignored)."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * channels * int(rate * seconds))
    return path


# A call with grouped turns, a pause marker, and an hour-plus timestamp.
CALL = [
    {"start": 0.0, "end": 2.0, "text": "Hi there.", "speaker": "Bob"},
    {"start": 2.2, "end": 3.0, "text": "Can you hear me?", "speaker": "Bob"},
    {"start": 3.5, "end": 5.0, "text": "Loud and clear.", "speaker": "Speaker 1"},
    {"start": 65.0, "end": 65.0, "text": "", "speaker": "", "marker": "recording paused for 1m 05s"},
    {"start": 70.0, "end": 72.0, "text": "Back again.", "speaker": "Speaker 1"},
    {"start": 3725.0, "end": 3730.0, "text": "Wrapping up.", "speaker": "Bob"},
]


class NameTests(unittest.TestCase):
    def test_is_url(self):
        self.assertTrue(pipeline.is_url("https://x.com/a.mp3"))
        self.assertTrue(pipeline.is_url("http://x.com/a.mp3"))
        self.assertFalse(pipeline.is_url("ftp://x.com/a.mp3"))
        self.assertFalse(pipeline.is_url("/Users/bob/a.mp3"))
        self.assertFalse(pipeline.is_url(""))

    def test_source_name(self):
        self.assertEqual(pipeline.source_name("https://x.com/p/My%20Talk.mp3?x=1"), "My Talk")
        self.assertEqual(pipeline.source_name("~/clip.m4a"), "clip")
        self.assertEqual(pipeline.source_name("https://x.com/"), "transcript")
        self.assertEqual(pipeline.source_name(""), "transcript")

    def test_unique_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "out"
            first = pipeline.unique_path(folder, "a/b", ".txt")
            self.assertEqual(first.name, "a-b.txt")
            first.write_text("x")
            self.assertEqual(pipeline.unique_path(folder, "a/b", ".txt").name, "a-b (2).txt")
            self.assertEqual(pipeline.unique_path(folder, "  ", ".txt").name, "transcript.txt")


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(pipeline, "AUDIO_CACHE", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_cached_path_drops_query_string(self):
        self.assertEqual(pipeline.cached_audio_path("https://x.com/a/ep.mp3?token=1").name, "ep.mp3")

    def test_clear_removes_downloads_only(self):
        url = "https://x.com/ep.mp3"
        cached = pipeline.cached_audio_path(url)
        cached.write_bytes(b"x")
        local = write_wav(Path(self.tmp.name) / "mine.wav")
        pipeline.clear_cached_audio(str(local))
        self.assertTrue(local.exists())
        pipeline.clear_cached_audio(url)
        self.assertFalse(cached.exists())
        pipeline.clear_cached_audio(url)  # already gone: no error

    def test_cached_download_is_reused_without_network(self):
        url = "https://x.com/ep.mp3"
        pipeline.cached_audio_path(url).write_bytes(b"x")
        with mock.patch.object(pipeline.requests, "get") as get, \
                mock.patch("builtins.print"):
            self.assertEqual(pipeline.fetch_url(url), pipeline.cached_audio_path(url))
        get.assert_not_called()

    def test_web_page_instead_of_audio_is_refused(self):
        response = mock.MagicMock(status_code=200, headers={"Content-Type": "text/html"})
        response.__enter__.return_value = response
        with mock.patch.object(pipeline.requests, "get", return_value=response), \
                mock.patch("builtins.print"):
            with self.assertRaisesRegex(pipeline.PipelineError, "web page"):
                pipeline.fetch_url("https://x.com/player")

    def test_download_writes_file(self):
        response = mock.MagicMock(status_code=200, headers={"Content-Type": "audio/mpeg",
                                                            "Content-Length": "6"})
        response.__enter__.return_value = response
        response.iter_content.return_value = [b"abc", b"def"]
        with mock.patch.object(pipeline.requests, "get", return_value=response), \
                mock.patch("builtins.print"):
            path = pipeline.fetch_url("https://x.com/ep.mp3")
        self.assertEqual(path.read_bytes(), b"abcdef")
        self.assertFalse(path.with_name("ep.mp3.part").exists())

    def test_download_progress_line(self):
        self.assertEqual(pipeline.download_progress(5e6, 10e6, 5e6, 10),
                         "  5.0 / 10.0 MB (50%) at 500 KB/s, about 0m10s left")
        self.assertEqual(pipeline.download_progress(5e6, None, 0, 0), "  5.0 MB at 0 KB/s")


class PrepareAudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_missing_file(self):
        with self.assertRaisesRegex(pipeline.PipelineError, "File not found"):
            pipeline.prepare_audio(str(self.dir / "nope.wav"))

    def test_missing_ffmpeg(self):
        wav = write_wav(self.dir / "a.wav")
        with mock.patch.object(pipeline.shutil, "which", return_value=None):
            with self.assertRaisesRegex(pipeline.PipelineError, "brew install ffmpeg"):
                pipeline.prepare_audio(str(wav))

    def test_ffmpeg_is_asked_for_16khz_mono(self):
        wav = write_wav(self.dir / "a.wav")
        with mock.patch.object(pipeline.shutil, "which", return_value="/bin/ffmpeg"), \
                mock.patch.object(pipeline.subprocess, "run") as run:
            out = pipeline.prepare_audio(str(wav))
        self.addCleanup(out.unlink, missing_ok=True)
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("-i") + 1], str(wav))
        self.assertEqual(args[args.index("-ar") + 1], "16000")
        self.assertEqual(args[args.index("-ac") + 1], "1")
        self.assertEqual(args[-1], str(out))

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_real_conversion_of_a_fixture_wav(self):
        out = pipeline.prepare_audio(str(write_wav(self.dir / "a.wav")))
        self.addCleanup(out.unlink, missing_ok=True)
        with wave.open(str(out)) as w:
            self.assertEqual((w.getframerate(), w.getnchannels()), (16000, 1))

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_unreadable_audio_is_a_pipeline_error_and_leaves_no_temp_file(self):
        bad = self.dir / "bad.m4a"
        bad.write_text("not audio")
        before = set(Path(tempfile.gettempdir()).glob("scribeiq_*.wav"))
        with self.assertRaises(pipeline.PipelineError) as ctx:
            pipeline.prepare_audio(str(bad))
        self.assertEqual(str(ctx.exception), "Couldn't read bad.m4a as audio "
                                             "(Invalid data found when processing input).")
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("scribeiq_*.wav")), before)

    def test_ffmpeg_reason(self):
        self.assertEqual(pipeline.ffmpeg_reason(
            "[in#0] moov atom not found\nError opening input files: Invalid data\n\n", 183),
            "Invalid data")
        self.assertEqual(pipeline.ffmpeg_reason("", 1), "ffmpeg exited with code 1")
        self.assertEqual(pipeline.ffmpeg_reason(None, 1), "ffmpeg exited with code 1")

    def test_load_waveform_reads_pcm(self):
        try:
            import numpy  # noqa: F401
            import torch  # noqa: F401
        except ImportError:
            self.skipTest("numpy/torch not installed")
        result = pipeline.load_waveform(write_wav(self.dir / "a.wav", seconds=0.5, rate=16000))
        self.assertEqual(result["sample_rate"], 16000)
        self.assertEqual(tuple(result["waveform"].shape), (2, 8000))


class SpeakerTests(unittest.TestCase):
    def test_assign_speakers_by_largest_overlap(self):
        segments = [{"start": 0, "end": 4, "text": "a"}, {"start": 10, "end": 11, "text": "b"}]
        turns = [{"start": 0, "end": 1, "speaker": "SPEAKER_00"},
                 {"start": 1, "end": 4, "speaker": "SPEAKER_01"}]
        merged = pipeline.assign_speakers(segments, turns)
        self.assertEqual([s["speaker"] for s in merged], ["SPEAKER_01", "UNKNOWN"])

    def test_friendly_names_keep_unknown(self):
        merged = [{"speaker": "SPEAKER_03"}, {"speaker": "UNKNOWN"}, {"speaker": "SPEAKER_00"}]
        self.assertEqual([s["speaker"] for s in pipeline.friendly_speaker_names(merged)],
                         ["Speaker 1", "UNKNOWN", "Speaker 2"])

    def test_require_hf_token(self):
        with mock.patch.dict(os.environ, {"HF_TOKEN": ""}):
            with self.assertRaisesRegex(pipeline.PipelineError, "HF_TOKEN"):
                pipeline.require_hf_token()
        with mock.patch.dict(os.environ, {"HF_TOKEN": "abc"}):
            self.assertEqual(pipeline.require_hf_token(), "abc")


class FormatTests(unittest.TestCase):
    def test_format_timestamp(self):
        self.assertEqual(pipeline.format_timestamp(0), "00:00:00")
        self.assertEqual(pipeline.format_timestamp(59.9), "00:00:59")
        self.assertEqual(pipeline.format_timestamp(3725), "01:02:05")

    def test_txt_matches_golden_file(self):
        self.assertEqual(pipeline.format_transcript(CALL), (GOLDEN / "call.txt").read_text())

    def test_md_matches_golden_file(self):
        self.assertEqual(pipeline.format_markdown(CALL, "Weekly sync"),
                         (GOLDEN / "call.md").read_text())

    def test_plain_matches_golden_file(self):
        plain = [{k: v for k, v in s.items() if k != "speaker"} for s in CALL if not s.get("marker")]
        self.assertEqual(pipeline.format_plain_transcript(plain), (GOLDEN / "plain.txt").read_text())

    def test_empty_transcripts(self):
        self.assertEqual(pipeline.format_transcript([]), "\n")
        self.assertEqual(pipeline.format_markdown([], "T"), "# T\n")
        self.assertEqual(pipeline.format_plain_transcript([]), "")

    def test_markdown_puts_each_speaker_in_its_own_paragraph(self):
        md = pipeline.format_markdown(CALL, "T")
        paragraphs = md.split("\n\n")
        self.assertIn("Hi there. Can you hear me?", paragraphs)
        self.assertIn("**Speaker 1** `00:00:03`", paragraphs)
        self.assertFalse(any(line.endswith(" ") for line in md.splitlines()))


def fake_whisper(segments):
    module = types.ModuleType("mlx_whisper")
    module.transcribe = mock.Mock(return_value={"segments": segments})
    return mock.patch.dict(sys.modules, {"mlx_whisper": module}), module


class TranscribeSourceTests(unittest.TestCase):
    """transcribe_source end to end with every slow step stubbed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wav = Path(self.tmp.name) / "prepared.wav"
        self.wav.write_bytes(b"x")
        patcher = mock.patch.object(pipeline, "prepare_audio", return_value=self.wav)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_without_speakers(self):
        patch, whisper = fake_whisper([{"start": 0, "end": 1, "text": " Hi. "},
                                       {"start": 1, "end": 2, "text": "   "}])
        steps = []
        with patch:
            segments = pipeline.transcribe_source("a.wav", model="turbo", identify_speakers=False,
                                                  progress=lambda f, s: steps.append(s))
        self.assertEqual(segments, [{"start": 0, "end": 1, "text": "Hi."}])
        self.assertEqual(whisper.transcribe.call_args.kwargs["path_or_hf_repo"],
                         pipeline.MODEL_REPOS["turbo"])
        self.assertEqual(steps, ["Preparing audio...", "Transcribing with turbo..."])
        self.assertFalse(self.wav.exists(), "the temp WAV is deleted afterwards")

    def test_with_speakers(self):
        patch, _ = fake_whisper([{"start": 0, "end": 1, "text": "Hi."},
                                 {"start": 2, "end": 3, "text": "Yo."}])
        turns = [{"start": 0, "end": 1, "speaker": "SPEAKER_01"},
                 {"start": 2, "end": 3, "speaker": "SPEAKER_00"}]
        with patch, mock.patch.dict(os.environ, {"HF_TOKEN": "x"}), \
                mock.patch.object(pipeline, "diarize", return_value=turns) as diarize:
            segments = pipeline.transcribe_source("a.wav", num_speakers=2)
        diarize.assert_called_once_with(self.wav, 2)
        self.assertEqual([s["speaker"] for s in segments], ["Speaker 1", "Speaker 2"])

    def test_missing_token_fails_before_any_work(self):
        with mock.patch.dict(os.environ, {"HF_TOKEN": ""}):
            with self.assertRaises(pipeline.PipelineError):
                pipeline.transcribe_source("a.wav")
        pipeline.prepare_audio.assert_not_called()

    def test_temp_wav_is_deleted_when_whisper_fails(self):
        module = types.ModuleType("mlx_whisper")
        module.transcribe = mock.Mock(side_effect=RuntimeError("boom"))
        with mock.patch.dict(sys.modules, {"mlx_whisper": module}):
            with self.assertRaises(RuntimeError):
                pipeline.transcribe_source("a.wav", identify_speakers=False)
        self.assertFalse(self.wav.exists())


if __name__ == "__main__":
    unittest.main()
