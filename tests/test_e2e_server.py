"""The E2E server's test mode is off unless SCRIBEIQ_E2E=1 is set, and when on
it wires in only fakes.

Run with:  python -m unittest discover tests
"""

import os
import tempfile
import time
import unittest
import warnings
from pathlib import Path
from unittest import mock

warnings.filterwarnings("ignore", message="Using `httpx`")
from fastapi.testclient import TestClient  # noqa: E402

from tests import e2e_server  # noqa: E402


class EnableTests(unittest.TestCase):
    def test_refuses_without_the_env_var(self):
        for env in ({}, {"SCRIBEIQ_E2E": ""}, {"SCRIBEIQ_E2E": "0"}, {"SCRIBEIQ_E2E": "true"}):
            with self.subTest(env=env), mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(SystemExit) as ctx:
                    e2e_server.main([])
                self.assertIn("SCRIBEIQ_E2E=1", str(ctx.exception.code))

    def test_allows_with_the_env_var(self):
        e2e_server.require_enabled({"SCRIBEIQ_E2E": "1"})

    def test_real_app_never_uses_the_fakes(self):
        from scribeiq import api, calls, pipeline
        app = api.create_app(serve_frontend=False)
        self.assertNotEqual(app.state.studio.recorder_bin, e2e_server.FAKE_RECORDER)
        self.assertIsNot(calls.transcribe_call, e2e_server.stub_call)
        self.assertIsNot(pipeline.transcribe_source, e2e_server.stub_file)


class StubAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {})
        env.start()
        self.addCleanup(env.stop)
        delay = mock.patch.object(e2e_server, "STEP_DELAY", 0)
        delay.start()
        self.addCleanup(delay.stop)
        self.client = TestClient(e2e_server.build_app(Path(self.tmp.name)),
                                 base_url="http://127.0.0.1")

    def wait(self, job_id):
        for _ in range(250):
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] != "running":
                return job
            time.sleep(0.02)
        self.fail("job did not finish")

    def test_seeded_history(self):
        titles = [t["title"] for t in self.client.get("/api/transcripts").json()]
        self.assertEqual(sorted(titles), ["Seeded lecture", "Seeded team call"])

    def test_app_list_offers_one_that_is_gone(self):
        apps = {a["id"] for a in self.client.get("/api/apps").json()["apps"]}
        self.assertIn("com.example.gone", apps)
        r = self.client.post("/api/session/record", json={"name": "x", "app": "com.example.gone"})
        self.assertEqual(r.status_code, 400)

    def test_call_with_pause_and_two_speakers(self):
        self.client.post("/api/session/record", json={"name": "call", "app": "us.zoom.xos"})
        self.client.post("/api/session/pause")
        self.client.post("/api/session/resume")
        self.client.post("/api/session/stop")
        job = self.client.post("/api/session/finish",
                               json={"my_name": "Bob", "their_count": 2}).json()
        self.assertEqual(self.wait(job["id"])["status"], "done")
        t = self.client.get("/api/transcripts/call").json()
        self.assertEqual(t["speakers"], ["Bob", "Speaker 1", "Speaker 2"])
        self.assertTrue(any(s.get("marker", "").startswith("recording paused") for s in t["segments"]))

    def test_bad_file_fails_like_unreadable_audio(self):
        r = self.client.post("/api/files/transcribe",
                             files={"file": ("bad.m4a", b"x", "audio/mp4")},
                             data={"identify_speakers": "false"})
        job = self.wait(r.json()["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("Couldn't read bad.m4a as audio", job["error"])


class FakeTokenTests(unittest.TestCase):
    """The test server uses its own fake HF_TOKEN, even when .env leaves it blank
    (#6) or holds a real one."""

    def test_replaces_a_blank_or_real_token(self):
        for value in ("", "hf_real_token"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp, \
                    mock.patch.dict(os.environ, {"HF_TOKEN": value}), \
                    mock.patch.object(e2e_server, "STEP_DELAY", 0):
                client = TestClient(e2e_server.build_app(Path(tmp)), base_url="http://127.0.0.1")
                self.assertEqual(os.environ["HF_TOKEN"], e2e_server.FAKE_HF_TOKEN)
                r = client.post("/api/files/transcribe",
                                files={"file": ("talk.m4a", b"x", "audio/mp4")},
                                data={"identify_speakers": "true"})
                self.assertEqual(r.status_code, 202, r.text)


if __name__ == "__main__":
    unittest.main()
