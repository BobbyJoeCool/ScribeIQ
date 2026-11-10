"""Local HTTP API for the ScribeIQ UI. Wraps the scribeiq package; holds one
recording session and at most one transcription job at a time.

Serve it on 127.0.0.1 only (app.py does). Requests from other web pages are
refused: see OriginGuard.
"""

import asyncio
import os
import shutil
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from . import calls, config, pipeline
from .recorder import (BUTTONS, SYSTEM_AUDIO, Recorder, RecorderError, State,
                       default_session_name, list_apps, next_state)
from .transcripts import TRASH, TranscriptNotFound, TranscriptStore

LIVE_INTERVAL = 0.2  # seconds between WebSocket updates
FRONTEND_DIST = config.PROJECT_ROOT / "frontend" / "dist"
SYSTEM_AUDIO_LABEL = "All system audio"
PREFERRED_APP = "us.zoom.xos"


class Conflict(Exception):
    """An action that isn't valid right now (HTTP 409)."""


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@dataclass
class Job:
    kind: str                       # "call" or "file"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "running"         # running | done | failed
    progress: float = 0.0
    step: str = "Starting..."
    error: str | None = None
    transcript_id: str | None = None

    def report(self, fraction: float, step: str) -> None:
        self.progress, self.step = round(fraction, 3), step

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Server-side state
# ---------------------------------------------------------------------------

