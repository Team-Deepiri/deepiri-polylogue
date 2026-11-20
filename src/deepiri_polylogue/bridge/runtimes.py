"""Inject prompts into local agent runtimes (Cursor, Claude, OpenCode, …).

Priority for every runtime:
  1. Write to the agent TTY as keyboard input (same mechanics as the user typing).
  2. Resume / continue the CLI session with the prompt as a new user turn.
  3. Spawn a one-shot CLI print turn in the workspace cwd.
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

# Match Cursor agent, Claude Code, OpenCode TUI processes on a PTY
_AGENT_CMD_RE = re.compile(
    r"(?:^|/|\\)(agent|cursor-agent|claude|opencode)(?:\s|$)",
    re.IGNORECASE,
)


def detect_local_runtime() -> str:
    forced = os.environ.get("POLYLOGUE_DELEGATE_RUNTIME", "").strip().lower()
    if forced:
        return forced
    # Prefer whatever is actually on a TTY right now
    ttys = _list_agent_ttys()
    for row in ttys:
        args = row["args"].lower()
        if "claude" in args.split()[0] or "/claude" in args:
            return "claude"
        if "opencode" in args:
            return "opencode"
        if "agent" in args or "cursor-agent" in args:
            return "cursor"
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
    return _inject_any(prompt, cwd, req, runtime=runtime)


def inject_user_text(text: str, *, cwd: Path | str | None = None, runtime: str | None = None) -> dict[str, Any]:
    """Inject free-form text as if the human typed it (bridge message → agent turn)."""
    runtime = runtime or detect_local_runtime()
    work = Path(cwd).resolve() if cwd else Path.cwd()
    body = text if text.startswith("[") else f"[Polylogue peer]\n\n{text.strip()}"
    fake: dict[str, Any] = {
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
    return _inject_any(body, work, fake, runtime=runtime)


def _inject_any(
    prompt: str,
    cwd: Path,
    req: DelegateRequest | dict[str, Any],
    *,
    runtime: str,
) -> dict[str, Any]:
    settings = _workspace_inject_settings(cwd)
    mode = (
        os.environ.get("POLYLOGUE_INJECT_MODE", "").strip().lower()
        or os.environ.get("POLYLOGUE_CURSOR_INJECT", "").strip().lower()
        or str(settings.get("mode", "auto")).lower()
    )
    attempts: list[dict[str, Any]] = []

    # Prefer configured chat/session resume BEFORE guessing a TTY — Composer/IDE
    # agents have no PTY; wrong-TTY inject is silent from the peer's point of view.
    has_session = bool(
        settings.get("chat_id")
        or settings.get("claude_session")
        or settings.get("session_id")
        or os.environ.get("POLYLOGUE_CURSOR_CHAT_ID")
        or os.environ.get("POLYLOGUE_CLAUDE_SESSION")
    )
    if has_session and mode in ("auto", "resume", "all"):
        cli = _cli_inject(prompt, cwd, settings, runtime=runtime, mode="resume")
        if cli and cli.get("method") in ("resume", "continue", "spawn"):
            attempts.append(cli)
            if mode != "all":
                return cli

    # TTY — interactive Claude / Cursor / OpenCode terminal sessions
    if mode in ("auto", "tty", "all"):
        tty = _resolve_tty(cwd, settings, preferred_runtime=runtime)
        if tty:
            try:
                result = _write_tty(tty, prompt)
                result["runtime"] = runtime
                attempts.append(result)
                if isinstance(req, DelegateRequest):
                    inbox = append_inbox(
                        req.target,
                        {"type": "delegate_tty", "tty": tty, "delegate_id": req.delegate_id},
                    )
                    result["inbox"] = str(inbox)
                if mode != "all":
                    return result
            except OSError as exc:
                attempts.append({"method": "tty", "error": str(exc), "tty": tty})
                logger.warning("%s TTY inject failed (%s): %s", runtime, tty, exc)

    # Resume / continue / spawn when no session pin or TTY worked
    if mode in ("auto", "resume", "continue", "spawn", "all"):
        cli = _cli_inject(prompt, cwd, settings, runtime=runtime, mode=mode)
        if cli:
            attempts.append(cli)
            if mode != "all" and cli.get("method") != "none":
                return cli
    inbox_result = _inject_inbox(prompt, req, runtime=runtime)
    inbox_result["attempts"] = attempts
    return inbox_result


def _cli_inject(
    prompt: str,
    cwd: Path,
    settings: dict[str, Any],
    *,
    runtime: str,
    mode: str,
) -> dict[str, Any] | None:
    if runtime == "cursor":
        return _cli_cursor(prompt, cwd, settings, mode=mode)
    if runtime == "claude":
        return _cli_claude(prompt, cwd, settings, mode=mode)
    if runtime == "opencode":
        return _cli_opencode(prompt, cwd, settings, mode=mode)
    return None


def _cli_cursor(prompt: str, cwd: Path, settings: dict[str, Any], *, mode: str) -> dict[str, Any] | None:
    exe = shutil.which("agent") or shutil.which("cursor-agent")
    if not exe:
        return None
    chat_id = (
        os.environ.get("POLYLOGUE_CURSOR_CHAT_ID", "").strip()
        or str(settings.get("chat_id") or "")
    )
    use_continue = (
        os.environ.get("POLYLOGUE_CURSOR_CONTINUE", "").strip() in ("1", "true", "yes")
        or bool(settings.get("continue"))
    )
    if chat_id and mode in ("auto", "resume", "all"):
        return _spawn([exe, "--resume", chat_id, "-p", "--force", prompt], cwd, "cursor", "resume", chat_id)
    if (use_continue or mode == "continue") and mode in ("auto", "continue", "all"):
        return _spawn([exe, "--continue", "-p", "--force", prompt], cwd, "cursor", "continue")
    if mode in ("auto", "spawn", "all"):
        return _spawn([exe, "-p", "--force", prompt], cwd, "cursor", "spawn")
    return None


def _cli_claude(prompt: str, cwd: Path, settings: dict[str, Any], *, mode: str) -> dict[str, Any] | None:
    exe = shutil.which("claude")
    if not exe:
        return None
    session = (
        os.environ.get("POLYLOGUE_CLAUDE_SESSION", "").strip()
        or str(settings.get("claude_session") or settings.get("session_id") or "")
    )
    use_continue = (
        os.environ.get("POLYLOGUE_CLAUDE_CONTINUE", "").strip() in ("1", "true", "yes")
        or bool(settings.get("continue"))
    )
    # Prefer bypass in sandboxed / already-trusted agent loops when env set
    skip = os.environ.get("POLYLOGUE_CLAUDE_SKIP_PERMISSIONS", "").strip() in ("1", "true", "yes")
    base_flags = ["-p"]
    if skip:
        base_flags.append("--dangerously-skip-permissions")

    if session and mode in ("auto", "resume", "all"):
        return _spawn([exe, "--resume", session, *base_flags, prompt], cwd, "claude", "resume", session)
    if (use_continue or mode == "continue") and mode in ("auto", "continue", "all"):
        return _spawn([exe, "--continue", *base_flags, prompt], cwd, "claude", "continue")
    if mode in ("auto", "spawn", "all"):
        return _spawn([exe, *base_flags, prompt], cwd, "claude", "spawn")
    return None


def _cli_opencode(prompt: str, cwd: Path, settings: dict[str, Any], *, mode: str) -> dict[str, Any] | None:
    exe = shutil.which("opencode")
    if not exe:
        return None
    # `opencode run` is the non-interactive message path
    if mode in ("auto", "spawn", "resume", "continue", "all"):
        return _spawn([exe, "run", "--dir", str(cwd), prompt], cwd, "opencode", "spawn")
    return None


def _spawn(
    cmd: list[str],
    cwd: Path,
    runtime: str,
    method: str,
    session: str | None = None,
) -> dict[str, Any]:
    logger.info("delegate inject %s %s: %s", runtime, method, cmd[0])
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return {
        "runtime": runtime,
        "method": method,
        "pid": proc.pid,
        "session": session,
        "cmd": [cmd[0], *[c if c != cmd[-1] else "<prompt>" for c in cmd[1:]]],
    }


def _cursor_inject_config() -> dict[str, Any]:
    path = config_dir() / "cursor_inject.json"
    # Also accept generic name
    alt = config_dir() / "inject.json"
    for candidate in (path, alt):
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
    return {}


def _workspace_inject_settings(cwd: Path) -> dict[str, Any]:
    cfg = _cursor_inject_config()
    workspaces = cfg.get("workspaces") or {}
    key = str(cwd.resolve())
    if key in workspaces:
        return dict(workspaces[key])
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
        # Skip polylogue itself
        low = args.lower()
        if "deepiri-polylogue" in low or "polylogue" in args.split()[:3]:
            continue
        kind = "cursor"
        if re.search(r"(?:^|/|\\)claude(?:\s|$)", args, re.I):
            kind = "claude"
        elif re.search(r"(?:^|/|\\)opencode(?:\s|$)", args, re.I):
            kind = "opencode"
        found.append({"pid": pid, "tty": tty, "args": args, "kind": kind})
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
    body = text.replace("\r\n", "\n").rstrip("\n") + "\n"
    data = body.encode("utf-8", errors="replace")
    fd = os.open(str(dev), os.O_WRONLY)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return {"method": "tty", "tty": str(dev), "bytes": len(data)}


def _resolve_tty(
    cwd: Path,
    settings: dict[str, Any],
    *,
    preferred_runtime: str | None = None,
) -> str | None:
    env_tty = (
        os.environ.get("POLYLOGUE_INJECT_TTY", "").strip()
        or os.environ.get("POLYLOGUE_CURSOR_TTY", "").strip()
        or os.environ.get("POLYLOGUE_CLAUDE_TTY", "").strip()
    )
    if env_tty:
        return env_tty
    if settings.get("tty"):
        return str(settings["tty"])
    agents = _list_agent_ttys()
    if not agents:
        return None
    cwd_s = str(cwd.resolve())
    matched: list[dict[str, str]] = []
    for row in agents:
        if preferred_runtime and row.get("kind") and row["kind"] != preferred_runtime:
            # Keep as secondary preference; don't exclude entirely if nothing else matches
            pass
        try:
            proc_cwd = os.readlink(f"/proc/{row['pid']}/cwd")
        except OSError:
            proc_cwd = ""
        if proc_cwd and (proc_cwd == cwd_s or cwd_s.startswith(proc_cwd + "/") or proc_cwd.startswith(cwd_s)):
            matched.append(row)
    # Prefer matching runtime kind among cwd matches
    if preferred_runtime and matched:
        kind_hit = [r for r in matched if r.get("kind") == preferred_runtime]
        if kind_hit:
            return kind_hit[0]["tty"]
        return matched[0]["tty"]
    if matched:
        return matched[0]["tty"]
    if preferred_runtime:
        kind_hit = [r for r in agents if r.get("kind") == preferred_runtime]
        if len(kind_hit) == 1:
            return kind_hit[0]["tty"]
        if kind_hit:
            return kind_hit[0]["tty"]
    if len(agents) == 1:
        return agents[0]["tty"]
    return None


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
