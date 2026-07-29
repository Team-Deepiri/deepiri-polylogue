"""MCP server exposing Polylogue journal + bridge tools."""
from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

from .. import __version__
from . import session as s

SERVER_INSTRUCTIONS = s.INSTRUCTIONS


def create_server() -> MCPServer[Any]:
    mcp = MCPServer(
        "polylogue_mcp",
        title="Polylogue",
        description="Filesystem-first shared journal and live bridge for multi-LLM cohesion.",
        instructions=SERVER_INSTRUCTIONS,
        version=__version__,
    )

    @mcp.tool()
    def polylogue_ensure(
        cwd: str | None = None,
        session: str | None = None,
        participant_id: str | None = None,
        label: str | None = None,
        start_listen: bool = True,
    ) -> dict[str, Any]:
        """Bootstrap Polylogue for this agent: start the shared daemon, init/join the session,
        mark presence active, and spawn a detached bridge listener so peers can reach you.

        Call once per agent session before other polylogue tools. Returns room, participant id,
        provider, live peers, and listener status.
        """
        try:
            return s.ensure_session(
                cwd=cwd,
                session=session,
                participant_id=participant_id,
                label=label,
                start_listen=start_listen,
            )
        except Exception as exc:  # noqa: BLE001 — surface actionable MCP errors
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_whoami(
        cwd: str | None = None,
        participant_id: str | None = None,
        room: str | None = None,
    ) -> dict[str, Any]:
        """Resolve this agent's bridge room, participant id, detected provider, and peer list."""
        try:
            return s.whoami(cwd=cwd, participant_id=participant_id, room=room)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_peers(
        cwd: str | None = None,
        participant_id: str | None = None,
    ) -> dict[str, Any]:
        """Scan for other agents: live bridge-connected peers plus the full roster (any provider)."""
        try:
            return s.peers(cwd=cwd, participant_id=participant_id)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_bridge_status() -> dict[str, Any]:
        """Return live bridge room membership and connection counts from the local daemon."""
        try:
            return s.bridge_status_info()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_bridge_send(
        text: str,
        cwd: str | None = None,
        participant_id: str | None = None,
        to: str | None = None,
        broadcast: bool = False,
    ) -> dict[str, Any]:
        """Send a live bridge message to a peer (or broadcast to the room).

        If `to` is omitted and exactly one live peer exists, that peer is auto-targeted.
        Set broadcast=true to fan out to everyone. Also prefer polylogue_say for durable history.
        """
        try:
            return s.bridge_send(
                text,
                cwd=cwd,
                participant_id=participant_id,
                to=to,
                broadcast=broadcast,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_bridge_inbox(
        cwd: str | None = None,
        participant_id: str | None = None,
        limit: int = 50,
        reset: bool = False,
    ) -> dict[str, Any]:
        """Read unread inbound bridge messages since the last poll (from the listener log).

        Call at the start of a turn. Set reset=true to re-read from the beginning of the log.
        """
        try:
            return s.bridge_inbox(
                cwd=cwd,
                participant_id=participant_id,
                limit=limit,
                reset=reset,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_sync_pack(
        cwd: str | None = None,
        lines: int = 40,
        context_bytes: int = 24_000,
        memory_bytes: int = 12_000,
    ) -> str:
        """Render the full awareness pack (journal, roster, presence, context, memory) as Markdown.

        Load this before substantive replies so you share the same picture as peer agents.
        """
        try:
            return s.do_sync_pack(
                cwd=cwd,
                lines=lines,
                context_bytes=context_bytes,
                memory_bytes=memory_bytes,
            )
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"

    @mcp.tool()
    def polylogue_join(
        participant_id: str,
        label: str,
        provider: str = "unknown",
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Register or update a participant on the session roster."""
        try:
            return s.do_join(
                participant_id=participant_id,
                label=label,
                provider=provider,
                cwd=cwd,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_say(
        text: str,
        participant_id: str | None = None,
        role: str = "assistant",
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Append an utterance to the durable journal (preferred for lasting conclusions)."""
        try:
            return s.do_say(text, participant_id=participant_id, role=role, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_handoff(
        text: str,
        next_participant: str,
        participant_id: str | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Log a structured handoff to another participant in the journal."""
        try:
            return s.do_handoff(
                text,
                next_participant=next_participant,
                participant_id=participant_id,
                cwd=cwd,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_snapshot(
        summary: str,
        participant_id: str | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Log a short state snapshot (e.g. tests green at commit abc) to the journal."""
        try:
            return s.do_snapshot(summary, participant_id=participant_id, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_system(text: str, cwd: str | None = None) -> dict[str, Any]:
        """Append a system/meta line to the journal."""
        try:
            return s.do_system(text, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_tail(lines: int = 40, cwd: str | None = None) -> dict[str, Any]:
        """Return the last N journal events as structured JSON."""
        try:
            return s.do_tail(lines=lines, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_status(cwd: str | None = None) -> dict[str, Any]:
        """Show session meta, roster, and key file paths."""
        try:
            return s.do_status(cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_presence_list(cwd: str | None = None) -> dict[str, Any]:
        """List who is reading/editing what (presence plane, including subagents)."""
        try:
            return s.presence_list(cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_presence_set(
        actor_id: str,
        state: str = "idle",
        label: str | None = None,
        cwd_path: str | None = None,
        note: str | None = None,
        path_specs: list[str] | None = None,
        journal: bool = True,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Upsert a presence row. state is idle|reading|editing. path_specs like 'src/foo.py:edit'."""
        try:
            return s.presence_set(
                actor_id=actor_id,
                state=state,
                label=label,
                cwd_path=cwd_path,
                note=note,
                path_specs=path_specs,
                journal=journal,
                cwd=cwd,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_presence_clear(actor_id: str, cwd: str | None = None) -> dict[str, Any]:
        """Remove one presence actor row by id."""
        try:
            return s.presence_clear(actor_id=actor_id, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_context_show(max_bytes: int = 24_000, cwd: str | None = None) -> str:
        """Show the shared context.md tail (canonical mission context)."""
        try:
            return s.context_show(max_bytes=max_bytes, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"

    @mcp.tool()
    def polylogue_context_append(text: str, cwd: str | None = None) -> dict[str, Any]:
        """Append text to shared context.md (atomic)."""
        try:
            return s.context_append(text, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_memory_show(max_bytes: int = 12_000, cwd: str | None = None) -> str:
        """Show the durable memory.md tail (long-lived decisions)."""
        try:
            return s.memory_show(max_bytes=max_bytes, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"

    @mcp.tool()
    def polylogue_memory_append(text: str, cwd: str | None = None) -> dict[str, Any]:
        """Append text to shared memory.md (atomic)."""
        try:
            return s.memory_append(text, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_turn_aware(
        cwd: str | None = None,
        session: str | None = None,
        participant_id: str | None = None,
        lines: int = 40,
        inbox_limit: int = 50,
    ) -> dict[str, Any]:
        """One-shot start-of-turn: ensure + sync pack + live peers + bridge inbox.

        Prefer this over calling ensure/sync_pack/peers/inbox separately when beginning work.
        """
        try:
            return s.turn_aware(
                cwd=cwd,
                session=session,
                participant_id=participant_id,
                lines=lines,
                inbox_limit=inbox_limit,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_context_set(text: str, cwd: str | None = None) -> dict[str, Any]:
        """Replace shared context.md entirely (atomic). Prefer append for incremental updates."""
        try:
            return s.context_set(text, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_subagent_list(parent: str | None = None, cwd: str | None = None) -> dict[str, Any]:
        """List registered subagent presence rows (optionally filtered by parent id)."""
        try:
            return s.subagent_list(parent=parent, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_subagent_add(
        parent_id: str,
        sub_id: str,
        label: str | None = None,
        state: str = "reading",
        cwd_path: str | None = None,
        note: str | None = None,
        path_specs: list[str] | None = None,
        journal: bool = True,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Register a subagent under a parent surface with optional path roles."""
        try:
            return s.subagent_add(
                parent_id=parent_id,
                sub_id=sub_id,
                label=label,
                state=state,
                cwd_path=cwd_path,
                note=note,
                path_specs=path_specs,
                journal=journal,
                cwd=cwd,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_subagent_remove(
        parent_id: str,
        sub_id: str,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Remove one subagent presence row."""
        try:
            return s.subagent_remove(parent_id=parent_id, sub_id=sub_id, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_scratch_dir(
        participant_id: str | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Return (and create) the per-participant scratch directory path."""
        try:
            return s.scratch_dir(participant_id=participant_id, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_scratch_write(
        name: str,
        text: str,
        participant_id: str | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Atomically write text into scratch/<participant>/NAME (relative path)."""
        try:
            return s.scratch_write(name, text, participant_id=participant_id, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_scratch_list(cwd: str | None = None) -> dict[str, Any]:
        """List per-participant scratch directories and file counts."""
        try:
            return s.scratch_list(cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_file_read(
        path: str,
        actor_id: str | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Record that an actor read a file (call before editing so peers can detect staleness)."""
        try:
            return s.file_read(path, actor_id=actor_id, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_file_check(
        actor_id: str | None = None,
        path: str | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """List files modified since the last recorded read (cross-surface stale detection)."""
        try:
            return s.file_check(actor_id=actor_id, path=path, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.tool()
    def polylogue_file_assert(
        path: str,
        actor_id: str | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Assert a file is still fresh since this actor last read it. ok=false if stale."""
        try:
            return s.file_assert(path, actor_id=actor_id, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    @mcp.resource(
        "polylogue://sync-pack",
        name="sync_pack",
        title="Polylogue sync pack",
        description="Full awareness Markdown for the current POLYLOGUE_MCP_CWD session.",
        mime_type="text/markdown",
    )
    def resource_sync_pack() -> str:
        return s.do_sync_pack()

    @mcp.resource(
        "polylogue://status",
        name="status",
        title="Polylogue status",
        description="Session meta, roster, and key paths as JSON.",
        mime_type="application/json",
    )
    def resource_status() -> dict[str, Any]:
        return s.do_status()

    @mcp.resource(
        "polylogue://presence",
        name="presence",
        title="Polylogue presence",
        description="Who is reading/editing what.",
        mime_type="application/json",
    )
    def resource_presence() -> dict[str, Any]:
        return s.presence_list()

    @mcp.resource(
        "polylogue://peers",
        name="peers",
        title="Polylogue peers",
        description="Live bridge peers and roster for cross-provider discovery.",
        mime_type="application/json",
    )
    def resource_peers() -> dict[str, Any]:
        return s.peers()

    @mcp.prompt(
        name="polylogue_cohesion",
        title="Polylogue cohesion recipe",
        description="How to stay mutually aware with other LLM agents via Polylogue.",
    )
    def prompt_cohesion() -> str:
        return s.COHESION_PROMPT

    @mcp.prompt(
        name="polylogue_turn_start",
        title="Polylogue turn-start checklist",
        description="Checklist for the start of an agent turn using Polylogue MCP tools.",
    )
    def prompt_turn_start() -> str:
        return s.TURN_START_PROMPT

    return mcp


def main() -> None:
    create_server().run(transport="stdio")
