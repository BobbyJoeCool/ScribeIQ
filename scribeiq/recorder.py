"""Controls the native recorder helper (recorder/bin/scribe-recorder) from Python.

The helper does the actual audio capture; this module starts it, sends it
pause/resume/stop over stdin, reads its status lines, and keeps the session
state the UI shows. See recorder/ScribeRecorder.swift for the protocol.
"""

import atexit
import json
import subprocess
import threading
import time
from datetime import datetime
from enum import Enum
from pathlib import Path


class State(str, Enum):
    IDLE = "Idle"
    RECORDING = "Recording"
    PAUSED = "Paused"
    STOPPED = "Stopped"
    PROCESSING = "Processing"
    DONE = "Done"


# Which buttons are usable in each state. "pause" doubles as Resume while paused.
BUTTONS = {
    State.IDLE:       {"record": True,  "pause": False, "stop": False, "finish": False},
    State.RECORDING:  {"record": False, "pause": True,  "stop": True,  "finish": False},
    State.PAUSED:     {"record": False, "pause": True,  "stop": True,  "finish": False},
    State.STOPPED:    {"record": True,  "pause": False, "stop": False, "finish": True},
    State.PROCESSING: {"record": False, "pause": False, "stop": False, "finish": False},
    State.DONE:       {"record": True,  "pause": False, "stop": False, "finish": False},
}

# What each action does to the state. Anything not listed is not allowed.
TRANSITIONS = {
    (State.IDLE, "record"): State.RECORDING,
    (State.STOPPED, "record"): State.RECORDING,
    (State.DONE, "record"): State.RECORDING,
    (State.RECORDING, "pause"): State.PAUSED,
    (State.PAUSED, "resume"): State.RECORDING,
    (State.RECORDING, "stop"): State.STOPPED,
    (State.PAUSED, "stop"): State.STOPPED,
    (State.STOPPED, "finish"): State.PROCESSING,
    (State.PROCESSING, "done"): State.DONE,
    (State.PROCESSING, "failed"): State.STOPPED,
}


def next_state(state: State, action: str) -> State:
    try:
        return TRANSITIONS[(state, action)]
    except KeyError:
        raise ValueError(f"Can't {action} while {state.value.lower()}") from None


def default_session_name() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M")


def unique_session_name(folder: Path, name: str) -> str:
    """Never overwrite an earlier recording: a taken name gets " (2)", " (3)"..."""
    name = name.strip().replace("/", "-") or default_session_name()
    candidate, n = name, 2
    while (folder / f"{candidate}_me.wav").exists() or (folder / f"{candidate}_them.wav").exists():
        candidate, n = f"{name} ({n})", n + 1
    return candidate


class RecorderError(RuntimeError):
    pass


SYSTEM_AUDIO = "system"  # session "source" when no single app was chosen


