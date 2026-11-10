"""API tests: the FastAPI app driven through TestClient, with the fake recorder
helper and stubbed transcription (no audio, no Whisper).

Run with:  python -m unittest discover tests
"""

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
import warnings
from unittest import mock

# Starlette suggests a newer HTTP client for TestClient; httpx works fine here.
warnings.filterwarnings("ignore", message="Using `httpx`")
from fastapi.testclient import TestClient  # noqa: E402

from scribeiq.api import create_app  # noqa: E402
from scribeiq.transcripts import TranscriptStore  # noqa: E402

FAKE_RECORDER = Path(__file__).with_name("fake_recorder.py")


def fake_call(me_wav, them_wav, *, my_name, their_count, model, session_path, progress):
    progress(0.5, "Transcribing...")
    return [{"start": 0.0, "end": 2.0, "text": "Hi there.", "speaker": my_name},
            {"start": 2.5, "end": 4.0, "text": "Hello!", "speaker": "Them"},
            {"start": 5.0, "end": 5.0, "text": "", "speaker": "",
             "marker": "recording paused for 10s"},
            {"start": 6.0, "end": 8.0, "text": "Back again.", "speaker": "Them"}]


def fake_file(source, *, model, identify_speakers, num_speakers, progress):
    seg = {"start": 0.0, "end": 3.0, "text": f"From {Path(source).name}."}
    return [{**seg, "speaker": "Speaker 1"}] if identify_speakers else [seg]


