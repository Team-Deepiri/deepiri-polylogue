"""Inject delegated prompts into local agent runtimes (any surface)."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .delegate import DelegateRequest, append_inbox, format_injected_prompt

logger = logging.getLogger(__name__)


def detect_local_runtime() -> str:
    forced = os.environ.get("POLYLOGUE_DELEGATE_RUNTIME", "").strip().lower()
    if forced:
        return forced
    if shutil.which("opencode"):
        return "opencode"
    if shutil.which("agent") or shutil.which("cursor-agent"):
        return "cursor"
    if os.environ.get("CURSOR_AGENT") == "1":
        return "cursor"
    if shutil.which("claude"):
        return "claude"
    return "inbox"


def inject_delegate(req: DelegateRequest | dict[str, Any], *, runtime: str | None = None) -> dict[str, Any]:
    if isinstance(req, dict):
        req = DelegateRequest(
            delegate_id=str(req["delegate_id"]),
            room=str(req["room"]),
            sender=str(req["from"]),
            target=str(req["to"]),
            on_behalf_of=str(req["on_behalf_of"]),
            sender_provider=str(req.get("sender_provider", "unknown")),
            prompt=str(req["prompt"]),
            cwd=str(req["cwd"]),
            ts=str(req.get("ts", "")),
            signature=str(req.get("sig", "")),
        )
    runtime = runtime or detect_local_runtime()
    prompt = format_injected_prompt(req)
    cwd = Path(req.cwd)

    if runtime == "opencode":
        return _inject_opencode(prompt, cwd)
    if runtime == "cursor":
        return _inject_cursor(prompt, cwd, req)
    if runtime == "claude":
        return _inject_claude(prompt, cwd, req)
    return _inject_inbox(prompt, req)


def _inject_opencode(prompt: str, cwd: Path) -> dict[str, Any]:
    exe = shutil.which("opencode")
    if not exe:
        raise RuntimeError("opencode not found on PATH")
    cmd = [exe, "run", "--dir", str(cwd), prompt]
    logger.info("delegate inject opencode: %s", " ".join(cmd[:4]))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return {"runtime": "opencode", "pid": proc.pid, "cmd": cmd[:3] + ["<prompt>"]}


def _inject_cursor(prompt: str, cwd: Path, req: DelegateRequest) -> dict[str, Any]:
    exe = shutil.which("agent") or shutil.which("cursor-agent")
    if exe:
        cmd = [exe, prompt]
        logger.info("delegate inject cursor agent: %s", exe)
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return {"runtime": "cursor", "pid": proc.pid, "cmd": [exe, "<prompt>"]}
    return _inject_inbox(prompt, req, runtime="cursor")


def _inject_claude(prompt: str, cwd: Path, req: DelegateRequest) -> dict[str, Any]:
    exe = shutil.which("claude")
    if not exe:
        return _inject_inbox(prompt, req, runtime="claude")
    cmd = [exe, "-p", prompt]
    logger.info("delegate inject claude: %s", exe)
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return {"runtime": "claude", "pid": proc.pid, "cmd": [exe, "-p", "<prompt>"]}


def _inject_inbox(prompt: str, req: DelegateRequest, *, runtime: str = "inbox") -> dict[str, Any]:
    path = append_inbox(
        req.target,
        {
            "type": "delegate_inbox",
            "delegate_id": req.delegate_id,
            "from": req.sender,
            "prompt": prompt,
            "cwd": req.cwd,
        },
    )
    logger.info("delegate inbox %s", path)
    return {"runtime": runtime, "inbox": str(path)}
