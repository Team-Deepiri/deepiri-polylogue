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
