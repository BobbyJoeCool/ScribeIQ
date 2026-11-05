"""Tests for the parts of ScribeIQ that don't need real audio or models.

Run with:  python -m unittest discover tests
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

from scribeiq import calls, pipeline
from scribeiq.recorder import (BUTTONS, TRANSITIONS, Recorder, RecorderError, State,
                               list_apps, next_state, unique_session_name)

FAKE_RECORDER = Path(__file__).with_name("fake_recorder.py")


def seg(start, text, speaker=None, end=None):
    s = {"start": start, "end": end if end is not None else start + 1, "text": text}
    if speaker:
        s["speaker"] = speaker
    return s


class MergeTests(unittest.TestCase):
    def test_tracks_interleave_by_start_time(self):
        mine = calls.label_my_segments([seg(0, "hi"), seg(5, "great")], "Bob")
        theirs = calls.label_their_segments([seg(2, "hello"), seg(7, "bye")], None)
        merged = calls.merge_tracks(mine, theirs)
        self.assertEqual([s["text"] for s in merged], ["hi", "hello", "great", "bye"])
        self.assertEqual([s["speaker"] for s in merged], ["Bob", "Them", "Bob", "Them"])

    def test_overlapping_speech_keeps_both_sides(self):
        merged = calls.merge_tracks([seg(3, "me", "Me")], [seg(3, "them", "Them")])
        self.assertEqual([s["text"] for s in merged], ["me", "them"])

    def test_diarized_speakers_get_friendly_names_in_order(self):
        segments = [seg(0, "a", end=2), seg(3, "b", end=5), seg(6, "c", end=8)]
        turns = [{"start": 0, "end": 2.5, "speaker": "SPEAKER_01"},
                 {"start": 2.5, "end": 5.5, "speaker": "SPEAKER_00"},
                 {"start": 5.5, "end": 9, "speaker": "SPEAKER_01"}]
        labeled = calls.label_their_segments(segments, turns)
        self.assertEqual([s["speaker"] for s in labeled], ["Speaker 1", "Speaker 2", "Speaker 1"])

    def test_pause_markers_land_in_order_and_render(self):
        markers = calls.pause_markers([{"at": 4.0, "length": 65}])
        merged = calls.merge_tracks([seg(0, "before", "Me")], [seg(5, "after", "Them")], markers)
        text = pipeline.format_transcript(merged)
        self.assertIn("(recording paused for 1m 05s)", text)
        self.assertLess(text.index("before"), text.index("paused"))
        self.assertLess(text.index("paused"), text.index("after"))

    def test_rename_speakers(self):
        mapping = calls.parse_rename_text("Speaker 1 = Dave, Them=Sarah\nbad line")
        self.assertEqual(mapping, {"Speaker 1": "Dave", "Them": "Sarah"})
        renamed = calls.rename_speakers([seg(0, "x", "Speaker 1"), seg(1, "y", "Me")], mapping)
        self.assertEqual([s["speaker"] for s in renamed], ["Dave", "Me"])

    def test_markdown_groups_consecutive_lines(self):
        md = pipeline.format_markdown([seg(0, "one", "Me"), seg(1, "two", "Me"),
                                       seg(2, "three", "Them")], "Call")
        self.assertTrue(md.startswith("# Call"))
        self.assertEqual(md.count("**Me**"), 1)
        self.assertIn("one two", md)


class StateMachineTests(unittest.TestCase):
    def test_happy_path(self):
        state = State.IDLE
        for action in ["record", "pause", "resume", "stop", "finish", "done", "record"]:
            state = next_state(state, action)
        self.assertEqual(state, State.RECORDING)

    def test_invalid_actions_are_rejected(self):
        with self.assertRaises(ValueError):
            next_state(State.IDLE, "finish")
        with self.assertRaises(ValueError):
            next_state(State.PROCESSING, "record")

    def test_failed_processing_allows_retry(self):
        self.assertEqual(next_state(State.PROCESSING, "failed"), State.STOPPED)
        self.assertTrue(BUTTONS[State.STOPPED]["finish"])

    def test_buttons_match_transitions(self):
        """A button is enabled exactly when its action is valid in that state."""
        for state, buttons in BUTTONS.items():
            for button, enabled in buttons.items():
                actions = ["pause", "resume"] if button == "pause" else [button]
                valid = any((state, a) in TRANSITIONS for a in actions)
                self.assertEqual(enabled, valid, f"{button} in {state}")


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_session_with_pause(self):
        rec = Recorder(FAKE_RECORDER, self.dir, "call")
        rec.start()
        time.sleep(0.2)
        self.assertEqual(rec.levels, (0.25, 0.5))
        rec.pause()
        time.sleep(0.3)
        rec.resume()
        me, them = rec.stop()
        self.assertEqual((me.name, them.name), ("call_me.wav", "call_them.wav"))
        session = json.loads(rec.session_path.read_text())
        self.assertEqual(len(session["pauses"]), 1)
        self.assertGreaterEqual(session["pauses"][0]["length"], 0.3)

    def test_stop_while_paused_closes_the_pause(self):
        rec = Recorder(FAKE_RECORDER, self.dir, "call")
        rec.start()
        rec.pause()
        rec.stop()
        session = json.loads(rec.session_path.read_text())
        self.assertGreaterEqual(session["pauses"][0]["length"], 0)

    def test_helper_error_is_reported(self):
        with self.assertRaisesRegex(RecorderError, "Screen Recording"):
            Recorder(FAKE_RECORDER, self.dir, "fail").start()

    def test_missing_helper_is_reported(self):
        with self.assertRaisesRegex(RecorderError, "build.sh"):
            Recorder(self.dir / "nope", self.dir, "call").start()

    def test_list_apps(self):
        self.assertEqual(list_apps(FAKE_RECORDER),
                         [("Safari", "com.apple.Safari"), ("zoom.us", "us.zoom.xos")])

    def test_list_apps_without_helper(self):
        with self.assertRaisesRegex(RecorderError, "build.sh"):
            list_apps(self.dir / "nope")

    def test_session_records_the_chosen_app(self):
        rec = Recorder(FAKE_RECORDER, self.dir, "call", app="us.zoom.xos")
        rec.start()
        rec.stop()
        self.assertEqual(json.loads(rec.session_path.read_text())["source"], "us.zoom.xos")

    def test_no_app_means_system_audio(self):
        rec = Recorder(FAKE_RECORDER, self.dir, "call")
        rec.start()
        rec.stop()
        self.assertEqual(json.loads(rec.session_path.read_text())["source"], "system")

    def test_app_not_running_is_reported_without_files(self):
        with self.assertRaisesRegex(RecorderError, "not running"):
            Recorder(FAKE_RECORDER, self.dir, "call", app="com.example.gone").start()
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_session_file_is_written_once(self):
        """Calling stop() again (as the exit hook would) never rewrites the session,
        even after its folder is gone."""
        rec = Recorder(FAKE_RECORDER, self.dir, "call")
        rec.start()
        rec.stop()
        rec.session_path.unlink()
        rec.stop()
        self.assertFalse(rec.session_path.exists())

    def test_names_never_overwrite(self):
        (self.dir / "call_me.wav").write_bytes(b"")
        self.assertEqual(unique_session_name(self.dir, "call"), "call (2)")

    def test_unique_transcript_paths(self):
        first = pipeline.unique_path(self.dir, "t", ".txt")
        first.write_text("x")
        self.assertEqual(pipeline.unique_path(self.dir, "t", ".txt").name, "t (2).txt")


if __name__ == "__main__":
    unittest.main()
