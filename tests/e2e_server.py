#!/usr/bin/env python3
"""ScribeIQ server for the Playwright E2E suite (frontend/e2e/).

Runs the real FastAPI app and the built front end, with the fake recorder
helper (tests/fake_recorder.py), stubbed transcription, and a throwaway output
folder. Nothing records audio, loads a model, or touches the network.

It refuses to start unless SCRIBEIQ_E2E=1 is set, so it can't stand in for
the real app by accident. Playwright starts it (see frontend/playwright.config.ts):

    SCRIBEIQ_E2E=1 python tests/e2e_server.py --port 8799

Stubs:
- Recording a call returns a fixed transcript: your name, then "Them" (one
  person on the other end) or "Speaker 1" and "Speaker 2" (more than one),
  plus a marker for every pause taken while recording.
- Transcribing a file returns two speakers (or plain lines without speaker
  identification). A file or link whose name contains "bad" fails the way an
  unreadable file does.
- Two transcripts are saved at startup so History is never empty.
"""

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scribeiq import api, calls, pipeline  # noqa: E402
from scribeiq.transcripts import TranscriptStore  # noqa: E402

FAKE_RECORDER = Path(__file__).with_name("fake_recorder.py")
ENABLE_VAR = "SCRIBEIQ_E2E"
STEP_DELAY = 0.5  # seconds per stubbed step, so progress is visible


class NotEnabled(SystemExit):
    pass


def require_enabled(environ=os.environ) -> None:
    if environ.get(ENABLE_VAR) != "1":
        raise NotEnabled(f"The E2E server uses a fake recorder and fake transcription. "
                         f"Set {ENABLE_VAR}=1 to run it; for real use run app.py.")


def stub_call(me_wav, them_wav, *, my_name, their_count, model, session_path, progress):
    for fraction, step in ((0.1, "Preparing audio..."), (0.45, f"Transcribing {my_name}..."),
                           (0.8, "Transcribing the other side...")):
        progress(fraction, step)
        time.sleep(STEP_DELAY)
    them = ["Them", "Them"] if their_count == 1 else ["Speaker 1", "Speaker 2"]
    mine = [{"start": 0.0, "end": 0.4, "text": "Hi, can you hear me?", "speaker": my_name}]
    theirs = [{"start": 0.5, "end": 0.9, "text": "Loud and clear.", "speaker": them[0]},
              {"start": 600.0, "end": 602.0, "text": "Let's get started.", "speaker": them[1]}]
    pauses = calls.load_session(session_path).get("pauses", [])
    return calls.merge_tracks(mine, theirs, calls.pause_markers(pauses))


def stub_file(source, *, model, identify_speakers, num_speakers, progress):
    name = Path(source).name
    for fraction, step in ((0.05, "Preparing audio..."), (0.15, f"Transcribing with {model}..."),
                           (0.7, "Identifying speakers...")):
        progress(fraction, step)
        time.sleep(STEP_DELAY)
        if "bad" in name:
            raise pipeline.PipelineError(f"Couldn't read {name} as audio "
                                         "(Invalid data found when processing input).")
    segments = [{"start": 0.0, "end": 3.0, "text": f"Welcome to {name}."},
                {"start": 3.5, "end": 6.0, "text": "Thanks for having me."}]
    if identify_speakers:
        segments = [{**s, "speaker": f"Speaker {i + 1}"} for i, s in enumerate(segments)]
    return segments


def seed(store: TranscriptStore) -> None:
    store.save("Seeded team call", [
        {"start": 0.0, "end": 2.0, "text": "Morning, everyone.", "speaker": "Me"},
        {"start": 2.5, "end": 4.0, "text": "Morning!", "speaker": "Speaker 1"},
    ], labeled=True, kind="call", duration=1800, source="us.zoom.xos")
    store.save("Seeded lecture", [{"start": 0.0, "end": 5.0, "text": "Today we cover sorting."}],
               labeled=False, kind="file")


def build_app(data_dir: Path):
    """The app with every slow or real-world piece swapped for a fake."""
    os.environ.setdefault("HF_TOKEN", "e2e-not-a-real-token")  # group calls pass the check
    os.environ["FAKE_RECORDER_LIST_GONE"] = "1"
    transcripts = data_dir / "transcripts"
    seed(TranscriptStore(transcripts))
    return api.create_app(recorder_bin=FAKE_RECORDER, recordings_dir=data_dir / "recordings",
                          transcripts_dir=transcripts, trash_dir=data_dir / "Trash",
                          call_runner=stub_call, file_runner=stub_file)


def main(argv=None) -> None:
    require_enabled()
    parser = argparse.ArgumentParser(description="ScribeIQ server for E2E tests.")
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args(argv)
    if not api.frontend_built():
        sys.exit("Build the front end first: cd frontend && npm run build")

    import uvicorn

    with tempfile.TemporaryDirectory(prefix="scribeiq_e2e_") as tmp:
        app = build_app(Path(tmp))
        print(f"E2E server on http://127.0.0.1:{args.port} (data in {tmp})", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
