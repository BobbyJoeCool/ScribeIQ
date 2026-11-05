#!/usr/bin/env python3
"""ScribeIQ command line.

Transcribe one audio file or URL:
    python transcribe.py interview.m4a --speakers 2

Transcribe a recorded call (the two tracks the recorder saves):
    python transcribe.py --me call_me.wav --them call_them.wav [--their-count 3]

Transcripts are written to Outputs/transcripts in this project (or SCRIBEIQ_HOME)
as .txt and .md (plus a .json the app uses for History and renaming), never
overwriting an earlier one.
"""

import argparse
from pathlib import Path

from scribeiq import calls, config, pipeline
from scribeiq.transcripts import TranscriptStore


def main():
    parser = argparse.ArgumentParser(description="Transcribe audio or a recorded call.")
    parser.add_argument("audio", nargs="?", help="URL or path of an audio file")
    parser.add_argument("--me", type=Path, help="Call mode: your microphone track")
    parser.add_argument("--them", type=Path, help="Call mode: the other side's track")
    parser.add_argument("--their-count", type=int, default=1,
                        help="Call mode: people on the other end (default 1)")
    parser.add_argument("--my-name", default=config.MY_NAME, help="Call mode: your label")
    parser.add_argument("--speakers", type=int, help="File mode: expected number of voices")
    parser.add_argument("--no-diarize", action="store_true",
                        help="File mode: skip speaker labels (no HF_TOKEN needed)")
    parser.add_argument("--model", choices=list(pipeline.MODEL_REPOS), default="large-v3")
    parser.add_argument("--out", help="Output name without extension")
    parser.add_argument("--out-dir", type=Path, default=config.TRANSCRIPTS_DIR)
    args = parser.parse_args()

    call_mode = args.me or args.them
    if call_mode and not (args.me and args.them):
        parser.error("call mode needs both --me and --them")
    if not call_mode and not args.audio:
        parser.error("give an audio file/URL, or --me and --them for a call")

    store = TranscriptStore(args.out_dir)
    try:
        if call_mode:
            title = args.out or args.me.stem.removesuffix("_me")
            session_path = args.me.with_name(f"{title}_session.json")
            segments = calls.transcribe_call(
                args.me, args.them, my_name=args.my_name, their_count=args.their_count,
                model=args.model, session_path=session_path,
                progress=lambda f, desc: print(desc, flush=True))
            session = calls.load_session(session_path)
            record = store.save(title, segments, labeled=True, kind="call",
                                duration=session.get("duration"), source=session.get("source"))
        else:
            title = args.out or pipeline.source_name(args.audio)
            segments = pipeline.transcribe_source(
                args.audio, model=args.model, identify_speakers=not args.no_diarize,
                num_speakers=args.speakers, progress=lambda f, desc: print(desc, flush=True))
            record = store.save(title, segments, labeled=not args.no_diarize, kind="file",
                                source=args.audio if pipeline.is_url(args.audio) else None)
    except pipeline.PipelineError as e:
        raise SystemExit(f"Error: {e}")

    for ext in ("txt", "md"):
        print(f"Wrote {store.file_path(record['id'], ext)}")


if __name__ == "__main__":
    main()
