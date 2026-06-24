"""Workspace → session registry stored in the user data dir (not in repos)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import utc_now_iso
from .platform_detect import data_dir
from .fsutil import ensure_dir


def global_data_root() -> Path:
    p = Path(data_dir())
    ensure_dir(p)
    return p


def registry_path() -> Path:
    return global_data_root() / "registry.json"


def sessions_root() -> Path:
    p = global_data_root() / "sessions"
    ensure_dir(p)
    return p


def _normalize_repo(cwd: Path) -> str:
    return str(cwd.expanduser().resolve())


def _repo_key(cwd: Path) -> str:
    return hashlib.sha256(_normalize_repo(cwd).encode("utf-8")).hexdigest()[:16]


def load_registry() -> dict[str, Any]:
    path = registry_path()
    if not path.is_file():
        return {"version": 1, "workspaces": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_registry(doc: dict[str, Any]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def session_dir_for_name(session: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in session.strip())[:80] or "default"
    p = sessions_root() / safe
    ensure_dir(p)
    return p


def register_workspace(cwd: Path, session: str) -> dict[str, Any]:
    """Map a repo path to a global session directory."""
    repo = _normalize_repo(cwd)
    key = _repo_key(cwd)
    root = session_dir_for_name(session)
    entry = {
        "repo_path": repo,
        "repo_key": key,
        "session": session,
        "session_root": str(root),
        "registered_at": utc_now_iso(),
    }
    doc = load_registry()
    workspaces = doc.setdefault("workspaces", {})
    workspaces[repo] = entry
    doc["updated_at"] = utc_now_iso()
    save_registry(doc)
    return entry


def lookup_workspace(cwd: Path) -> dict[str, Any] | None:
    repo = _normalize_repo(cwd)
    doc = load_registry()
    entry = doc.get("workspaces", {}).get(repo)
    if entry:
        return entry
    # Walk up to find parent repo registration (monorepo subdirs)
    path = Path(repo)
    for parent in [path, *path.parents]:
        pstr = str(parent)
        if pstr in doc.get("workspaces", {}):
            return doc["workspaces"][pstr]
    return None


def resolve_session_root(cwd: Path) -> Path | None:
    entry = lookup_workspace(cwd)
    if not entry:
        return None
    root = Path(entry["session_root"])
    if root.is_dir() and (root / "meta.json").is_file():
        return root
    return None


def list_workspaces() -> list[dict[str, Any]]:
    doc = load_registry()
    return list(doc.get("workspaces", {}).values())
