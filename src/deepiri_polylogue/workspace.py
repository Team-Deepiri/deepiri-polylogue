from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Literal

from .models import utc_now_iso
from .fsutil import ensure_dir

ActorKind = Literal["participant", "subagent"]
ActorState = Literal["idle", "reading", "editing"]


def shared_dir(root: Path) -> Path:
    return root / "shared"


def context_path(root: Path) -> Path:
    return shared_dir(root) / "context.md"


def memory_path(root: Path) -> Path:
    return shared_dir(root) / "memory.md"


def presence_path(root: Path) -> Path:
    return root / "presence.json"


def scratch_root(root: Path) -> Path:
    return root / "scratch"


def scratch_dir_for(root: Path, participant_id: str) -> Path:
    safe = sanitize_id(participant_id)
    return scratch_root(root) / safe


def sanitize_id(participant_id: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", participant_id.strip())
    return (s.strip("._") or "anon")[:120]


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path: Path, obj: Any) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    atomic_write_text(path, text)


def default_presence() -> dict[str, Any]:
    return {"updated_at": utc_now_iso(), "actors": []}


def load_presence(root: Path) -> dict[str, Any]:
    p = presence_path(root)
    if not p.is_file():
        return default_presence()
    return json.loads(p.read_text(encoding="utf-8"))


def save_presence(root: Path, doc: dict[str, Any]) -> None:
    doc["updated_at"] = utc_now_iso()
    atomic_write_json(presence_path(root), doc)


def workspace_init(root: Path) -> None:
    ensure_dir(shared_dir(root))
    ensure_dir(scratch_root(root))
    cp = context_path(root)
    if not cp.is_file():
        cp.write_text(
            "# Shared context (canonical)\n\n"
            "All surfaces read this file via `polylogue sync-pack`.\n"
            "Edit with `polylogue context append` / `context set` or manually.\n\n"
            "---\n\n",
            encoding="utf-8",
        )
    mp = memory_path(root)
    if not mp.is_file():
        mp.write_text(
            "# Long-term memory / decisions\n\n"
            "Append-only durable notes (not every chat token).\n\n",
            encoding="utf-8",
        )
    if not presence_path(root).is_file():
        save_presence(root, default_presence())


def read_text_tail(path: Path, *, max_bytes: int) -> str:
    if not path.is_file():
        return ""
    raw = path.read_bytes()
    if len(raw) <= max_bytes:
        return raw.decode("utf-8", errors="replace")
    chunk = raw[-max_bytes:]
    return "...[truncated]\n" + chunk.decode("utf-8", errors="replace")


def parse_path_role(spec: str) -> tuple[str, str]:
    if ":" not in spec:
        return spec, "edit"
    path, role = spec.rsplit(":", 1)
    role = role.strip().lower()
    if role not in ("edit", "read"):
        raise ValueError(f"role must be edit|read, got {role!r} in {spec!r}")
    return path.strip(), role


def upsert_actor(
    root: Path,
    *,
    actor_id: str,
    kind: ActorKind,
    parent_id: str | None,
    label: str | None,
    state: ActorState,
    cwd: str | None,
    paths: list[tuple[str, str]] | None,
    note: str | None,
) -> dict[str, Any]:
    doc = load_presence(root)
    actors: list[dict[str, Any]] = list(doc.get("actors", []))
    found = -1
    for i, a in enumerate(actors):
        if a.get("id") == actor_id:
            found = i
            break
    base: dict[str, Any] = {
        "id": actor_id,
        "kind": kind,
        "parent_id": parent_id,
        "label": (label or actor_id) if label is not None else actor_id,
        "state": state,
        "cwd": cwd,
        "paths": [],
        "note": "" if note is None else note,
        "updated_at": utc_now_iso(),
    }
    if found >= 0:
        prev = actors[found]
        base["label"] = label if label is not None else prev.get("label", actor_id)
        if cwd is None:
            base["cwd"] = prev.get("cwd")
        if note is None:
            base["note"] = prev.get("note", "")
        else:
            base["note"] = note
        if paths is None:
            base["paths"] = list(prev.get("paths", []))
        else:
            base["paths"] = [{"path": p, "role": r} for p, r in paths]
    else:
        base["paths"] = [{"path": p, "role": r} for p, r in (paths or [])]

    if found >= 0:
        actors[found] = base
    else:
        actors.append(base)
    doc["actors"] = actors
    save_presence(root, doc)
    return base


def clear_actor(root: Path, actor_id: str) -> bool:
    doc = load_presence(root)
    actors = [a for a in doc.get("actors", []) if a.get("id") != actor_id]
    if len(actors) == len(doc.get("actors", [])):
        return False
    doc["actors"] = actors
    save_presence(root, doc)
    return True


def remove_subagents_of(root: Path, parent_id: str) -> int:
    doc = load_presence(root)
    before = len(doc.get("actors", []))
    doc["actors"] = [a for a in doc.get("actors", []) if not (a.get("kind") == "subagent" and a.get("parent_id") == parent_id)]
    removed = before - len(doc["actors"])
    if removed:
        save_presence(root, doc)
    return removed


def clear_subagent(root: Path, parent_id: str, sub_id: str) -> bool:
    doc = load_presence(root)
    actors = [
        a
        for a in doc.get("actors", [])
        if not (a.get("kind") == "subagent" and a.get("parent_id") == parent_id and a.get("id") == sub_id)
    ]
    if len(actors) == len(doc.get("actors", [])):
        return False
    doc["actors"] = actors
    save_presence(root, doc)
    return True


def validate_scratch_rel(name: str) -> None:
    if not name.strip() or name.startswith("/"):
        raise ValueError("invalid scratch name")
    for part in name.replace("\\", "/").split("/"):
        if part in ("", ".", ".."):
            raise ValueError("invalid scratch path segment")


def scratch_write_stdin(root: Path, participant_id: str, name: str) -> Path:
    validate_scratch_rel(name)
    d = scratch_dir_for(root, participant_id)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / name
    data = sys.stdin.buffer.read()
    atomic_write_bytes(dest, data)
    return dest


def list_scratch_files(root: Path) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    sr = scratch_root(root)
    if not sr.is_dir():
        return out
    for sub in sorted(sr.iterdir()):
        if not sub.is_dir():
            continue
        n = sum(1 for _ in sub.rglob("*") if _.is_file())
        out.append((sub.name, n))
    return out
