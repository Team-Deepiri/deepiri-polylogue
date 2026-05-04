from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - Windows without fcntl
    fcntl = None  # type: ignore[assignment]


def journal_path(root: Path) -> Path:
    return root / "journal.jsonl"


def append_event(root: Path, event: dict[str, Any]) -> None:
    path = journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False) + "\n"
    data = line.encode("utf-8")
    flags = os.O_CREAT | os.O_WRONLY | os.O_APPEND
    fd = os.open(path, flags, 0o644)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            os.write(fd, data)
        finally:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def iter_events(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def tail_events(root: Path, *, lines: int = 50) -> list[dict[str, Any]]:
    path = journal_path(root)
    if not path.is_file():
        return []
    n = max(1, min(int(lines), 50_000))
    buf: list[dict[str, Any]] = []
    for ev in iter_events(path):
        buf.append(ev)
        if len(buf) > n:
            buf.pop(0)
    return buf
