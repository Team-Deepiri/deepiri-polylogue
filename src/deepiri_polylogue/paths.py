from __future__ import annotations

import os
from pathlib import Path

from . import registry as reg
from . import service_client as svc


def polylogue_root(cwd: Path | None = None) -> Path:
    """
    Resolve the active polylogue directory.

    Priority:
      1. DEEPIRI_POLYLOGUE_ROOT (explicit override)
      2. Running background service / global registry (service mode — no repo sidecar)
      3. Legacy repo sidecar: <cwd>/.deepiri/polylogue
    """
    env = os.environ.get("DEEPIRI_POLYLOGUE_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()

    base = (Path(cwd) if cwd else Path.cwd()).resolve()

    if os.environ.get("POLYLOGUE_LEGACY_SIDECAR", "").strip() in ("1", "true", "yes"):
        return base / ".deepiri" / "polylogue"

    service_root = svc.resolve_root(base)
    if service_root is not None:
        return service_root

    registry_root = reg.resolve_session_root(base)
    if registry_root is not None:
        return registry_root

    return base / ".deepiri" / "polylogue"