def wait_for_job(client, job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


class ApiTestCase(unittest.TestCase):
    call_runner = staticmethod(fake_call)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.recordings, self.transcripts = root / "recordings", root / "transcripts"
        self.trash = root / "Trash"
        self.app = create_app(recorder_bin=FAKE_RECORDER, recordings_dir=self.recordings,
                              trash_dir=self.trash,
                              transcripts_dir=self.transcripts, call_runner=self.call_runner,
                              file_runner=fake_file, allowed_hosts={"testserver"},
                              serve_frontend=False)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def post(self, path, **kwargs):
        return self.client.post(path, **kwargs)


class SessionTests(ApiTestCase):
    def test_record_pause_resume_stop_finish(self):
        r = self.post("/api/session/record", json={"name": "call", "app": "us.zoom.xos"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["state"], "Recording")
        self.assertEqual(r.json()["buttons"], {"record": False, "pause": True,
                                               "stop": True, "finish": False})
        self.assertEqual(self.post("/api/session/pause").json()["state"], "Paused")
        self.assertEqual(self.post("/api/session/resume").json()["state"], "Recording")

        stopped = self.post("/api/session/stop").json()
        self.assertEqual(stopped["state"], "Stopped")
        self.assertEqual([Path(p).name for p in stopped["tracks"]],
                         ["call_me.wav", "call_them.wav"])
        session = json.loads((self.recordings / "call_session.json").read_text())
        self.assertEqual(session["source"], "us.zoom.xos")

        r = self.post("/api/session/finish", json={"my_name": "Bob", "their_count": 1})
        self.assertEqual(r.status_code, 202, r.text)
        job = wait_for_job(self.client, r.json()["id"])
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(job["transcript_id"], "call")

        snap = self.client.get("/api/session").json()
        self.assertEqual(snap["state"], "Done")
        self.assertEqual(snap["transcript_id"], "call")
        for ext in ("json", "txt", "md"):
            self.assertTrue((self.transcripts / f"call.{ext}").exists(), ext)

    def test_invalid_actions_return_409(self):
        for path in ("/api/session/pause", "/api/session/stop", "/api/session/finish"):
            r = self.post(path)
            self.assertEqual(r.status_code, 409, path)
            self.assertIn("idle", r.json()["detail"])
        self.post("/api/session/record", json={"name": "call"})
        r = self.post("/api/session/finish")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"], "Can't finish while recording")
        self.assertEqual(self.post("/api/session/record").status_code, 409)
        self.post("/api/session/stop")

    def test_app_not_running_returns_400_and_stays_idle(self):
        r = self.post("/api/session/record", json={"name": "call", "app": "com.example.gone"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("not running", r.json()["detail"])
        self.assertEqual(self.client.get("/api/session").json()["state"], "Idle")

    def test_system_audio_choice(self):
        self.post("/api/session/record", json={"name": "call", "app": "system"})
        self.post("/api/session/stop")
        session = json.loads((self.recordings / "call_session.json").read_text())
        self.assertEqual(session["source"], "system")

    def test_group_call_without_token_fails_before_processing(self):
        self.post("/api/session/record", json={"name": "call"})
        self.post("/api/session/stop")
        with mock.patch.dict(os.environ, {"HF_TOKEN": ""}):
            r = self.post("/api/session/finish", json={"their_count": 3})
        self.assertEqual(r.status_code, 400)
        self.assertIn("HF_TOKEN", r.json()["detail"])
        self.assertEqual(self.client.get("/api/session").json()["state"], "Stopped")

    def test_unknown_model_is_rejected(self):
        self.post("/api/session/record", json={"name": "call"})
        self.post("/api/session/stop")
        r = self.post("/api/session/finish", json={"model": "tiny"})
        self.assertEqual(r.status_code, 400)

    def test_helper_that_saves_and_exits_leaves_a_finishable_session(self):
        self.post("/api/session/record", json={"name": "call"})
        rec = self.app.state.studio.recorder
        rec._send("stop")  # what SIGTERM does to the real helper: save, then exit
        rec._proc.wait(5)
        snap = self.client.get("/api/session").json()
        self.assertEqual(snap["state"], "Stopped")
        self.assertIn("saved", snap["notice"]["message"])
        self.assertTrue((self.recordings / "call_session.json").exists())
        self.assertTrue(snap["buttons"]["finish"])

    def test_helper_that_dies_returns_to_idle_with_a_notice(self):
        self.post("/api/session/record", json={"name": "call"})
        rec = self.app.state.studio.recorder
        rec._proc.kill()
        rec._proc.wait(5)
        snap = self.client.get("/api/session").json()
        self.assertEqual(snap["state"], "Idle")
        self.assertIn("stopped", snap["notice"]["message"])

    def test_shutdown_saves_an_active_recording(self):
        self.post("/api/session/record", json={"name": "call"})
        self.client.__exit__(None, None, None)
        self.assertTrue((self.recordings / "call_me.wav").exists())
        self.client = TestClient(self.app)  # so tearDown has something to close
        self.client.__enter__()


class FailingCallTests(ApiTestCase):
    @staticmethod
    def call_runner(*args, **kwargs):
        raise RuntimeError("model exploded")

    def test_failed_finish_returns_to_stopped_for_retry(self):
        self.post("/api/session/record", json={"name": "call"})
        self.post("/api/session/stop")
        job = wait_for_job(self.client, self.post("/api/session/finish").json()["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("model exploded", job["error"])
        snap = self.client.get("/api/session").json()
        self.assertEqual(snap["state"], "Stopped")
        self.assertTrue(snap["buttons"]["finish"])


class SlowCallTests(ApiTestCase):
    release = None

    def call_runner(self, *args, **kwargs):
        SlowCallTests.release.wait(5)
        return fake_call(*args, **kwargs)

    def test_only_one_job_at_a_time(self):
        SlowCallTests.release = threading.Event()
        self.post("/api/session/record", json={"name": "call"})
        self.post("/api/session/stop")
        job_id = self.post("/api/session/finish").json()["id"]
        self.assertEqual(self.client.get("/api/session").json()["state"], "Processing")
        r = self.post("/api/files/transcribe", data={"url": "https://example.com/a.mp3",
                                                     "identify_speakers": "false"})
        self.assertEqual(r.status_code, 409)
        SlowCallTests.release.set()
        self.assertEqual(wait_for_job(self.client, job_id)["status"], "done")


class AppsTests(ApiTestCase):
    def test_apps_list_starts_with_system_audio_and_prefers_zoom(self):
        body = self.client.get("/api/apps").json()
        self.assertEqual(body["apps"][0], {"id": "system", "name": "All system audio"})
        self.assertIn({"id": "us.zoom.xos", "name": "zoom.us"}, body["apps"])
        self.assertEqual(body["default"], "us.zoom.xos")


class FileTests(ApiTestCase):
    def test_upload_without_speakers(self):
        r = self.post("/api/files/transcribe",
                      files={"file": ("talk.m4a", b"audio", "audio/mp4")},
                      data={"identify_speakers": "false"})
        self.assertEqual(r.status_code, 202, r.text)
        job = wait_for_job(self.client, r.json()["id"])
        self.assertEqual(job["status"], "done", job)
        transcript = self.client.get(f"/api/transcripts/{job['transcript_id']}").json()
        self.assertEqual(transcript["title"], "talk")
        self.assertFalse(transcript["labeled"])
        self.assertEqual(transcript["segments"][0]["text"], "From talk.m4a.")

    def test_url_with_speakers_and_name(self):
        with mock.patch.dict(os.environ, {"HF_TOKEN": "x"}):
            r = self.post("/api/files/transcribe",
                          data={"url": "https://example.com/ep%201.mp3", "name": "Episode",
                                "num_speakers": "2"})
        job = wait_for_job(self.client, r.json()["id"])
        transcript = self.client.get(f"/api/transcripts/{job['transcript_id']}").json()
        self.assertEqual(transcript["title"], "Episode")
        self.assertEqual(transcript["speakers"], ["Speaker 1"])

    def test_nothing_to_transcribe(self):
        r = self.post("/api/files/transcribe", data={"identify_speakers": "false"})
        self.assertEqual(r.status_code, 400)


class TranscriptTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        store = TranscriptStore(self.transcripts)
        self.record = store.save("Weekly sync", fake_call(None, None, my_name="Bob",
                                 their_count=1, model="turbo", session_path=None,
                                 progress=lambda *a: None), labeled=True, kind="call")

    def test_rename_speakers_rewrites_every_file(self):
        r = self.client.patch("/api/transcripts/Weekly sync/speakers", json={"Them": "Dave"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["speakers"], ["Bob", "Dave"])
        self.assertIn("Dave:", (self.transcripts / "Weekly sync.txt").read_text())
        self.assertIn("**Dave**", (self.transcripts / "Weekly sync.md").read_text())
        reloaded = self.client.get("/api/transcripts/Weekly sync").json()
        self.assertEqual(reloaded["speakers"], ["Bob", "Dave"])
        self.assertEqual(reloaded["segments"][2]["marker"], "recording paused for 10s")

    def test_rename_needs_a_name(self):
        r = self.client.patch("/api/transcripts/Weekly sync/speakers", json={"Them": "  "})
        self.assertEqual(r.status_code, 400)

    def test_list_and_download(self):
        (self.transcripts / "old notes.txt").write_text("[00:00:00] hello\n")
        listing = self.client.get("/api/transcripts").json()
        self.assertEqual({t["id"] for t in listing}, {"Weekly sync", "old notes"})
        legacy = self.client.get("/api/transcripts/old notes").json()
        self.assertTrue(legacy["legacy"])
        r = self.client.get("/api/transcripts/Weekly sync/download?format=md")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.text.startswith("# Weekly sync"))
        self.assertIn("attachment", r.headers["content-disposition"])

    def test_delete_moves_every_file_to_the_trash(self):
        (self.trash).mkdir()
        (self.trash / "Weekly sync.txt").write_text("an older file with the same name")
        r = self.client.delete("/api/transcripts/Weekly sync")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(list(self.transcripts.glob("Weekly sync*")), [])
        self.assertEqual(sorted(p.name for p in self.trash.iterdir()),
                         ["Weekly sync (2).txt", "Weekly sync.json", "Weekly sync.md",
                          "Weekly sync.txt"])
        self.assertEqual(self.client.get("/api/transcripts").json(), [])
        self.assertEqual(self.client.delete("/api/transcripts/Weekly sync").status_code, 404)

    def test_delete_clears_the_finished_session_transcript(self):
        self.post("/api/session/record", json={"name": "call"})
        self.post("/api/session/stop")
        wait_for_job(self.client, self.post("/api/session/finish").json()["id"])
        self.client.delete("/api/transcripts/call")
        self.assertIsNone(self.client.get("/api/session").json()["transcript_id"])

    def test_delete_refuses_unsafe_ids(self):
        r = self.client.delete("/api/transcripts/..%2Fsecret")
        self.assertEqual(r.status_code, 404)

    def test_missing_and_unsafe_ids(self):
        self.assertEqual(self.client.get("/api/transcripts/nope").status_code, 404)
        self.assertEqual(self.client.get("/api/transcripts/..%2F..%2Fetc").status_code, 404)
        r = self.client.get("/api/transcripts/Weekly sync/download?format=json")
        self.assertEqual(r.status_code, 400)


class GuardTests(ApiTestCase):
    def test_other_sites_are_refused(self):
        r = self.post("/api/session/record", headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get("/api/session").json()["state"], "Idle")

    def test_same_origin_is_allowed(self):
        r = self.client.get("/api/session", headers={"Origin": "http://testserver"})
        self.assertEqual(r.status_code, 200)

    def test_unknown_host_is_refused(self):
        r = self.client.get("/api/session", headers={"Host": "attacker.example"})
        self.assertEqual(r.status_code, 403)


class LiveTests(ApiTestCase):
    def test_websocket_pushes_state_and_levels(self):
        self.post("/api/session/record", json={"name": "call"})
        time.sleep(0.2)
        with self.client.websocket_connect("/api/live") as ws:
            msg = ws.receive_json()
        self.assertEqual(msg["state"], "Recording")
        self.assertEqual(msg["levels"], {"me": 0.25, "them": 0.5})
        self.post("/api/session/stop")


if __name__ == "__main__":
    unittest.main()
