"""Session bootstrap for the Polylogue MCP: ensure service, join, listen, inbox."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .. import registry as reg
from .. import workspace as ws
from ..bridge.client import bridge_status, send_message
from ..bridge.listener import state_paths
from ..bridge.resolve import (
    detect_provider,
    find_repo_root,
    resolve_bridge_context,
    resolve_send_target,
)
from ..journal import append_event, journal_path, tail_events
from ..models import Participant, event_line, utc_now_iso
from ..pack import render_sync_pack
from ..participants import load_participants, touch_participant, upsert_participant
from ..paths import polylogue_root
from ..service_client import ensure_service, health, is_running
from ..store import init_session, load_meta


INSTRUCTIONS = """\
You are connected to Polylogue: a shared journal + live bridge so multiple LLM agents
(Cursor, Claude, OpenCode, Codex, Gemini, etc.) stay mutually aware on the same mission.

Cohesion loop (follow every turn you participate):
1. Call polylogue_ensure once per session (auto-starts the daemon + bridge listener).
2. Call polylogue_sync_pack and polylogue_bridge_inbox before substantive replies.
3. Use polylogue_peers to see other live agents (any provider) in this room.
4. Log material conclusions with polylogue_say (durable) and/or polylogue_bridge_send (live).
5. Prefer auto-target when a single peer is live; use broadcast=true for the whole room.
6. Never put secrets in journal or bridge messages.
"""


def resolve_cwd(cwd: str | None = None) -> Path:
    if cwd:
        return Path(cwd).expanduser().resolve()
    env = os.environ.get("POLYLOGUE_MCP_CWD", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd().resolve()


def session_name_for(cwd: Path, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    repo = find_repo_root(cwd)
    name = repo.name.strip() or "default"
    return name[:80] or "default"


def listener_pid_path(participant_id: str) -> Path:
    log_path, _ = state_paths(participant_id)
    return log_path.with_name(log_path.name.replace(".log", "-listen.pid"))


def inbox_offset_path(participant_id: str) -> Path:
    log_path, _ = state_paths(participant_id)
    return log_path.with_name(log_path.name.replace(".log", "-inbox.offset"))


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def listener_running(participant_id: str) -> bool:
    path = listener_pid_path(participant_id)
    if not path.is_file():
        return False
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if not _pid_alive(pid):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def start_listener(
    cwd: Path,
    *,
    participant_id: str,
    room: str | None = None,
) -> dict[str, Any]:
    if listener_running(participant_id):
        return {"started": False, "already_running": True, "pid_file": str(listener_pid_path(participant_id))}

    log_path, outbox = state_paths(participant_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    outbox.touch(exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "deepiri_polylogue",
        "--cwd",
        str(cwd),
        "bridge",
        "listen",
        "--id",
        participant_id,
    ]
    if room:
        cmd.extend(["--room", room])

    try:
        errf = open(log_path, "ab")  # noqa: SIM115 — handed to detached child
    except OSError:
        errf = subprocess.DEVNULL

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=errf,
            stderr=errf,
            start_new_session=True,
        )
    except OSError as exc:
        return {"started": False, "error": str(exc)}

    pid_path = listener_pid_path(participant_id)
    try:
        pid_path.write_text(str(proc.pid) + "\n", encoding="utf-8")
    except OSError:
        pass
    return {"started": True, "pid": proc.pid, "pid_file": str(pid_path), "log": str(log_path)}


def ensure_session(
    *,
    cwd: str | None = None,
    session: str | None = None,
    participant_id: str | None = None,
    label: str | None = None,
    start_listen: bool = True,
) -> dict[str, Any]:
    work = resolve_cwd(cwd)
    name = session_name_for(work, session)

    service_ok = ensure_service()
    entry = reg.lookup_workspace(work)
    if entry is None:
        root = init_session(Path("."), name, workspace=work, use_service=True)
    else:
        root = polylogue_root(work)
        try:
            load_meta(root)
        except FileNotFoundError:
            root = init_session(Path("."), name, workspace=work, use_service=True)

    ws.workspace_init(root)
    ctx = resolve_bridge_context(work, participant_id=participant_id)
    provider = ctx.provider if ctx.provider != "unknown" else detect_provider()
    pid = ctx.participant_id
    display = label or f"{provider} agent"

    upsert_participant(
        root,
        Participant(id=pid, label=display, provider=provider, last_seen=utc_now_iso()),
    )
    row = ws.upsert_actor(
        root,
        actor_id=pid,
        kind="participant",
        parent_id=None,
        label=display,
        state="idle",
        cwd=str(work),
        paths=None,
        note="mcp active",
    )

    listen_info: dict[str, Any] = {"started": False, "skipped": True}
    if start_listen:
        listen_info = start_listener(work, participant_id=pid, room=ctx.room)

    # Refresh peers after listener may have connected.
    ctx = resolve_bridge_context(work, participant_id=pid, room=ctx.room)
    h = health() if is_running() else None
    return {
        "ok": True,
        "cwd": str(work),
        "session": name,
        "session_root": str(root),
        "room": ctx.room,
        "participant_id": pid,
        "provider": provider,
        "peers": ctx.peers,
        "service_ok": service_ok,
        "service_running": is_running(),
        "health": h,
        "presence": row,
        "listener": listen_info,
    }


def whoami(*, cwd: str | None = None, participant_id: str | None = None, room: str | None = None) -> dict[str, Any]:
    work = resolve_cwd(cwd)
    ctx = resolve_bridge_context(work, participant_id=participant_id, room=room)
    return ctx.to_json()


def peers(*, cwd: str | None = None, participant_id: str | None = None) -> dict[str, Any]:
    work = resolve_cwd(cwd)
    ctx = resolve_bridge_context(work, participant_id=participant_id)
    root = polylogue_root(work)
    roster: list[dict[str, Any]] = []
    try:
        load_meta(root)
        roster = [p.to_json() for p in load_participants(root)]
    except FileNotFoundError:
        pass

    live: set[str] = set()
    h = health()
    if h and h.get("bridge"):
        rooms = h["bridge"].get("rooms") or {}
        live = set(rooms.get(ctx.room, []))
    status = bridge_status()
    if status.get("rooms"):
        live |= set(status["rooms"].get(ctx.room, []))

    live.discard(ctx.participant_id)
    return {
        "room": ctx.room,
        "self": ctx.participant_id,
        "provider": ctx.provider,
        "live_peers": sorted(live),
        "roster_peers": ctx.peers,
        "roster": roster,
        "bridge": status,
    }


def bridge_status_info() -> dict[str, Any]:
    out = bridge_status()
    if is_running():
        h = health()
        if h and h.get("bridge"):
            out = dict(h["bridge"])
            out["from"] = "service_health"
    return out


def bridge_send(
    text: str,
    *,
    cwd: str | None = None,
    participant_id: str | None = None,
    to: str | None = None,
    broadcast: bool = False,
) -> dict[str, Any]:
    if not text.strip():
        raise ValueError("text must be non-empty")
    ensure_service()
    work = resolve_cwd(cwd)
    ctx = resolve_bridge_context(work, participant_id=participant_id)
    target = resolve_send_target(ctx, explicit_to=to, broadcast=broadcast)
    send_message(ctx.room, ctx.participant_id, text, to=target)
    return {
        "ok": True,
        "room": ctx.room,
        "from": ctx.participant_id,
        "to": target,
        "broadcast": broadcast or target is None,
        "text": text,
    }


def bridge_inbox(
    *,
    cwd: str | None = None,
    participant_id: str | None = None,
    limit: int = 50,
    reset: bool = False,
) -> dict[str, Any]:
    work = resolve_cwd(cwd)
    ctx = resolve_bridge_context(work, participant_id=participant_id)
    log_path, _ = state_paths(ctx.participant_id)
    offset_path = inbox_offset_path(ctx.participant_id)

    if reset and offset_path.is_file():
        try:
            offset_path.unlink()
        except OSError:
            pass

    offset = 0
    if offset_path.is_file():
        try:
            offset = int(offset_path.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            offset = 0

    if not log_path.is_file():
        return {
            "participant_id": ctx.participant_id,
            "messages": [],
            "count": 0,
            "offset": offset,
            "log": str(log_path),
            "note": "no listener log yet — call polylogue_ensure first",
        }

    raw = log_path.read_bytes()
    if offset > len(raw):
        offset = 0
    chunk = raw[offset:].decode("utf-8", errors="replace")
    messages: list[dict[str, Any]] = []
    for line in chunk.splitlines():
        line = line.strip()
        if not line.startswith("recv:"):
            continue
        payload = line[len("recv:") :].strip()
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            messages.append({"raw": payload})
            continue
        if isinstance(data, dict):
            messages.append(data)
        else:
            messages.append({"raw": payload})
        if len(messages) >= limit:
            break

    new_offset = len(raw)
    try:
        offset_path.parent.mkdir(parents=True, exist_ok=True)
        offset_path.write_text(str(new_offset) + "\n", encoding="utf-8")
    except OSError:
        pass

    return {
        "participant_id": ctx.participant_id,
        "messages": messages,
        "count": len(messages),
        "offset": new_offset,
        "log": str(log_path),
    }


def require_root(cwd: str | None = None) -> Path:
    work = resolve_cwd(cwd)
    root = polylogue_root(work)
    load_meta(root)
    ws.workspace_init(root)
    return root


def do_join(
    *,
    participant_id: str,
    label: str,
    provider: str = "unknown",
    cwd: str | None = None,
) -> dict[str, Any]:
    root = require_root(cwd)
    people = upsert_participant(
        root,
        Participant(id=participant_id, label=label, provider=provider, last_seen=utc_now_iso()),
    )
    return {"ok": True, "participant_id": participant_id, "roster_size": len(people)}


def do_say(
    text: str,
    *,
    participant_id: str | None = None,
    role: str = "assistant",
    cwd: str | None = None,
) -> dict[str, Any]:
    root = require_root(cwd)
    work = resolve_cwd(cwd)
    pid = participant_id or resolve_bridge_context(work).participant_id
    touch_participant(root, pid)
    ev = event_line(type="utterance", participant_id=pid, role=role, text=text)
    append_event(root, ev)
    return {"ok": True, "event": ev}


def do_handoff(
    text: str,
    *,
    next_participant: str,
    participant_id: str | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    root = require_root(cwd)
    work = resolve_cwd(cwd)
    pid = participant_id or resolve_bridge_context(work).participant_id
    touch_participant(root, pid)
    ev = event_line(
        type="handoff",
        participant_id=pid,
        text=text,
        next_participant=next_participant,
    )
    append_event(root, ev)
    return {"ok": True, "event": ev}


def do_snapshot(
    summary: str,
    *,
    participant_id: str | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    root = require_root(cwd)
    work = resolve_cwd(cwd)
    pid = participant_id or resolve_bridge_context(work).participant_id
    if pid:
        touch_participant(root, pid)
    ev = event_line(type="snapshot", participant_id=pid, summary=summary)
    append_event(root, ev)
    return {"ok": True, "event": ev}


def do_system(text: str, *, cwd: str | None = None) -> dict[str, Any]:
    root = require_root(cwd)
    ev = event_line(type="system", text=text)
    append_event(root, ev)
    return {"ok": True, "event": ev}


def do_tail(*, lines: int = 40, cwd: str | None = None) -> dict[str, Any]:
    root = require_root(cwd)
    events = tail_events(root, lines=lines)
    return {"count": len(events), "events": events}


def do_status(*, cwd: str | None = None) -> dict[str, Any]:
    root = require_root(cwd)
    return {
        "root": str(root),
        "meta": load_meta(root),
        "participants": [p.to_json() for p in load_participants(root)],
        "journal": str(journal_path(root)),
        "context": str(ws.context_path(root)),
        "memory": str(ws.memory_path(root)),
        "presence": str(ws.presence_path(root)),
    }


def do_sync_pack(
    *,
    lines: int = 40,
    context_bytes: int = 24_000,
    memory_bytes: int = 12_000,
    cwd: str | None = None,
) -> str:
    root = require_root(cwd)
    return render_sync_pack(
        root,
        lines=lines,
        context_max_bytes=context_bytes,
        memory_max_bytes=memory_bytes,
    )


def presence_list(*, cwd: str | None = None) -> dict[str, Any]:
    root = require_root(cwd)
    return ws.load_presence(root)


def presence_set(
    *,
    actor_id: str,
    state: str = "idle",
    label: str | None = None,
    cwd_path: str | None = None,
    note: str | None = None,
    path_specs: list[str] | None = None,
    journal: bool = True,
    cwd: str | None = None,
) -> dict[str, Any]:
    root = require_root(cwd)
    if state not in ("idle", "reading", "editing"):
        raise ValueError("state must be idle|reading|editing")
    paths = None
    if path_specs:
        paths = [ws.parse_path_role(s) for s in path_specs]
    row = ws.upsert_actor(
        root,
        actor_id=actor_id,
        kind="participant",
        parent_id=None,
        label=label,
        state=state,  # type: ignore[arg-type]
        cwd=cwd_path,
        paths=paths,
        note=note,
    )
    if journal:
        touch_participant(root, actor_id)
        snap = {
            "kind": row.get("kind"),
            "state": row.get("state"),
            "paths": row.get("paths"),
            "cwd": row.get("cwd"),
            "note": (row.get("note") or "")[:200],
        }
        append_event(
            root,
            event_line(
                type="presence",
                participant_id=actor_id,
                summary=json.dumps(snap, ensure_ascii=False),
            ),
        )
    return row


def presence_clear(*, actor_id: str, cwd: str | None = None) -> dict[str, Any]:
    root = require_root(cwd)
    ok = ws.clear_actor(root, actor_id)
    if not ok:
        raise ValueError(f"no actor {actor_id!r}")
    return {"ok": True, "cleared": actor_id}


def context_show(*, max_bytes: int = 24_000, cwd: str | None = None) -> str:
    root = require_root(cwd)
    return ws.read_text_tail(ws.context_path(root), max_bytes=max_bytes)


def context_append(text: str, *, cwd: str | None = None) -> dict[str, Any]:
    root = require_root(cwd)
    cp = ws.context_path(root)
    prev = cp.read_bytes().decode("utf-8", errors="replace") if cp.is_file() else ""
    block = prev.rstrip() + "\n\n" + text.strip() + "\n"
    ws.atomic_write_text(cp, block)
    return {"ok": True, "path": str(cp), "bytes": len(block.encode("utf-8"))}


def memory_show(*, max_bytes: int = 12_000, cwd: str | None = None) -> str:
    root = require_root(cwd)
    return ws.read_text_tail(ws.memory_path(root), max_bytes=max_bytes)


def memory_append(text: str, *, cwd: str | None = None) -> dict[str, Any]:
    root = require_root(cwd)
    mp = ws.memory_path(root)
    prev = mp.read_bytes().decode("utf-8", errors="replace") if mp.is_file() else ""
    block = prev.rstrip() + "\n" + text.strip() + "\n"
    ws.atomic_write_text(mp, block)
    return {"ok": True, "path": str(mp), "bytes": len(block.encode("utf-8"))}
