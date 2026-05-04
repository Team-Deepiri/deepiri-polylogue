from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import utc_now_iso
from .paths import ensure_dir


def meta_path(root: Path) -> Path:
    return root / "meta.json"


def init_session(root: Path, session: str) -> None:
    ensure_dir(root)
    mp = meta_path(root)
    payload: dict[str, Any] = {
        "session": session,
        "created_at": utc_now_iso(),
        "kind": "deepiri-polylogue",
    }
    mp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_meta(root: Path) -> dict[str, Any]:
    mp = meta_path(root)
    if not mp.is_file():
        raise FileNotFoundError(f"missing {mp}; run: polylogue init")
    return json.loads(mp.read_text(encoding="utf-8"))
