#!/usr/bin/env python3
"""Stand-in for recorder/bin/scribe-recorder that speaks the same stdin/stdout
protocol, so the Python controller can be tested without capturing audio.

Running apps are faked as Zoom and Safari. A session named "fail" reports a
permission error, like the real helper does without Screen Recording access.
"""
import sys
from pathlib import Path

RUNNING = {"us.zoom.xos": "zoom.us", "com.apple.Safari": "Safari"}

if sys.argv[1:] == ["--list-apps"]:
    for bundle_id, name in RUNNING.items():
        print(f"APP {bundle_id}\t{name}", flush=True)
    sys.exit(0)

args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
out, name = Path(args["--out-dir"]), args["--name"]
app = args.get("--app")
if app is not None and app not in RUNNING:
    print(f"ERROR {app} is not running", flush=True)
    sys.exit(3)
if name == "fail":
    print("ERROR Screen Recording permission is needed", flush=True)
    sys.exit(2)
out.mkdir(parents=True, exist_ok=True)
print("READY", flush=True)
print("RECORDING", flush=True)
print("LEVEL 0.250 0.500", flush=True)
for line in sys.stdin:
    cmd = line.strip()
    if cmd == "pause":
        print("PAUSED", flush=True)
    elif cmd == "resume":
        print("RECORDING", flush=True)
    elif cmd == "stop":
        break
me, them = out / f"{name}_me.wav", out / f"{name}_them.wav"
me.write_bytes(b"RIFF"); them.write_bytes(b"RIFF")
print(f"SAVED {me} {them}", flush=True)
