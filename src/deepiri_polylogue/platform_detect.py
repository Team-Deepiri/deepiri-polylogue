"""Cross-platform detection for polylogue service installers."""
from __future__ import annotations

import os
import platform
import sys


def detect_platform() -> str:
    """
    Return one of: linux | macos | wsl | windows | unknown
    """
    if sys.platform == "win32":
        return "windows"

    if sys.platform == "darwin":
        return "macos"

    if sys.platform.startswith("linux"):
        try:
            with open("/proc/version", encoding="utf-8", errors="ignore") as f:
                if "microsoft" in f.read().lower() or "wsl" in os.environ.get("WSL_DISTRO_NAME", "").lower():
                    return "wsl"
        except OSError:
            pass
        return "linux"

    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    return "unknown"


def data_dir() -> str:
    """User-level polylogue data directory (sessions, registry, pid)."""
    plat = detect_platform()
    if plat == "windows":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        return os.path.join(base, "deepiri-polylogue")
    if plat == "macos":
        return os.path.expanduser("~/Library/Application Support/deepiri-polylogue")
    # linux, wsl, unknown → XDG
    xdg = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return os.path.join(xdg, "deepiri-polylogue")
