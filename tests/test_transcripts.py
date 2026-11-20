"""Tests for scribeiq/transcripts.py: the saved-transcript store.

Run with:  python -m unittest discover tests
"""

import json
import tempfile
import unittest
from pathlib import Path

from scribeiq.transcripts import (TranscriptNotFound, TranscriptStore, render, speakers,
                                  summary)


def call_segments():
    return [{"start": 0.0, "end": 2.0, "text": "Hi there.", "speaker": "Bob"},
            {"start": 2.5, "end": 4.0, "text": "Hello!", "speaker": "Speaker 1"},
            {"start": 5.0, "end": 5.0, "text": "", "speaker": "",
             "marker": "recording paused for 10s"},
            {"start": 6.0, "end": 8.0, "text": "Back again.", "speaker": "Speaker 2"},
            {"start": 9.0, "end": 9.5, "text": "Me too.", "speaker": "Speaker 1"}]


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.folder = self.root / "saved"
        self.trash = self.root / "Trash"
        self.store = TranscriptStore(self.folder)

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, title="Weekly sync", **kwargs):
        kwargs.setdefault("labeled", True)
        kwargs.setdefault("kind", "call")
        return self.store.save(title, call_segments(), **kwargs)


class SaveTests(StoreTestCase):
    def test_save_writes_json_txt_and_md(self):
        record = self.save(duration=12.345, source="us.zoom.xos")
        self.assertEqual(record["id"], "Weekly sync")
        self.assertEqual(record["duration"], 12.35)
        self.assertEqual(record["speakers"], ["Bob", "Speaker 1", "Speaker 2"])
        stored = json.loads((self.folder / "Weekly sync.json").read_text())
        self.assertNotIn("speakers", stored)  # derived, never stored
        self.assertEqual(stored["source"], "us.zoom.xos")
        self.assertIn("[00:00:00] Bob:", (self.folder / "Weekly sync.txt").read_text())
        self.assertTrue((self.folder / "Weekly sync.md").read_text().startswith("# Weekly sync"))

    def test_duration_defaults_to_last_segment_end(self):
        self.assertEqual(self.save()["duration"], 9.5)

    def test_duration_of_empty_transcript_is_zero(self):
        record = self.store.save("Silent", [], labeled=True, kind="call")
        self.assertEqual(record["duration"], 0.0)
        self.assertEqual(record["speakers"], [])

    def test_names_never_collide(self):
        ids = [self.save()["id"] for _ in range(3)]
        self.assertEqual(ids, ["Weekly sync", "Weekly sync (2)", "Weekly sync (3)"])

    def test_name_taken_by_any_one_extension_is_skipped(self):
        self.folder.mkdir()
        (self.folder / "Weekly sync.md").write_text("someone else's notes")
        self.assertEqual(self.save()["id"], "Weekly sync (2)")

    def test_unsafe_titles_become_plain_names(self):
        self.assertEqual(self.save("../etc/passwd")["id"], "-etc-passwd")
        self.assertEqual(self.save("   ")["id"], "transcript")
        self.assertEqual(self.save("...hidden")["id"], "hidden")
        self.assertEqual(sorted(p.parent for p in self.folder.iterdir()), [self.folder] * 9)

    def test_unlabeled_transcript_renders_plain_lines(self):
        segments = [{"start": 0.0, "end": 1.0, "text": "One."},
                    {"start": 61.0, "end": 62.0, "text": "Two."}]
        record = self.store.save("Talk", segments, labeled=False, kind="file")
        self.assertEqual((self.folder / "Talk.txt").read_text(),
                         "[00:00:00] One.\n[00:01:01] Two.\n")
        self.assertEqual((self.folder / "Talk.md").read_text(),
                         "# Talk\n\n[00:00:00] One.\n[00:01:01] Two.\n")
        self.assertEqual(record["speakers"], [])


class RenameTests(StoreTestCase):
    def test_rename_rewrites_every_file(self):
        self.save()
        record = self.store.rename_speakers("Weekly sync", {"Speaker 1": "Dave"})
        self.assertEqual(record["speakers"], ["Bob", "Dave", "Speaker 2"])
        self.assertIn("Dave:", (self.folder / "Weekly sync.txt").read_text())
        self.assertIn("**Dave**", (self.folder / "Weekly sync.md").read_text())
        self.assertEqual(self.store.load("Weekly sync")["speakers"], ["Bob", "Dave", "Speaker 2"])

    def test_names_are_trimmed_and_blank_or_unchanged_ones_ignored(self):
        self.save()
        record = self.store.rename_speakers("Weekly sync", {"Speaker 1": "  Dave  ",
                                                            "Speaker 2": "   ",
                                                            "Bob": "Bob"})
        self.assertEqual(record["speakers"], ["Bob", "Dave", "Speaker 2"])

    def test_nothing_to_rename_is_an_error(self):
        self.save()
        with self.assertRaisesRegex(ValueError, "at least one"):
            self.store.rename_speakers("Weekly sync", {"Speaker 1": ""})
        with self.assertRaisesRegex(ValueError, "at least one"):
            self.store.rename_speakers("Weekly sync", {"Speaker 1": 42})

    def test_unlabeled_and_legacy_transcripts_cannot_be_renamed(self):
        self.store.save("Talk", [{"start": 0, "end": 1, "text": "x"}], labeled=False, kind="file")
        with self.assertRaisesRegex(ValueError, "no speaker names"):
            self.store.rename_speakers("Talk", {"Speaker 1": "Dave"})
        (self.folder / "old.txt").write_text("[00:00:00] hello\n")
        with self.assertRaisesRegex(ValueError, "no speaker names"):
            self.store.rename_speakers("old", {"Speaker 1": "Dave"})

    def test_renaming_a_missing_transcript(self):
        with self.assertRaises(TranscriptNotFound):
            self.store.rename_speakers("nope", {"Speaker 1": "Dave"})


