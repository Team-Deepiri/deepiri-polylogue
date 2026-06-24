"""Service daemon configuration."""
from __future__ import annotations

import os
from pathlib import Path

from .platform_detect import data_dir

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7849
DEFAULT_BRIDGE_PORT = 7850


def service_host() -> str:
    return os.environ.get("POLYLOGUE_SERVICE_HOST", DEFAULT_HOST)


def service_port() -> int:
    return int(os.environ.get("POLYLOGUE_SERVICE_PORT", str(DEFAULT_PORT)))


def service_url() -> str:
    return f"http://{service_host()}:{service_port()}"


def bridge_host() -> str:
    return os.environ.get("POLYLOGUE_BRIDGE_HOST", DEFAULT_HOST)


def bridge_port() -> int:
    return int(os.environ.get("POLYLOGUE_BRIDGE_PORT", str(DEFAULT_BRIDGE_PORT)))


def bridge_url() -> str:
    return f"ws://{bridge_host()}:{bridge_port()}"


def pid_path() -> Path:
    return Path(data_dir()) / "service.pid"


def log_path() -> Path:
    return Path(data_dir()) / "service.log"
