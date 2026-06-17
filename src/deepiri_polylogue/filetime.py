from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import utc_now_iso
from . import workspace as ws


def file_reads_path(root: Path) -> Path:
    return root / "file_reads.json"


def _default_doc() -> dict[str, Any]:
    return {"updated_at": utc_now_iso(), "reads": {}}


def load_file_reads(root: Path) -> dict[str, Any]:
    p = file_reads_path(root)
    if not p.is_file():
        return _default_doc()
    return json.loads(p.read_text(encoding="utf-8"))


def save_file_reads(root: Path, doc: dict[str, Any]) -> None:
    doc["updated_at"] = utc_now_iso()
    ws.atomic_write_json(file_reads_path(root), doc)


def resolve_file_path(path: str, *, cwd: Path | None) -> Path:
    raw = Path(path).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    base = (cwd or Path.cwd()).resolve()
    return (base / raw).resolve()


def _mtime_iso(path: Path) -> str:
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _mtime_epoch(path: Path) -> float:
    return path.stat().st_mtime


@dataclass(frozen=True)
class FileReadRecord:
    actor_id: str
    path: str
    abs_path: str
    cwd: str | None
    read_at: str
    mtime_at_read: str


def record_read(root: Path, *, actor_id: str, path: str, cwd: Path | None = None) -> FileReadRecord:
    abs_path = resolve_file_path(path, cwd=cwd)
    if not abs_path.is_file():
        raise FileNotFoundError(f"not a file: {abs_path}")
    read_at = utc_now_iso()
    mtime_at_read = _mtime_epoch(abs_path)
    doc = load_file_reads(root)
    reads: dict[str, Any] = doc.setdefault("reads", {})
    actor_reads: dict[str, Any] = reads.setdefault(actor_id, {})
    actor_reads[str(abs_path)] = {
        "path": path,
        "abs_path": str(abs_path),
        "cwd": str(cwd.resolve()) if cwd else None,
        "read_at": read_at,
        "mtime_at_read": mtime_at_read,
    }
    save_file_reads(root, doc)
    return FileReadRecord(
        actor_id=actor_id,
        path=path,
        abs_path=str(abs_path),
        cwd=str(cwd.resolve()) if cwd else None,
        read_at=read_at,
        mtime_at_read=_mtime_iso(abs_path),
    )


@dataclass(frozen=True)
class StaleFile:
    actor_id: str
    path: str
    abs_path: str
    read_at: str
    last_modification: str

    def format_message(self) -> str:
        return (
            f"File {self.abs_path} has been modified since it was last read.\n"
            f"Last modification: {self.last_modification}\n"
            f"Last read: {self.read_at}\n\n"
            f"Please read the file again before modifying it."
        )


def _is_stale(abs_path: Path, mtime_at_read: float) -> str | None:
    if not abs_path.is_file():
        return None
    current = _mtime_epoch(abs_path)
    if current > mtime_at_read:
        return _mtime_iso(abs_path)
    return None


def list_stale(
    root: Path,
    *,
    actor_id: str | None = None,
    path: str | None = None,
    cwd: Path | None = None,
) -> list[StaleFile]:
    doc = load_file_reads(root)
    reads: dict[str, Any] = doc.get("reads", {})
    out: list[StaleFile] = []
    target_abs = resolve_file_path(path, cwd=cwd) if path else None

    for aid, actor_reads in reads.items():
        if actor_id and aid != actor_id:
            continue
        if not isinstance(actor_reads, dict):
            continue
        for abs_s, row in actor_reads.items():
            if not isinstance(row, dict):
                continue
            read_at = str(row.get("read_at", ""))
            raw_mtime = row.get("mtime_at_read")
            if not read_at or raw_mtime is None:
                continue
            mtime_at_read = float(raw_mtime)
            abs_path = Path(abs_s)
            if target_abs and abs_path != target_abs:
                continue
            last_mod = _is_stale(abs_path, mtime_at_read)
            if last_mod:
                out.append(
                    StaleFile(
                        actor_id=aid,
                        path=str(row.get("path") or abs_s),
                        abs_path=str(abs_path),
                        read_at=read_at,
                        last_modification=last_mod,
                    )
                )
    out.sort(key=lambda s: (s.actor_id, s.abs_path))
    return out


def assert_fresh(
    root: Path,
    *,
    actor_id: str,
    path: str,
    cwd: Path | None = None,
) -> StaleFile | None:
    abs_path = resolve_file_path(path, cwd=cwd)
    doc = load_file_reads(root)
    actor_reads = doc.get("reads", {}).get(actor_id, {})
    row = actor_reads.get(str(abs_path)) if isinstance(actor_reads, dict) else None
    if not isinstance(row, dict) or not row.get("read_at") or row.get("mtime_at_read") is None:
        raise ValueError(
            f"You must read file {abs_path} before overwriting it. "
            f"Run `polylogue file read --id {actor_id} --path {path}` first."
        )
    read_at = str(row["read_at"])
    mtime_at_read = float(row["mtime_at_read"])
    last_mod = _is_stale(abs_path, mtime_at_read)
    if not last_mod:
        return None
    return StaleFile(
        actor_id=actor_id,
        path=str(row.get("path") or path),
        abs_path=str(abs_path),
        read_at=read_at,
        last_modification=last_mod,
    )
