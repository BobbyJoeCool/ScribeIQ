"""Tests for transcribe.py (the command line), with the pipeline stubbed.

Run with:  python -m unittest discover tests
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import transcribe
from scribeiq import calls, pipeline


def run_cli(*argv):
    """Run main() with argv; returns (exit code or None, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    code = None
    with mock.patch.object(sys, "argv", ["transcribe.py", *argv]), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            transcribe.main()
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def fake_source(source, *, model, identify_speakers, num_speakers, progress):
    progress(0.5, "Transcribing...")
    seg = {"start": 0.0, "end": 2.0, "text": f"From {Path(source).name}."}
    return [{**seg, "speaker": "Speaker 1"}] if identify_speakers else [seg]


def fake_call(me, them, *, my_name, their_count, model, session_path, progress):
    return [{"start": 0.0, "end": 1.0, "text": "Hi.", "speaker": my_name},
            {"start": 1.0, "end": 2.0, "text": "Hey.", "speaker": "Them"}]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / "out"

    def test_file_mode_writes_txt_md_and_json(self):
        with mock.patch.object(pipeline, "transcribe_source", side_effect=fake_source) as run:
            code, stdout, _ = run_cli("talk.m4a", "--no-diarize", "--model", "turbo",
                                      "--out-dir", str(self.out))
        self.assertIsNone(code)
        self.assertEqual(run.call_args.kwargs, {"model": "turbo", "identify_speakers": False,
                                                "num_speakers": None,
                                                "progress": mock.ANY})
        self.assertIn("Transcribing...", stdout)
        self.assertIn(f"Wrote {self.out / 'talk.txt'}", stdout)
        self.assertIn(f"Wrote {self.out / 'talk.md'}", stdout)
        record = json.loads((self.out / "talk.json").read_text())
        self.assertEqual((record["kind"], record["labeled"], record["source"]),
                         ("file", False, None))
        self.assertEqual((self.out / "talk.txt").read_text(), "[00:00:00] From talk.m4a.\n")

    def test_file_mode_with_speakers_url_and_name(self):
        url = "https://example.com/ep%201.mp3"
        with mock.patch.object(pipeline, "transcribe_source", side_effect=fake_source) as run:
            code, _, _ = run_cli(url, "--speakers", "3", "--out", "Episode",
                                 "--out-dir", str(self.out))
        self.assertIsNone(code)
        self.assertEqual(run.call_args.kwargs["num_speakers"], 3)
        self.assertTrue(run.call_args.kwargs["identify_speakers"])
        record = json.loads((self.out / "Episode.json").read_text())
        self.assertEqual(record["source"], url)
        self.assertTrue(record["labeled"])

    def test_default_name_from_url(self):
        with mock.patch.object(pipeline, "transcribe_source", side_effect=fake_source):
            run_cli("https://example.com/ep%201.mp3", "--out-dir", str(self.out))
        self.assertTrue((self.out / "ep 1.txt").exists())

    def test_never_overwrites(self):
        with mock.patch.object(pipeline, "transcribe_source", side_effect=fake_source):
            run_cli("talk.m4a", "--out-dir", str(self.out))
            run_cli("talk.m4a", "--out-dir", str(self.out))
        self.assertTrue((self.out / "talk (2).txt").exists())

    def test_call_mode_reads_the_session_file(self):
        rec = self.root / "rec"
        rec.mkdir()
        me, them = rec / "call_me.wav", rec / "call_them.wav"
        (rec / "call_session.json").write_text(json.dumps({"duration": 42.5,
                                                           "source": "us.zoom.xos"}))
        with mock.patch.object(calls, "transcribe_call", side_effect=fake_call) as run:
            code, _, _ = run_cli("--me", str(me), "--them", str(them), "--their-count", "2",
                                 "--my-name", "Bob", "--out-dir", str(self.out))
        self.assertIsNone(code)
        kwargs = run.call_args.kwargs
        self.assertEqual((kwargs["my_name"], kwargs["their_count"], kwargs["session_path"]),
                         ("Bob", 2, rec / "call_session.json"))
        record = json.loads((self.out / "call.json").read_text())
        self.assertEqual((record["kind"], record["duration"], record["source"]),
                         ("call", 42.5, "us.zoom.xos"))
        self.assertIn("[00:00:00] Bob:", (self.out / "call.txt").read_text())

    def test_call_mode_needs_both_tracks(self):
        code, _, err = run_cli("--me", "a_me.wav")
        self.assertEqual(code, 2)
        self.assertIn("needs both --me and --them", err)

    def test_needs_some_input(self):
        code, _, err = run_cli()
        self.assertEqual(code, 2)
        self.assertIn("give an audio file/URL", err)

    def test_unknown_model_is_rejected(self):
        code, _, err = run_cli("a.wav", "--model", "tiny")
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)

    def test_pipeline_error_is_a_clean_exit(self):
        with mock.patch.object(pipeline, "transcribe_source",
                               side_effect=pipeline.PipelineError("File not found: a.wav")):
            code, _, _ = run_cli("a.wav", "--out-dir", str(self.out))
        self.assertEqual(code, "Error: File not found: a.wav")
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))


if __name__ == "__main__":
    unittest.main()
