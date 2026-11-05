"""Saved transcripts: one .json (the source of truth) plus .txt and .md copies
regenerated from it, all sharing a name in the transcripts folder.

A transcript's id is that shared file name without the extension, e.g.
"2026-11-04_1930" or "Weekly sync (2)". Older transcripts saved before the
.json existed are still listed, as read-only text.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

from . import calls, pipeline

FORMATS = ("txt", "md")
TRASH = Path.home() / ".Trash"


class TranscriptNotFound(LookupError):
    pass


class TranscriptStore:
    def __init__(self, folder: Path):
        self.folder = Path(folder)

    # ---- writing ---------------------------------------------------------
    def save(self, title: str, segments: list, *, labeled: bool, kind: str,
             duration: float | None = None, source: str | None = None) -> dict:
        """Write a new transcript under a name no earlier one uses."""
        if duration is None:
            duration = max((s.get("end", s["start"]) for s in segments), default=0.0)
        record = {
            "id": self._unique_id(title),
            "title": title.strip() or "transcript",
            "created": datetime.now().isoformat(timespec="seconds"),
            "kind": kind,                # "call" or "file"
            "labeled": labeled,          # segments carry speaker names
            "duration": round(duration, 2),
            "source": source,
            "segments": segments,
        }
        self._write(record)
        record["speakers"] = speakers(record)
        return record

    def rename_speakers(self, transcript_id: str, mapping: dict) -> dict:
        record = self.load(transcript_id)
        if record.get("legacy") or not record["labeled"]:
            raise ValueError("This transcript has no speaker names to change.")
        mapping = {old: new.strip() for old, new in mapping.items()
                   if isinstance(new, str) and new.strip() and new.strip() != old}
        if not mapping:
            raise ValueError("Give at least one new speaker name.")
        record["segments"] = calls.rename_speakers(record["segments"], mapping)
        self._write(record)
        record["speakers"] = speakers(record)
        return record

    def delete(self, transcript_id: str, trash: Path = TRASH) -> list[Path]:
        """Move a transcript's .json, .txt and .md to the Trash (recoverable from
        Finder). Returns where the files went."""
        files = [p for ext in ("json", *FORMATS)
                 if (p := self._path(transcript_id, ext)).exists()]
        if not files:
            raise TranscriptNotFound(transcript_id)
        trash.mkdir(parents=True, exist_ok=True)
        moved = []
        for path in files:
            dest, n = trash / path.name, 2
            while dest.exists():  # the Trash may already hold a file with this name
                dest, n = trash / f"{path.stem} ({n}){path.suffix}", n + 1
            moved.append(Path(shutil.move(str(path), str(dest))))
        return moved

    # ---- reading ---------------------------------------------------------
    def list(self) -> list[dict]:
        """Summaries of every transcript, newest first."""
        if not self.folder.exists():
            return []
        items = {}
        for path in self.folder.glob("*.json"):
            try:
                record = json.loads(path.read_text())
                items[path.stem] = summary(record)
            except (OSError, ValueError, KeyError):
                continue  # not one of ours, or damaged; leave it alone
        for path in self.folder.glob("*.txt"):
            if path.stem not in items:
                items[path.stem] = {
                    "id": path.stem, "title": path.stem, "legacy": True,
                    "created": datetime.fromtimestamp(path.stat().st_mtime)
                                       .isoformat(timespec="seconds"),
                    "kind": None, "duration": None, "speakers": [],
                }
        return sorted(items.values(), key=lambda t: t["created"], reverse=True)

    def load(self, transcript_id: str) -> dict:
        json_path = self._path(transcript_id, "json")
        if json_path.exists():
            record = json.loads(json_path.read_text())
            record["id"] = transcript_id
            record["speakers"] = speakers(record)
            return record
        txt = self._path(transcript_id, "txt")
        if txt.exists():
            return {"id": transcript_id, "title": transcript_id, "legacy": True,
                    "labeled": False, "segments": [], "speakers": [],
                    "text": txt.read_text()}
        raise TranscriptNotFound(transcript_id)

    def file_path(self, transcript_id: str, fmt: str) -> Path:
        if fmt not in FORMATS:
            raise ValueError(f"Format must be one of: {', '.join(FORMATS)}")
        path = self._path(transcript_id, fmt)
        if not path.exists():
            raise TranscriptNotFound(transcript_id)
        return path

    # ---- internals -------------------------------------------------------
    def _path(self, transcript_id: str, ext: str) -> Path:
        """Only plain names inside the transcripts folder are allowed."""
        if (not transcript_id or transcript_id != Path(transcript_id).name
                or transcript_id.startswith(".")):
            raise TranscriptNotFound(transcript_id)
        return self.folder / f"{transcript_id}.{ext}"

    def _unique_id(self, title: str) -> str:
        """Like pipeline.unique_path, but free for all three extensions at once."""
        name = title.strip().replace("/", "-").replace("\\", "-").lstrip(".") or "transcript"
        self.folder.mkdir(parents=True, exist_ok=True)
        candidate, n = name, 2
        while any((self.folder / f"{candidate}.{ext}").exists()
                  for ext in ("json", *FORMATS)):
            candidate, n = f"{name} ({n})", n + 1
        return candidate

    def _write(self, record: dict) -> None:
        tid = record["id"]
        stored = {k: v for k, v in record.items() if k != "speakers"}
        text, markdown = render(record)
        self._path(tid, "txt").write_text(text)
        self._path(tid, "md").write_text(markdown)
        self._path(tid, "json").write_text(json.dumps(stored, indent=2))


def render(record: dict) -> tuple[str, str]:
    """(.txt, .md) text for a transcript record."""
    segments, title = record["segments"], record["title"]
    if record["labeled"]:
        return pipeline.format_transcript(segments), pipeline.format_markdown(segments, title)
    text = pipeline.format_plain_transcript(segments)
    return text, f"# {title}\n\n" + text


def speakers(record: dict) -> list[str]:
    """Speaker names in order of first appearance (so colors stay stable)."""
    seen = []
    for seg in record.get("segments", []):
        name = seg.get("speaker")
        if name and not seg.get("marker") and name not in seen:
            seen.append(name)
    return seen


def summary(record: dict) -> dict:
    return {"id": record["id"], "title": record["title"], "created": record["created"],
            "kind": record.get("kind"), "duration": record.get("duration"),
            "speakers": speakers(record), "legacy": False}