def list_apps(binary: Path, timeout: float = 10.0) -> list[tuple[str, str]]:
    """Running regular apps as [(name, bundle_id)], sorted by name.
    Needs no macOS permission (the helper only asks NSWorkspace)."""
    binary = Path(binary)
    if not binary.exists():
        raise RecorderError(f"Recorder helper not built yet. Run recorder/build.sh "
                            f"(expected at {binary}).")
    try:
        out = subprocess.run([str(binary), "--list-apps"], capture_output=True, text=True,
                             timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise RecorderError("Listing running apps took too long.") from None
    apps = {}
    for line in out.stdout.splitlines():
        kind, _, rest = line.partition(" ")
        if kind == "APP" and "\t" in rest:
            bundle_id, name = rest.split("\t", 1)
            apps[bundle_id] = name
    return sorted(((name, bid) for bid, name in apps.items()), key=lambda a: a[0].lower())


class Recorder:
    """One recording session backed by the helper process."""

    def __init__(self, binary: Path, out_dir: Path, name: str, app: str | None = None):
        """app is a bundle id (e.g. "us.zoom.xos") to record only that app's
        audio as the other side, or None for all system audio."""
        self.binary = Path(binary)
        self.out_dir = Path(out_dir)
        self.name = unique_session_name(self.out_dir, name)
        self.app = app or None
        self.levels = (0.0, 0.0)
        self.error = None
        self.saved = None  # (me_path, them_path) once the helper confirms
        self.pauses = []   # [{"at": recorded seconds, "length": seconds}]
        self._session_written = False
        self._proc = None
        self._reader = None
        self._ready = threading.Event()
        self._started_at = None
        self._paused_at = None
        self._paused_total = 0.0
        self._started_wall = None

    # ---- lifecycle -------------------------------------------------------
    def start(self, timeout: float = 20.0) -> None:
        if not self.binary.exists():
            raise RecorderError(f"Recorder helper not built yet. Run recorder/build.sh "
                                f"(expected at {self.binary}).")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [str(self.binary), "--out-dir", str(self.out_dir), "--name", self.name]
        if self.app:
            cmd += ["--app", self.app]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True, bufsize=1)
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()
        atexit.register(self.stop)

        if not self._ready.wait(timeout) or self.error:
            self._kill()
            raise RecorderError(self.error or "The recorder didn't start in time.")
        self._started_at = time.monotonic()
        self._started_wall = datetime.now().isoformat(timespec="seconds")

    def pause(self) -> None:
        if self._paused_at is None:
            self._send("pause")
            self._paused_at = time.monotonic()
            self.pauses.append({"at": round(self.elapsed, 2), "length": 0.0})

    def resume(self) -> None:
        if self._paused_at is not None:
            self._send("resume")
            length = time.monotonic() - self._paused_at
            self._paused_total += length
            self.pauses[-1]["length"] = round(length, 2)
            self._paused_at = None

    def stop(self, timeout: float = 10.0) -> tuple:
        """Ask the helper to save and exit. Returns (me_path, them_path)."""
        if self._proc is None:
            return self.saved
        if self._proc.poll() is not None:
            # The helper already exited (e.g. SIGTERM, which makes it save first).
            self._reader.join(timeout=2)
            if self.saved and not self._session_written:
                if self._paused_at is not None:
                    self.resume_bookkeeping_only()
                self._write_session(round(self.elapsed, 2))
            atexit.unregister(self.stop)
            return self.saved
        if self._paused_at is not None:
            self.resume_bookkeeping_only()
        duration = round(self.elapsed, 2)
        self._send("stop")
        try:
            self._proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self._kill()  # last resort; the WAV headers may be incomplete
            raise RecorderError("The recorder didn't stop cleanly; the audio may be damaged.")
        self._reader.join(timeout=2)  # make sure the SAVED line has been read
        if not self.saved:
            raise RecorderError(self.error or "The recorder exited without saving.")
        self._write_session(duration)
        atexit.unregister(self.stop)
        return self.saved

    def resume_bookkeeping_only(self) -> None:
        """Close out an open pause when stopping while paused."""
        length = time.monotonic() - self._paused_at
        self._paused_total += length
        self.pauses[-1]["length"] = round(length, 2)
        self._paused_at = None

    # ---- status ----------------------------------------------------------
    @property
    def alive(self) -> bool:
        """The helper process is running (recording or paused)."""
        return self._proc is not None and self._proc.poll() is None

    @property
    def elapsed(self) -> float:
        """Recorded seconds, not counting time spent paused."""
        if self._started_at is None:
            return 0.0
        now = self._paused_at or time.monotonic()
        return max(0.0, now - self._started_at - self._paused_total)

    @property
    def session_path(self) -> Path:
        return self.out_dir / f"{self.name}_session.json"

    # ---- internals -------------------------------------------------------
    def _send(self, command: str) -> None:
        try:
            self._proc.stdin.write(command + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError, AttributeError):
            self.error = self.error or "The recorder stopped unexpectedly."

    def _read_output(self) -> None:
        for line in self._proc.stdout:
            kind, _, rest = line.strip().partition(" ")
            if kind == "READY":
                self._ready.set()
            elif kind == "LEVEL":
                try:
                    me, them = (float(x) for x in rest.split())
                    self.levels = (me, them)
                except ValueError:
                    pass
            elif kind == "SAVED":
                me_path, _, them_path = rest.partition(".wav ")
                self.saved = (Path(me_path + ".wav"), Path(them_path))
            elif kind == "ERROR":
                self.error = rest
                self._ready.set()

    def _write_session(self, duration: float) -> None:
        self._session_written = True
        self.session_path.write_text(json.dumps({
            "name": self.name,
            "started": self._started_wall,
            "duration": duration,
            "source": self.app or SYSTEM_AUDIO,
            "tracks": {"me": str(self.saved[0]), "them": str(self.saved[1])},
            "pauses": self.pauses,
        }, indent=2))

    def _kill(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
