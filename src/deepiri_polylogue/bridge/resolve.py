"""Infer bridge room + participant id from repo registry and runtime."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import registry as reg


@dataclass(frozen=True)
class BridgeContext:
    repo_root: Path
    room: str
    participant_id: str
    provider: str
    peers: list[str]
    session_root: Path | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "repo_root": str(self.repo_root),
            "room": self.room,
            "participant_id": self.participant_id,
            "provider": self.provider,
            "peers": self.peers,
            "session_root": str(self.session_root) if self.session_root else None,
        }


def find_repo_root(start: Path | None = None) -> Path:
    start = (start or Path.cwd()).resolve()
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return start


def _process_tree_blob() -> str:
    parts: list[str] = []
    pid = os.getpid()
    seen: set[int] = set()
    for _ in range(20):
        if pid in seen or pid <= 0:
            break
        seen.add(pid)
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        comm_path = Path(f"/proc/{pid}/comm")
        try:
            if cmdline_path.is_file():
                parts.append(cmdline_path.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore"))
            elif comm_path.is_file():
                parts.append(comm_path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            pass
        try:
            pid = int(Path(f"/proc/{pid}/stat").read_text().split()[3])
        except (OSError, ValueError, IndexError):
            break
    return " ".join(parts).lower()


def detect_provider() -> str:
    """Infer which LLM surface is calling us (for roster / bridge identity).

    Order: explicit POLYLOGUE_PROVIDER → well-known env vars → process tree heuristics.
    """
    explicit = os.environ.get("POLYLOGUE_PROVIDER", "").strip().lower()
    if explicit:
        return explicit

    # Cursor
    if os.environ.get("CURSOR_AGENT") == "1" or os.environ.get("CURSOR_TRACE_ID"):
        return "cursor"

    # OpenCode
    for key in ("OPENCODE", "OPENCODE_SESSION", "OPENCODE_CONFIG"):
        if os.environ.get(key):
            return "opencode"

    # Claude Code / Anthropic
    for key in ("CLAUDE_CODE", "CLAUDE_CODE_ENTRYPOINT", "ANTHROPIC_API_KEY"):
        # Prefer process-tree for API key alone (too generic); env flags are stronger.
        if key != "ANTHROPIC_API_KEY" and os.environ.get(key):
            return "claude"

    # Google Antigravity / Gemini CLI / Gemini Code Assist
    for key in (
        "ANTIGRAVITY",
        "ANTIGRAVITY_AGENT",
        "GEMINI_CLI",
        "GEMINI_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
    ):
        if key in ("GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI"):
            continue  # too generic alone
        if os.environ.get(key):
            if "antigravity" in key.lower():
                return "antigravity"
            return "gemini"

    # Codex
    if os.environ.get("CODEX_HOME") or os.environ.get("OPENAI_CODEX"):
        return "codex"

    # Windsurf
    if os.environ.get("WINDSURF") or os.environ.get("CODEIUM_WINDSURF"):
        return "windsurf"

    blob = _process_tree_blob()
    if "antigravity" in blob or "agy " in blob or "/agy" in blob:
        return "antigravity"
    if "opencode" in blob:
        return "opencode"
    if "cursor-agent" in blob or "cursor agent" in blob or "/.cursor/" in blob:
        return "cursor"
    if "claude" in blob:
        return "claude"
    if "codex" in blob:
        return "codex"
    if "gemini" in blob:
        return "gemini"
    if "windsurf" in blob:
        return "windsurf"
    if "vscode" in blob or "code-insiders" in blob:
        return "vscode"
    return "unknown"


def _session_from_registry(repo_root: Path) -> tuple[str, Path | None]:
    entry = reg.lookup_workspace(repo_root)
    if entry:
        root = reg.resolve_session_root(repo_root)
        return str(entry.get("session", "default")), root
    return "default", None


def _load_roster(session_root: Path | None) -> list[dict[str, Any]]:
    if session_root is None:
        return []
    path = session_root / "participants.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data.get("participants", []))
    except (json.JSONDecodeError, OSError):
        return []


def _bridge_connected(room: str) -> set[str]:
    from ..service_config import service_host, service_port

    host = os.environ.get("POLYLOGUE_SERVICE_HOST", service_host())
    port = os.environ.get("POLYLOGUE_SERVICE_PORT", str(service_port()))
    url = f"http://{host}:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return set(data.get("bridge", {}).get("rooms", {}).get(room, []))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return set()


def _pick_participant_id(provider: str, roster: list[dict[str, Any]], connected: set[str]) -> str:
    if not roster:
        return provider if provider != "unknown" else f"agent-{os.getpid()}"

    by_provider = [p for p in roster if str(p.get("provider", "")).lower() == provider]
    if by_provider:
        for p in by_provider:
            pid = str(p["id"])
            if pid not in connected:
                return pid
        return str(by_provider[0]["id"])

    if len(roster) == 1:
        return str(roster[0]["id"])

    roster_ids = {str(p["id"]) for p in roster}
    free = [p for p in roster if str(p["id"]) not in connected]
    if len(free) == 1:
        return str(free[0]["id"])

    if provider != "unknown" and provider not in connected and provider not in roster_ids:
        return provider
    if free:
        return str(free[0]["id"])
    return provider if provider != "unknown" else str(roster[0]["id"])


def _peers(participant_id: str, roster: list[dict[str, Any]], connected: set[str]) -> list[str]:
    roster_ids = [str(p["id"]) for p in roster if str(p["id"]) != participant_id]
    live = [p for p in roster_ids if p in connected and p != participant_id]
    if live:
        return live
    return roster_ids


def resolve_bridge_context(
    cwd: Path | None = None,
    *,
    participant_id: str | None = None,
    room: str | None = None,
) -> BridgeContext:
    repo_root = find_repo_root(cwd)
    detected_provider = detect_provider()

    resolved_room, session_root = _session_from_registry(repo_root)
    if room:
        resolved_room = room

    roster = _load_roster(session_root)
    connected = _bridge_connected(resolved_room)

    if participant_id:
        resolved_id = participant_id
    else:
        resolved_id = _pick_participant_id(detected_provider, roster, connected)

    peer_ids = _peers(resolved_id, roster, connected)
    return BridgeContext(
        repo_root=repo_root,
        room=resolved_room,
        participant_id=resolved_id,
        provider=detected_provider,
        peers=peer_ids,
        session_root=session_root,
    )


def resolve_send_target(
    ctx: BridgeContext,
    explicit_to: str | None = None,
    broadcast: bool = False,
) -> str | None:
    if broadcast:
        return None
    if explicit_to:
        return explicit_to
    if len(ctx.peers) == 1:
        return ctx.peers[0]
    live = _bridge_connected(ctx.room) - {ctx.participant_id}
    if len(live) == 1:
        return next(iter(live))
    return None