class Studio:
    """Everything the API remembers between requests."""

    def __init__(self, recorder_bin: Path, recordings_dir: Path, store: TranscriptStore):
        self.recorder_bin = Path(recorder_bin)
        self.recordings_dir = Path(recordings_dir)
        self.store = store
        self.lock = threading.Lock()
        self.state = State.IDLE
        self.recorder: Recorder | None = None
        self.tracks: tuple | None = None
        self.duration = 0.0
        self.transcript_id: str | None = None
        self.jobs: dict[str, Job] = {}
        self.job: Job | None = None     # the latest job, for the live feed
        self.notice: dict | None = None  # {"id": n, "message": str} for errors found later
        self._notice_count = 0

    # ---- session ---------------------------------------------------------
    def _act(self, action: str) -> State:
        try:
            return next_state(self.state, action)
        except ValueError as e:
            raise Conflict(str(e)) from None

    def record(self, name: str, app: str | None) -> dict:
        with self.lock:
            after = self._act("record")
            rec = Recorder(self.recorder_bin, self.recordings_dir,
                           (name or "").strip() or default_session_name(),
                           app=None if app in (None, "", SYSTEM_AUDIO) else app)
            rec.start()  # RecorderError -> 400, state unchanged
            self.recorder, self.tracks, self.duration = rec, None, 0.0
            self.transcript_id = None
            self.state = after
            return self.snapshot()

    def pause(self) -> dict:
        with self.lock:
            self.state = self._act("pause")
            self.recorder.pause()
            return self.snapshot()

    def resume(self) -> dict:
        with self.lock:
            self.state = self._act("resume")
            self.recorder.resume()
            return self.snapshot()

    def stop(self) -> dict:
        with self.lock:
            after = self._act("stop")
            self.duration = self.recorder.elapsed
            try:
                self.tracks = self.recorder.stop()
            except RecorderError:
                self.state = State.IDLE
                raise
            self.state = after
            return self.snapshot()

    def finish(self, my_name: str, their_count: int, model: str, runner) -> Job:
        with self.lock:
            if self.job_running:
                raise Conflict("Already transcribing. Wait for it to finish.")
            self.state = self._act("finish")
            job = self._new_job("call")
            rec, (me_wav, them_wav) = self.recorder, self.tracks
        threading.Thread(target=self._run_call, daemon=True,
                         args=(job, runner, rec, me_wav, them_wav,
                               my_name.strip() or config.MY_NAME, their_count, model)).start()
        return job

    def _run_call(self, job, runner, rec, me_wav, them_wav, my_name, their_count, model):
        try:
            segments = runner(me_wav, them_wav, my_name=my_name, their_count=their_count,
                              model=model, session_path=rec.session_path,
                              progress=job.report)
            record = self.store.save(rec.name, segments, labeled=True, kind="call",
                                     duration=self.duration,
                                     source=rec.app or SYSTEM_AUDIO)
        except Exception as e:  # noqa: BLE001 - report every failure to the UI
            with self.lock:
                self.state = next_state(self.state, "failed")
            self._fail(job, e)
            return
        with self.lock:
            self.state = next_state(self.state, "done")
            self.transcript_id = record["id"]
        job.report(1.0, "Done")
        job.transcript_id, job.status = record["id"], "done"

    # ---- file jobs -------------------------------------------------------
    def transcribe_file(self, source: str, title: str, identify_speakers: bool,
                        num_speakers: int | None, model: str, runner,
                        cleanup: Path | None = None) -> Job:
        with self.lock:
            if self.job_running:
                if cleanup:
                    shutil.rmtree(cleanup, ignore_errors=True)
                raise Conflict("Already transcribing. Wait for it to finish.")
            job = self._new_job("file")
        threading.Thread(target=self._run_file, daemon=True,
                         args=(job, runner, source, title, identify_speakers, num_speakers,
                               model, cleanup)).start()
        return job

    def _run_file(self, job, runner, source, title, identify_speakers, num_speakers, model,
                  cleanup):
        try:
            segments = runner(source, model=model, identify_speakers=identify_speakers,
                              num_speakers=num_speakers, progress=job.report)
            record = self.store.save(title, segments, labeled=identify_speakers, kind="file",
                                     source=source if pipeline.is_url(source) else None)
        except Exception as e:  # noqa: BLE001
            self._fail(job, e)
            return
        finally:
            if cleanup:
                shutil.rmtree(cleanup, ignore_errors=True)
        job.report(1.0, "Done")
        job.transcript_id, job.status = record["id"], "done"

    # ---- helpers ---------------------------------------------------------
    @property
    def job_running(self) -> bool:
        return self.job is not None and self.job.status == "running"

    def _new_job(self, kind: str) -> Job:
        job = Job(kind)
        self.jobs[job.id] = job
        self.job = job
        return job

    def _fail(self, job: Job, error: Exception) -> None:
        if isinstance(error, (pipeline.PipelineError, RecorderError)):
            message = str(error)
        else:
            message = f"Transcription failed: {error}"
        job.error, job.status = message, "failed"

    def _notify(self, message: str) -> None:
        self._notice_count += 1
        self.notice = {"id": self._notice_count, "message": message}

    def check_recorder(self) -> None:
        """Notice a helper that died mid-recording (e.g. capture interrupted)."""
        rec = self.recorder
        if self.state not in (State.RECORDING, State.PAUSED) or rec is None or rec.alive:
            return
        if not self.lock.acquire(blocking=False):
            return
        try:
            if self.state in (State.RECORDING, State.PAUSED) and not rec.alive:
                self.duration = rec.elapsed
                saved = rec.stop()  # picks up a SAVED line if the helper saved first
                if saved:
                    self.tracks, self.state = saved, State.STOPPED
                    self._notify("The recorder stopped on its own. The audio up to that "
                                 "point was saved; click Finish to transcribe it.")
                else:
                    self.state = State.IDLE
                    self._notify(rec.error or "The recorder stopped unexpectedly.")
        finally:
            self.lock.release()

    def snapshot(self) -> dict:
        self.check_recorder()
        rec, state = self.recorder, self.state
        live = state in (State.RECORDING, State.PAUSED) and rec is not None
        if live:
            elapsed = rec.elapsed
        elif state in (State.STOPPED, State.PROCESSING, State.DONE):
            elapsed = self.duration
        else:
            elapsed = 0.0
        me, them = rec.levels if (live and state == State.RECORDING) else (0.0, 0.0)
        return {
            "state": state.value,
            "buttons": BUTTONS[state],
            "elapsed": round(elapsed, 2),
            "levels": {"me": me, "them": them},
            "name": rec.name if rec else None,
            "app": (rec.app or SYSTEM_AUDIO) if rec else None,
            "tracks": [str(p) for p in self.tracks] if self.tracks else None,
            "transcript_id": self.transcript_id,
            "job": self.job.as_dict() if self.job else None,
            "notice": self.notice,
        }

    @property
    def busy(self) -> bool:
        """Recording or transcribing: closing the window would lose work."""
        return self.state in (State.RECORDING, State.PAUSED) or self.job_running

    def shutdown(self) -> None:
        """Save any recording in progress. The helper also saves on stdin EOF."""
        rec = self.recorder
        if rec is not None and rec.alive:
            try:
                self.tracks = rec.stop()
                self.state = State.STOPPED
            except RecorderError:
                pass


