#!/usr/bin/env python3
"""ScribeIQ: record a call from any app (or transcribe an audio file) and get a
speaker-labeled transcript, entirely on your Mac.

RUN:
    source .venv/bin/activate
    python app.py              # opens the ScribeIQ window
    python app.py --browser    # opens it in your default browser instead
    python app.py --dev        # API on :8765 for the Vite dev server (see README)

The window shows the React front end (frontend/dist) served by the local
FastAPI server in scribeiq/api.py, which listens on 127.0.0.1 only.
"""

import argparse
import socket
import sys
import threading
import webbrowser

import uvicorn

from scribeiq import api

DEV_API_PORT = 8765
DEV_UI_URL = "http://localhost:5173"
DEV_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}
HOST = "127.0.0.1"
BUSY_MESSAGE = ("ScribeIQ is still recording or transcribing. Quit anyway? "
                "A recording in progress is saved; a transcription in progress is lost.")


def free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def start_server(app, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    """Run uvicorn in a background thread and wait until it accepts requests."""
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        if not thread.is_alive():
            raise SystemExit(f"The ScribeIQ server could not start on port {port}.")
        thread.join(0.05)
    return server, thread


def stop_server(server: uvicorn.Server, thread: threading.Thread) -> None:
    server.should_exit = True  # runs the API's shutdown, which saves any recording
    thread.join(timeout=15)


def open_window(url: str, studio) -> None:
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window("ScribeIQ", url, width=1240, height=860, min_size=(900, 640))

    def on_closing():
        # Runs before pywebview decides whether to ask; only ask while busy.
        window.confirm_close = studio.busy

    window.events.closing += on_closing
    webview.start(localization={"global.quitConfirmation": BUSY_MESSAGE})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ScribeIQ app.")
    parser.add_argument("--browser", action="store_true",
                        help="open in the default browser instead of a window")
    parser.add_argument("--dev", action="store_true",
                        help=f"serve the API on port {DEV_API_PORT} for `npm run dev`")
    parser.add_argument("--port", type=int, help="port for the local server (default: any free)")
    args = parser.parse_args()

    if args.dev:
        port = args.port or DEV_API_PORT
        app = api.create_app(extra_origins=DEV_ORIGINS, serve_frontend=False)
        url = DEV_UI_URL
        print(f"API on http://{HOST}:{port}. Start the UI with: cd frontend && npm run dev")
    else:
        if not api.frontend_built():
            sys.exit("The front end isn't built yet. Run:\n"
                     "    cd frontend && npm install && npm run build\n"
                     "then start ScribeIQ again.")
        port = args.port or free_port()
        app = api.create_app()
        url = f"http://{HOST}:{port}"

    server, thread = start_server(app, port)
    studio = app.state.studio
    try:
        if args.browser:
            print(f"ScribeIQ is running at {url}. Press Ctrl+C to quit.")
            webbrowser.open(url)
            while thread.is_alive():
                thread.join(0.5)
        else:
            open_window(url, studio)
    except KeyboardInterrupt:
        pass
    finally:
        studio.shutdown()
        stop_server(server, thread)


if __name__ == "__main__":
    main()