class ReadTests(StoreTestCase):
    def test_list_is_newest_first(self):
        self.folder.mkdir()
        for name, created in (("b", "2026-01-02T00:00:00"), ("a", "2026-01-01T00:00:00"),
                              ("c", "2026-01-03T00:00:00")):
            (self.folder / f"{name}.json").write_text(json.dumps(
                {"id": name, "title": name, "created": created, "labeled": False,
                 "segments": []}))
        self.assertEqual([t["id"] for t in self.store.list()], ["c", "b", "a"])

    def test_list_of_missing_folder_is_empty(self):
        self.assertEqual(self.store.list(), [])

    def test_list_skips_damaged_json_but_keeps_its_text(self):
        self.save()
        self.folder.joinpath("broken.json").write_text("{not json")
        self.folder.joinpath("partial.json").write_text(json.dumps({"title": "no id"}))
        ids = {t["id"] for t in self.store.list()}
        self.assertEqual(ids, {"Weekly sync"})

    def test_list_includes_legacy_text_files(self):
        self.save()
        (self.folder / "old notes.txt").write_text("[00:00:00] hello\n")
        legacy = next(t for t in self.store.list() if t["id"] == "old notes")
        self.assertTrue(legacy["legacy"])
        self.assertEqual(legacy["speakers"], [])
        self.assertIsNone(legacy["kind"])

    def test_load_uses_file_name_as_id(self):
        self.save()
        (self.folder / "Weekly sync.json").rename(self.folder / "Renamed.json")
        self.assertEqual(self.store.load("Renamed")["id"], "Renamed")

    def test_load_legacy_text(self):
        self.folder.mkdir()
        (self.folder / "old.txt").write_text("hello\n")
        record = self.store.load("old")
        self.assertTrue(record["legacy"])
        self.assertEqual(record["text"], "hello\n")

    def test_load_missing_raises(self):
        with self.assertRaises(TranscriptNotFound):
            self.store.load("nope")

    def test_load_corrupt_json_raises_a_clear_error(self):
        self.folder.mkdir()
        (self.folder / "bad.json").write_text("{not json")
        with self.assertRaises(ValueError):
            self.store.load("bad")

    def test_unsafe_ids_are_not_found(self):
        self.save()
        for bad in ("", ".", "..", "../Weekly sync", "sub/Weekly sync", ".hidden"):
            with self.subTest(bad=bad), self.assertRaises(TranscriptNotFound):
                self.store.load(bad)

    def test_file_path_for_download(self):
        self.save()
        self.assertEqual(self.store.file_path("Weekly sync", "md"), self.folder / "Weekly sync.md")
        with self.assertRaisesRegex(ValueError, "txt, md"):
            self.store.file_path("Weekly sync", "json")
        with self.assertRaises(TranscriptNotFound):
            self.store.file_path("nope", "txt")


class DeleteTests(StoreTestCase):
    def test_delete_moves_all_files_to_trash(self):
        self.save()
        moved = self.store.delete("Weekly sync", trash=self.trash)
        self.assertEqual(sorted(p.name for p in moved),
                         ["Weekly sync.json", "Weekly sync.md", "Weekly sync.txt"])
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_delete_never_overwrites_trash(self):
        self.save()
        self.trash.mkdir()
        (self.trash / "Weekly sync.json").write_text("older")
        (self.trash / "Weekly sync (2).json").write_text("older still")
        self.store.delete("Weekly sync", trash=self.trash)
        self.assertEqual((self.trash / "Weekly sync.json").read_text(), "older")
        self.assertTrue((self.trash / "Weekly sync (3).json").exists())

    def test_delete_legacy_text_only(self):
        self.folder.mkdir()
        (self.folder / "old.txt").write_text("x")
        self.assertEqual([p.name for p in self.store.delete("old", trash=self.trash)], ["old.txt"])

    def test_delete_missing_raises(self):
        with self.assertRaises(TranscriptNotFound):
            self.store.delete("nope", trash=self.trash)
        self.assertFalse(self.trash.exists())


class HelperTests(unittest.TestCase):
    def test_speakers_in_order_of_first_appearance_without_markers(self):
        self.assertEqual(speakers({"segments": call_segments()}), ["Bob", "Speaker 1", "Speaker 2"])
        self.assertEqual(speakers({}), [])

    def test_summary_fields(self):
        record = {"id": "x", "title": "X", "created": "2026-01-01T00:00:00",
                  "segments": call_segments()}
        self.assertEqual(summary(record), {"id": "x", "title": "X",
                                           "created": "2026-01-01T00:00:00", "kind": None,
                                           "duration": None,
                                           "speakers": ["Bob", "Speaker 1", "Speaker 2"],
                                           "legacy": False})

    def test_render_labeled(self):
        text, md = render({"title": "T", "labeled": True, "segments": call_segments()})
        self.assertIn("(recording paused for 10s)", text)
        self.assertTrue(md.startswith("# T\n"))


if __name__ == "__main__":
    unittest.main()
