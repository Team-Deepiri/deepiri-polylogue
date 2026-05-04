from __future__ import annotations

import os
from pathlib import Path


def polylogue_root(cwd: Path | None = None) -> Path:
    """Return the active polylogue directory (contains meta, journal, roster)."""
    env = os.environ.get("DEEPIRI_POLYLOGUE_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    base = (cwd or Path.cwd()).resolve()
    return base / ".deepiri" / "polylogue"


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)
