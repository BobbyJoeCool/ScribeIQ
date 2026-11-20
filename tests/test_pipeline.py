"""Tests for scribeiq/pipeline.py: audio prep and the transcript formats.
No model is loaded and nothing is downloaded.

Run with:  python -m unittest discover tests
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from scribeiq import pipeline

GOLDEN = Path(__file__).with_name("golden")

# A call with grouped turns, a pause marker, and an hour-plus timestamp.
CALL = [
    {"start": 0.0, "end": 2.0, "text": "Hi there.", "speaker": "Bob"},
    {"start": 2.2, "end": 3.0, "text": "Can you hear me?", "speaker": "Bob"},
    {"start": 3.5, "end": 5.0, "text": "Loud and clear.", "speaker": "Speaker 1"},
    {"start": 65.0, "end": 65.0, "text": "", "speaker": "", "marker": "recording paused for 1m 05s"},
    {"start": 70.0, "end": 72.0, "text": "Back again.", "speaker": "Speaker 1"},
    {"start": 3725.0, "end": 3730.0, "text": "Wrapping up.", "speaker": "Bob"},
]


class PrepareAudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

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


class FormatTests(unittest.TestCase):
    def test_md_matches_golden_file(self):
        self.assertEqual(pipeline.format_markdown(CALL, "Weekly sync"),
                         (GOLDEN / "call.md").read_text())

    def test_markdown_puts_each_speaker_in_its_own_paragraph(self):
        md = pipeline.format_markdown(CALL, "T")
        paragraphs = md.split("\n\n")
        self.assertIn("Hi there. Can you hear me?", paragraphs)
        self.assertIn("**Speaker 1** `00:00:03`", paragraphs)
        self.assertFalse(any(line.endswith(" ") for line in md.splitlines()))


if __name__ == "__main__":
    unittest.main()
