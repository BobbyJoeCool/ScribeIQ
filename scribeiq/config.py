"""Paths and settings for ScribeIQ.

Everything can be overridden with environment variables, either exported in the
shell or placed in a `.env` file at the project root (see `.env.example`).
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Minimal .env reader: KEY=value lines, # comments. Real environment
    variables win over the file, so an exported HF_TOKEN is never overwritten."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_dotenv()

# Recordings and transcripts live in Outputs/ inside the project (gitignored),
# so they're right next to the code in the editor.
DATA_DIR = Path(os.environ.get("SCRIBEIQ_HOME", PROJECT_ROOT / "Outputs")).expanduser()
RECORDINGS_DIR = DATA_DIR / "recordings"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"

RECORDER_BIN = Path(os.environ.get(
    "SCRIBEIQ_RECORDER", PROJECT_ROOT / "recorder" / "bin" / "scribe-recorder"))

MY_NAME = os.environ.get("SCRIBEIQ_MY_NAME", "Me")
