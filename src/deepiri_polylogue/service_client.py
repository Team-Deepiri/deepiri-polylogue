"""HTTP client for the polylogue background service."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import registry as reg
from .service_config import service_url


def _request(method: str, path: str, body: dict | None = None, timeout: float = 2.0) -> dict[str, Any] | None:
    url = service_url().rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def health() -> dict[str, Any] | None:
    return _request("GET", "/health")


def is_running() -> bool:
    h = health()
    return bool(h and h.get("ok"))


def resolve_root(cwd: Path | None = None) -> Path | None:
    cwd = cwd or Path.cwd()
    result = _request("GET", "/resolve?" + urllib.parse.urlencode({"cwd": str(cwd.resolve())}))
    if result and result.get("session_root"):
        return Path(result["session_root"])
    return reg.resolve_session_root(cwd)


def register_workspace(cwd: Path, session: str) -> dict[str, Any]:
    if is_running():
        result = _request("POST", "/register", {"cwd": str(cwd.resolve()), "session": session})
        if result and result.get("session_root"):
            return result
    return reg.register_workspace(cwd, session)


def ensure_service(wait_s: float = 6.0) -> bool:
    """Ensure the shared coordination daemon is running, auto-starting it if not.

    This is what lets two independent agent sessions become reachable to each other with
    zero manual setup: the first participant to touch the bridge brings the shared daemon
    up (detached, so it outlives this CLI invocation), and subsequent sessions just connect
    to the same daemon. If two sessions race, only one wins the port bind and the other
    simply reuses it. Best-effort: returns True if the daemon is (or becomes) healthy.
    """
    import subprocess
    import sys
    import time

    if is_running():
        return True

    from .service_config import log_path

    try:
        logf: Any = open(log_path(), "ab")  # noqa: SIM115 — handed to the detached child
    except OSError:
        logf = subprocess.DEVNULL

    try:
        subprocess.Popen(
            [sys.executable, "-m", "deepiri_polylogue.service_daemon"],
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=logf,
            start_new_session=True,  # detach so the daemon survives this process exiting
        )
    except OSError:
        return False

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if is_running():
            return True
        time.sleep(0.15)
    return is_running()
