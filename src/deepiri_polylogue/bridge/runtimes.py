"""Inject delegated prompts into local agent runtimes (any surface).

Cursor path (priority):
  1. Write to the agent TTY as keyboard input (same mechanics as the user typing).
  2. `agent --resume <chatId>` / `--continue` with the prompt as a new user turn.
  3. Spawn `agent -p --force` in the workspace cwd.
  4. Fall back to the on-disk delegate inbox.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .delegate import DelegateRequest, append_inbox, config_dir, format_injected_prompt

logger = logging.getLogger(__name__)

_AGENT_CMD_RE = re.compile(r"(?:^|/|\\)(agent|cursor-agent)(?:\s|$)")


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


def inject_user_text(text: str, *, cwd: Path | str | None = None, runtime: str | None = None) -> dict[str, Any]:
    """Inject free-form text as if the human typed it (bridge message → agent turn)."""
    runtime = runtime or detect_local_runtime()
    work = Path(cwd).resolve() if cwd else Path.cwd()
    # Synthetic delegate-shaped request for shared formatting / inbox
    fake = {
        "delegate_id": f"bridge-{int(time.time())}",
        "room": "local",
        "from": "polylogue-bridge",
        "to": "local",
        "on_behalf_of": os.environ.get("USER", "user"),
        "sender_provider": "polylogue",
        "prompt": text,
        "cwd": str(work),
        "ts": "",
        "sig": "",
    }
    if runtime == "cursor":
        return _inject_cursor(text if text.startswith("[") else f"[Polylogue peer]\n\n{text.strip()}", work, fake)
    if runtime == "claude":
        return _inject_claude(text, work, fake)  # type: ignore[arg-type]
    if runtime == "opencode":
        return _inject_opencode(text, work)
    return _inject_inbox(text, fake, runtime="inbox")  # type: ignore[arg-type]


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


def _cursor_inject_config() -> dict[str, Any]:
    path = config_dir() / "cursor_inject.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _workspace_inject_settings(cwd: Path) -> dict[str, Any]:
    cfg = _cursor_inject_config()
    workspaces = cfg.get("workspaces") or {}
    key = str(cwd.resolve())
    if key in workspaces:
        return dict(workspaces[key])
    # Longest matching prefix
    best: dict[str, Any] = {}
    best_len = -1
    for path, settings in workspaces.items():
        try:
            p = str(Path(path).resolve())
        except OSError:
            p = path
        if key.startswith(p) and len(p) > best_len:
            best = dict(settings)
            best_len = len(p)
    return best


def _list_agent_ttys() -> list[dict[str, str]]:
    """Return running cursor-agent / agent processes with their controlling TTYs."""
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid=,tty=,args="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    found: list[dict[str, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, tty, args = parts[0], parts[1], parts[2]
        if tty in ("?", "tty?", "-"):
            continue
        if not _AGENT_CMD_RE.search(args):
            continue
        if "deepiri-polylogue" in args or "polylogue" in args.split()[:3]:
            continue
        found.append({"pid": pid, "tty": tty, "args": args})
    return found


def _tty_device(tty: str) -> Path | None:
    tty = tty.strip()
    if not tty or tty in ("?", "-"):
        return None
    if tty.startswith("/"):
        path = Path(tty)
    elif tty.startswith("pts/"):
        path = Path("/dev") / tty
    else:
        path = Path("/dev") / tty
    return path if path.exists() else None


def _write_tty(tty: str, text: str) -> dict[str, Any]:
    """Type `text` into a PTY as if the user pressed keys + Enter."""
    dev = _tty_device(tty)
    if not dev:
        raise FileNotFoundError(f"TTY not found: {tty}")
    # Normalize newlines; end with Enter so the agent accepts the turn.
    body = text.replace("\r\n", "\n").rstrip("\n") + "\n"
    data = body.encode("utf-8", errors="replace")
    # Prefer writing through the device node (appears as keyboard to the session).
    fd = os.open(str(dev), os.O_WRONLY)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return {"runtime": "cursor", "method": "tty", "tty": str(dev), "bytes": len(data)}


def _resolve_tty(cwd: Path, settings: dict[str, Any]) -> str | None:
    env_tty = os.environ.get("POLYLOGUE_CURSOR_TTY", "").strip()
    if env_tty:
        return env_tty
    if settings.get("tty"):
        return str(settings["tty"])
    agents = _list_agent_ttys()
    if not agents:
        return None
    # Prefer an agent whose /proc cwd matches (when readable)
    cwd_s = str(cwd.resolve())
    matched: list[dict[str, str]] = []
    for row in agents:
        try:
            proc_cwd = os.readlink(f"/proc/{row['pid']}/cwd")
        except OSError:
            proc_cwd = ""
        if proc_cwd and (proc_cwd == cwd_s or cwd_s.startswith(proc_cwd + "/") or proc_cwd.startswith(cwd_s)):
            matched.append(row)
    if len(matched) == 1:
        return matched[0]["tty"]
    if len(matched) > 1:
        return matched[0]["tty"]
    # Single local agent TTY → use it
    if len(agents) == 1:
        return agents[0]["tty"]
    return None


def _inject_cursor(prompt: str, cwd: Path, req: DelegateRequest | dict[str, Any]) -> dict[str, Any]:
    settings = _workspace_inject_settings(cwd)
    mode = (
        os.environ.get("POLYLOGUE_CURSOR_INJECT", "").strip().lower()
        or str(settings.get("mode", "auto")).lower()
    )
    attempts: list[dict[str, Any]] = []

    # 1) TTY — same mechanics as user keyboard input in the agent terminal
    if mode in ("auto", "tty", "all"):
        tty = _resolve_tty(cwd, settings)
        if tty:
            try:
                result = _write_tty(tty, prompt)
                attempts.append(result)
                # Also mirror to inbox for audit
                if isinstance(req, DelegateRequest):
                    inbox = append_inbox(req.target, {"type": "delegate_tty", "tty": tty, "delegate_id": req.delegate_id})
                    result["inbox"] = str(inbox)
                if mode != "all":
                    return result
            except OSError as exc:
                attempts.append({"method": "tty", "error": str(exc), "tty": tty})
                logger.warning("cursor TTY inject failed (%s): %s", tty, exc)

    exe = shutil.which("agent") or shutil.which("cursor-agent")
    chat_id = os.environ.get("POLYLOGUE_CURSOR_CHAT_ID", "").strip() or str(settings.get("chat_id") or "")
    use_continue = (
        os.environ.get("POLYLOGUE_CURSOR_CONTINUE", "").strip() in ("1", "true", "yes")
        or bool(settings.get("continue"))
    )

    # 2) Resume / continue existing chat as a new user turn
    if exe and mode in ("auto", "resume", "continue", "all"):
        cmd: list[str] | None = None
        if chat_id and mode != "continue":
            cmd = [exe, "--resume", chat_id, "-p", "--force", prompt]
        elif use_continue or mode == "continue":
            cmd = [exe, "--continue", "-p", "--force", prompt]
        if cmd:
            logger.info("delegate inject cursor resume/continue: %s", cmd[0])
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            result = {
                "runtime": "cursor",
                "method": "resume" if chat_id else "continue",
                "pid": proc.pid,
                "chat_id": chat_id or None,
                "cmd": [cmd[0], *cmd[1:-1], "<prompt>"],
            }
            attempts.append(result)
            if mode != "all":
                return result

    # 3) Fresh agent print turn in workspace
    if exe and mode in ("auto", "spawn", "all"):
        cmd = [exe, "-p", "--force", prompt]
        logger.info("delegate inject cursor agent spawn: %s", exe)
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        result = {
            "runtime": "cursor",
            "method": "spawn",
            "pid": proc.pid,
            "cmd": [exe, "-p", "--force", "<prompt>"],
        }
        attempts.append(result)
        if mode != "all":
            return result

    # 4) Inbox fallback
    inbox_result = _inject_inbox(prompt, req, runtime="cursor")
    inbox_result["attempts"] = attempts
    return inbox_result


def _inject_claude(prompt: str, cwd: Path, req: DelegateRequest | dict[str, Any]) -> dict[str, Any]:
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


def _inject_inbox(
    prompt: str,
    req: DelegateRequest | dict[str, Any],
    *,
    runtime: str = "inbox",
) -> dict[str, Any]:
    if isinstance(req, DelegateRequest):
        target = req.target
        extra = {
            "type": "delegate_inbox",
            "delegate_id": req.delegate_id,
            "from": req.sender,
            "prompt": prompt,
            "cwd": req.cwd,
        }
    else:
        target = str(req.get("to") or "local")
        extra = {
            "type": "delegate_inbox",
            "delegate_id": req.get("delegate_id"),
            "from": req.get("from"),
            "prompt": prompt,
            "cwd": req.get("cwd"),
        }
    path = append_inbox(target, extra)
    logger.info("delegate inbox %s", path)
    return {"runtime": runtime, "method": "inbox", "inbox": str(path)}
