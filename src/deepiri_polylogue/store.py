from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import registry as reg
from . import service_client as svc
from . import workspace as workspace_mod
from .models import utc_now_iso
from .fsutil import ensure_dir


def meta_path(root: Path) -> Path:
    return root / "meta.json"


def init_session(root: Path, session: str, *, workspace: Path | None = None, use_service: bool = True) -> Path:
    """
    Initialize a polylogue session.

    When use_service is True (default), registers the workspace in the global
    user data directory instead of requiring a .deepiri/ sidecar in the repo.
    Returns the resolved session root.
    """
    ws_cwd = (workspace or Path.cwd()).resolve()
    legacy = os.environ.get("POLYLOGUE_LEGACY_SIDECAR", "").strip() in ("1", "true", "yes")

    if use_service and not legacy:
        entry = svc.register_workspace(ws_cwd, session)
        root = Path(entry["session_root"])
    elif root in (Path("."), Path()) or str(root) == ".":
        root = ws_cwd / ".deepiri" / "polylogue"

    ensure_dir(root)
    mp = meta_path(root)
    payload: dict[str, Any] = {
        "session": session,
        "created_at": utc_now_iso(),
        "kind": "deepiri-polylogue",
        "mode": "legacy-sidecar" if legacy else ("service" if use_service else "sidecar"),
        "workspace": str(ws_cwd),
    }
    mp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    workspace_mod.workspace_init(root)
    if use_service and not legacy:
        reg.register_workspace(ws_cwd, session)
    return root


def load_meta(root: Path) -> dict[str, Any]:
    mp = meta_path(root)
    if not mp.is_file():
        raise FileNotFoundError(f"missing {mp}; run: deepiri-polylogue init")
    return json.loads(mp.read_text(encoding="utf-8"))
