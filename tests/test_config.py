"""Tests for scribeiq/config.py: .env loading and defaults.

Run with:  python -m unittest discover tests
"""

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scribeiq import config

KEYS = ("SCRIBEIQ_HOME", "SCRIBEIQ_RECORDER", "SCRIBEIQ_MY_NAME", "SCRIBEIQ_LANGUAGE")


class DotenvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = Path(self.tmp.name) / ".env"

    def load(self, text, environ=None):
        self.env.write_text(text)
        with mock.patch.dict(os.environ, environ or {}, clear=True):
            config.load_dotenv(self.env)
            return dict(os.environ)

    def test_reads_keys_and_strips_quotes(self):
        env = self.load("# comment\n\nHF_TOKEN=abc\nNAME = 'Bob B'\nQ=\"x=y\"\nnot a line\n")
        self.assertEqual(env, {"HF_TOKEN": "abc", "NAME": "Bob B", "Q": "x=y"})

    def test_real_environment_wins(self):
        self.assertEqual(self.load("HF_TOKEN=file", {"HF_TOKEN": "shell"})["HF_TOKEN"], "shell")

    def test_missing_file_is_fine(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            config.load_dotenv(Path(self.tmp.name) / "nope.env")
            self.assertEqual(dict(os.environ), {})


class DefaultsTests(unittest.TestCase):
    def reload(self, **env):
        """Re-import config with only `env` set (and no .env file), then restore."""
        clean = {k: v for k, v in os.environ.items() if k not in KEYS}
        with mock.patch.dict(os.environ, {**clean, **env}, clear=True), \
                mock.patch.object(Path, "exists", lambda self: False if self.name == ".env"
                                  else os.path.exists(self)):
            module = importlib.reload(config)
            values = {k: getattr(module, k) for k in ("DATA_DIR", "RECORDINGS_DIR",
                                                      "TRANSCRIPTS_DIR", "RECORDER_BIN",
                                                      "MY_NAME", "LANGUAGE")}
        importlib.reload(config)
        return values

    def test_defaults(self):
        v = self.reload()
        self.assertEqual(v["DATA_DIR"], config.PROJECT_ROOT / "Outputs")
        self.assertEqual(v["RECORDINGS_DIR"], v["DATA_DIR"] / "recordings")
        self.assertEqual(v["TRANSCRIPTS_DIR"], v["DATA_DIR"] / "transcripts")
        self.assertEqual(v["RECORDER_BIN"],
                         config.PROJECT_ROOT / "recorder" / "bin" / "scribe-recorder")
        self.assertEqual((v["MY_NAME"], v["LANGUAGE"]), ("Me", "en"))

    def test_overrides(self):
        v = self.reload(SCRIBEIQ_HOME="~/Calls", SCRIBEIQ_RECORDER="/opt/rec",
                        SCRIBEIQ_MY_NAME="Bob", SCRIBEIQ_LANGUAGE="  ES ")
        self.assertEqual(v["DATA_DIR"], Path.home() / "Calls")
        self.assertEqual(v["TRANSCRIPTS_DIR"], Path.home() / "Calls" / "transcripts")
        self.assertEqual(v["RECORDER_BIN"], Path("/opt/rec"))
        self.assertEqual((v["MY_NAME"], v["LANGUAGE"]), ("Bob", "es"))


if __name__ == "__main__":
    unittest.main()