# ---------------------------------------------------------------------------
# Request guard
# ---------------------------------------------------------------------------

class OriginGuard:
    """Refuse requests that didn't come from the ScribeIQ page itself.

    The server only listens on 127.0.0.1, but any web page open in a browser
    can still send requests there. Checking Host stops DNS-rebinding tricks;
    checking Origin stops another site from, say, starting a recording.
    """

    def __init__(self, app, allowed_hosts: set, extra_origins: set):
        self.app = app
        self.allowed_hosts = allowed_hosts
        self.extra_origins = extra_origins

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            host = headers.get("host", "")
            origin = headers.get("origin")
            if not self._host_ok(host) or not self._origin_ok(origin, host):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                response = JSONResponse({"detail": "Request refused."}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def _host_ok(self, host: str) -> bool:
        return host.rsplit(":", 1)[0] in self.allowed_hosts

    def _origin_ok(self, origin: str | None, host: str) -> bool:
        if origin is None:
            return True  # not from a web page (curl, tests, pywebview navigation)
        if origin in self.extra_origins:
            return True
        return urlparse(origin).netloc == host


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class RecordBody(BaseModel):
    name: str = ""
    app: str | None = None


class FinishBody(BaseModel):
    my_name: str = ""
    their_count: int = Field(1, ge=1, le=20)
    model: str = "large-v3"


def check_model(model: str) -> None:
    if model not in pipeline.MODEL_REPOS:
        raise HTTPException(400, f"Unknown model '{model}'. Choose one of: "
                                 f"{', '.join(pipeline.MODEL_REPOS)}.")


def create_app(*, recorder_bin: Path | None = None, recordings_dir: Path | None = None,
               transcripts_dir: Path | None = None, call_runner=None, file_runner=None,
               allowed_hosts: set | None = None, extra_origins: set | None = None,
               serve_frontend: bool = True, trash_dir: Path | None = None) -> FastAPI:
    """Build the API. Arguments default to config; tests pass fakes."""
    studio = Studio(recorder_bin or config.RECORDER_BIN,
                    recordings_dir or config.RECORDINGS_DIR,
                    TranscriptStore(transcripts_dir or config.TRANSCRIPTS_DIR))
    trash_dir = trash_dir or TRASH
    call_runner = call_runner or calls.transcribe_call
    file_runner = file_runner or pipeline.transcribe_source

    @asynccontextmanager
    async def lifespan(app):
        yield
        studio.shutdown()

    app = FastAPI(title="ScribeIQ", lifespan=lifespan)
    app.state.studio = studio

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(RecorderError)
    async def recorder_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(pipeline.PipelineError)
    async def pipeline_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(TranscriptNotFound)
    async def not_found(request, exc):
        return JSONResponse({"detail": "That transcript no longer exists."}, status_code=404)

    # ---- settings --------------------------------------------------------
    @app.get("/api/config")
    def get_config():
        return {"models": list(pipeline.MODEL_REPOS), "default_model": "large-v3",
                "my_name": config.MY_NAME, "hf_token": bool(os.environ.get("HF_TOKEN")),
                "default_session_name": default_session_name()}

    @app.get("/api/apps")
    def get_apps():
        apps = list_apps(studio.recorder_bin)
        ids = {bundle_id for _, bundle_id in apps}
        return {
            "apps": [{"id": SYSTEM_AUDIO, "name": SYSTEM_AUDIO_LABEL}]
                    + [{"id": bundle_id, "name": name} for name, bundle_id in apps],
            "default": PREFERRED_APP if PREFERRED_APP in ids else SYSTEM_AUDIO,
        }

    # ---- recording session -----------------------------------------------
    @app.get("/api/session")
    def get_session():
        return studio.snapshot()

    @app.post("/api/session/record")
    def record(body: RecordBody | None = None):
        body = body or RecordBody()
        return studio.record(body.name, body.app)

    @app.post("/api/session/pause")
    def pause():
        return studio.pause()

    @app.post("/api/session/resume")
    def resume():
        return studio.resume()

    @app.post("/api/session/stop")
    def stop():
        return studio.stop()

    @app.post("/api/session/finish", status_code=202)
    def finish(body: FinishBody | None = None):
        body = body or FinishBody()
        check_model(body.model)
        if body.their_count > 1:
            pipeline.require_hf_token()  # say so now, before the state changes
        job = studio.finish(body.my_name, body.their_count, body.model, call_runner)
        return job.as_dict()

    # ---- file transcription ----------------------------------------------
    @app.post("/api/files/transcribe", status_code=202)
    def transcribe_file(file: UploadFile | None = File(None), url: str = Form(""),
                        name: str = Form(""), identify_speakers: str = Form("true"),
                        num_speakers: int | None = Form(None), model: str = Form("large-v3")):
        # Parsed by hand: FastAPI reads a missing bool form field as False.
        identify_speakers = identify_speakers.strip().lower() in ("true", "1", "on", "yes")
        check_model(model)
        url = url.strip()
        if not url and file is None:
            raise HTTPException(400, "Upload an audio file or paste an audio URL first.")
        if url and not pipeline.is_url(url):
            raise HTTPException(400, "The URL must start with http:// or https://.")
        if identify_speakers:
            pipeline.require_hf_token()
        if num_speakers is not None and num_speakers < 1:
            num_speakers = None

        cleanup = None
        if url:
            source = url
        else:
            cleanup = Path(tempfile.mkdtemp(prefix="scribeiq_upload_"))
            filename = Path(file.filename or "upload").name or "upload"
            dest = cleanup / filename
            with dest.open("wb") as out:
                shutil.copyfileobj(file.file, out)
            source = str(dest)
        title = name.strip() or pipeline.source_name(url or (file.filename or "upload"))
        job = studio.transcribe_file(source, title, identify_speakers, num_speakers, model,
                                     file_runner, cleanup=cleanup)
        return job.as_dict()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = studio.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "No such job.")
        return job.as_dict()

    # ---- transcripts -----------------------------------------------------
    @app.get("/api/transcripts")
    def list_transcripts():
        return studio.store.list()

    @app.get("/api/transcripts/{transcript_id}")
    def get_transcript(transcript_id: str):
        return studio.store.load(transcript_id)

    @app.patch("/api/transcripts/{transcript_id}/speakers")
    def rename(transcript_id: str, mapping: dict[str, str] = Body(...)):
        try:
            return studio.store.rename_speakers(transcript_id, mapping)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @app.delete("/api/transcripts/{transcript_id}")
    def delete_transcript(transcript_id: str):
        studio.store.delete(transcript_id, trash=trash_dir)
        with studio.lock:
            if studio.transcript_id == transcript_id:
                studio.transcript_id = None  # the Record tab stops showing it
        return {"deleted": transcript_id}

    @app.get("/api/transcripts/{transcript_id}/download")
    def download(transcript_id: str, format: str = "txt"):
        try:
            path = studio.store.file_path(transcript_id, format)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        media = "text/markdown" if format == "md" else "text/plain"
        return FileResponse(path, media_type=media, filename=path.name)

    # ---- live feed -------------------------------------------------------
    @app.websocket("/api/live")
    async def live(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                await ws.send_json(studio.snapshot())
                await asyncio.sleep(LIVE_INTERVAL)
        except (WebSocketDisconnect, RuntimeError):
            pass

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
    def api_not_found(rest: str):
        raise HTTPException(404, "Not found.")

    if serve_frontend and FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    hosts = allowed_hosts or {"127.0.0.1", "localhost"}
    origins = extra_origins or set()
    app.add_middleware(OriginGuard, allowed_hosts=hosts, extra_origins=origins)
    return app


def frontend_built() -> bool:
    return (FRONTEND_DIST / "index.html").exists()
