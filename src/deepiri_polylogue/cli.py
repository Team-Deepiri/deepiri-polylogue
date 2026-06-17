from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import filetime as ft
from .journal import append_event, journal_path, tail_events
from .models import Participant, event_line, utc_now_iso
from .pack import render_sync_pack
from .participants import load_participants, touch_participant, upsert_participant
from .paths import polylogue_root
from .store import init_session, load_meta
from . import workspace as ws


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="polylogue", description="Deepiri Polylogue — multi-LLM journal + workspace sync")
    p.add_argument("--root", type=Path, help="Override polylogue root directory")
    p.add_argument("--cwd", type=Path, default=None, help="Working directory for default root resolution")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s_init = sub.add_parser("init", help="Create session under .deepiri/polylogue/")
    s_init.add_argument("--session", default="default", help="Session name stored in meta.json")

    s_join = sub.add_parser("join", help="Register or update a participant id")
    s_join.add_argument("--id", required=True, help="Stable id for this LLM surface (e.g. gpt-cursor-1)")
    s_join.add_argument("--label", required=True, help="Human-readable label")
    s_join.add_argument("--provider", default="unknown", help="Vendor or runtime label")

    s_say = sub.add_parser("say", help="Append an utterance or note to the journal")
    s_say.add_argument("--id", required=True, dest="participant_id", help="Participant id")
    s_say.add_argument("--role", default="assistant", choices=["user", "assistant", "meta"], help="Utterance role")
    s_say.add_argument("--text", required=True, help="Message body")

    s_handoff = sub.add_parser("handoff", help="Log a structured handoff between participants")
    s_handoff.add_argument("--id", required=True, dest="participant_id", help="From participant id")
    s_handoff.add_argument("--to", required=True, dest="next_participant", help="Target participant id")
    s_handoff.add_argument("--text", required=True, help="Reason / instructions")

    s_snap = sub.add_parser("snapshot", help="Log a short state snapshot")
    s_snap.add_argument("--id", dest="participant_id", default=None, help="Optional participant id")
    s_snap.add_argument("--summary", required=True, help="One-line or short summary")

    s_tail = sub.add_parser("tail", help="Print last N journal events as JSON")
    s_tail.add_argument("--lines", type=int, default=20)

    sub.add_parser("status", help="Show meta + roster + journal path")

    s_pack = sub.add_parser("sync-pack", help="Render Markdown pack (journal + context + presence + scratch)")
    s_pack.add_argument("--lines", type=int, default=40)
    s_pack.add_argument("--context-bytes", type=int, default=24_000, dest="context_bytes")
    s_pack.add_argument("--memory-bytes", type=int, default=12_000, dest="memory_bytes")

    s_sys = sub.add_parser("system", help="Append a system/meta journal line")
    s_sys.add_argument("--text", required=True)

    # --- workspace: presence ---
    s_pr = sub.add_parser("presence", help="Who is editing / reading what (incl. subagents)")
    pr_sub = s_pr.add_subparsers(dest="presence_cmd", required=True)

    pr_list = pr_sub.add_parser("list", help="Print presence.json")
    pr_list.add_argument("--json", action="store_true", help="Pretty JSON to stdout")

    pr_set = pr_sub.add_parser("set", help="Upsert a participant or subagent row")
    pr_set.add_argument("--id", required=True, help="Actor id (surface or subagent)")
    pr_set.add_argument("--kind", choices=["participant", "subagent"], default="participant")
    pr_set.add_argument("--parent", dest="parent_id", default=None, help="Required when kind=subagent")
    pr_set.add_argument("--label", default=None, help="Display label")
    pr_set.add_argument("--state", choices=["idle", "reading", "editing"], default="editing")
    pr_set.add_argument("--cwd", default=None, help="Workspace cwd for this actor")
    pr_set.add_argument(
        "--path",
        action="append",
        default=None,
        metavar="REL_PATH:ROLE",
        help="Repeatable; ROLE is edit or read. If omitted, keep existing paths.",
    )
    pr_set.add_argument("--note", default=None, help="Free line (omit to keep previous)")
    pr_set.add_argument("--no-journal", action="store_true", help="Do not append journal presence event")

    pr_clear = pr_sub.add_parser("clear", help="Remove one actor row by id")
    pr_clear.add_argument("--id", required=True)

    # --- workspace: subagent (convenience) ---
    s_sa = sub.add_parser("subagent", help="Track subagents under a parent surface")
    sa_sub = s_sa.add_subparsers(dest="subagent_cmd", required=True)

    sa_list = sa_sub.add_parser("list", help="List subagent rows")
    sa_list.add_argument("--parent", default=None, help="Filter by parent participant id")

    sa_add = sa_sub.add_parser("add", help="Register a subagent working on paths")
    sa_add.add_argument("--parent", required=True)
    sa_add.add_argument("--id", required=True, dest="sub_id")
    sa_add.add_argument("--label", required=True)
    sa_add.add_argument("--state", choices=["idle", "reading", "editing"], default="reading")
    sa_add.add_argument("--cwd", default=None)
    sa_add.add_argument("--path", action="append", default=None, metavar="REL:ROLE")
    sa_add.add_argument("--note", default=None)
    sa_add.add_argument("--no-journal", action="store_true")

    sa_rm = sa_sub.add_parser("remove", help="Remove one subagent by parent + id")
    sa_rm.add_argument("--parent", required=True)
    sa_rm.add_argument("--id", required=True, dest="sub_id")

    # --- shared context / memory files ---
    s_ctx = sub.add_parser("context", help="Canonical shared markdown context")
    ctx_sub = s_ctx.add_subparsers(dest="ctx_cmd", required=True)
    c_show = ctx_sub.add_parser("show", help="Print context.md (tail)")
    c_show.add_argument("--max-bytes", type=int, default=100_000)
    c_set = ctx_sub.add_parser("set", help="Replace context.md from a file (atomic)")
    c_set.add_argument("--file", type=Path, required=True)
    c_app = ctx_sub.add_parser("append", help="Append text to context.md (atomic)")
    c_app.add_argument("--text", required=True)

    s_mem = sub.add_parser("memory", help="Durable decisions / long memory markdown")
    mem_sub = s_mem.add_subparsers(dest="mem_cmd", required=True)
    m_show = mem_sub.add_parser("show", help="Print memory.md (tail)")
    m_show.add_argument("--max-bytes", type=int, default=100_000)
    m_app = mem_sub.add_parser("append", help="Append text to memory.md (atomic)")
    m_app.add_argument("--text", required=True)

    s_sd = sub.add_parser("scratch-dir", help="Print per-participant scratch directory path")
    s_sd.add_argument("--id", required=True, dest="participant_id")

    s_sw = sub.add_parser("scratch-write", help="Write stdin bytes to scratch/<id>/NAME (atomic)")
    s_sw.add_argument("--id", required=True, dest="participant_id")
    s_sw.add_argument("--name", required=True, help="Relative path under scratch, e.g. notes/x.md")

    # --- file read tracking (OpenCode-style stale detection, persisted cross-surface) ---
    s_file = sub.add_parser("file", help="Track file reads and detect external modifications")
    file_sub = s_file.add_subparsers(dest="file_cmd", required=True)

    f_read = file_sub.add_parser("read", help="Record that an actor read a file (before edit)")
    f_read.add_argument("--id", required=True, dest="actor_id", help="Actor / participant id")
    f_read.add_argument("--path", required=True, help="File path (relative to --cwd or absolute)")
    f_read.add_argument("--cwd", type=Path, default=None, help="Resolve relative paths against this directory")

    f_check = file_sub.add_parser("check", help="List files modified since last recorded read")
    f_check.add_argument("--id", dest="actor_id", default=None, help="Filter to one actor")
    f_check.add_argument("--path", default=None, help="Filter to one file")
    f_check.add_argument("--cwd", type=Path, default=None)
    f_check.add_argument("--json", action="store_true", help="JSON array to stdout")

    f_assert = file_sub.add_parser("assert", help="Exit 1 if file changed since this actor last read it")
    f_assert.add_argument("--id", required=True, dest="actor_id")
    f_assert.add_argument("--path", required=True)
    f_assert.add_argument("--cwd", type=Path, default=None)

    args = p.parse_args(argv)
    root = args.root if args.root else polylogue_root(args.cwd)

    try:
        if args.cmd == "init":
            init_session(root, args.session)
            print(f"Initialized polylogue at {root}", file=sys.stderr)
            return 0

        load_meta(root)
        ws.workspace_init(root)

        if args.cmd == "join":
            upsert_participant(
                root,
                Participant(id=args.id, label=args.label, provider=args.provider, last_seen=utc_now_iso()),
            )
            print(f"Joined {args.id} as {args.label!r}", file=sys.stderr)
            return 0

        if args.cmd == "say":
            touch_participant(root, args.participant_id)
            append_event(
                root,
                event_line(
                    type="utterance",
                    participant_id=args.participant_id,
                    role=args.role,
                    text=args.text,
                ),
            )
            return 0

        if args.cmd == "handoff":
            touch_participant(root, args.participant_id)
            append_event(
                root,
                event_line(
                    type="handoff",
                    participant_id=args.participant_id,
                    text=args.text,
                    next_participant=args.next_participant,
                ),
            )
            return 0

        if args.cmd == "snapshot":
            if args.participant_id:
                touch_participant(root, args.participant_id)
            append_event(
                root,
                event_line(
                    type="snapshot",
                    participant_id=args.participant_id,
                    summary=args.summary,
                ),
            )
            return 0

        if args.cmd == "system":
            append_event(root, event_line(type="system", text=args.text))
            return 0

        if args.cmd == "tail":
            for ev in tail_events(root, lines=args.lines):
                print(json.dumps(ev, ensure_ascii=False))
            return 0

        if args.cmd == "status":
            meta = load_meta(root)
            people = load_participants(root)
            jp = journal_path(root)
            out = {
                "root": str(root),
                "meta": meta,
                "participants": [x.to_json() for x in people],
                "journal": str(jp),
                "context": str(ws.context_path(root)),
                "memory": str(ws.memory_path(root)),
                "presence": str(ws.presence_path(root)),
                "file_reads": str(ft.file_reads_path(root)),
                "scratch": str(ws.scratch_root(root)),
            }
            print(json.dumps(out, indent=2))
            return 0

        if args.cmd == "sync-pack":
            sys.stdout.write(
                render_sync_pack(
                    root,
                    lines=args.lines,
                    context_max_bytes=args.context_bytes,
                    memory_max_bytes=args.memory_bytes,
                )
            )
            return 0

        if args.cmd == "presence":
            return _cmd_presence(root, args)

        if args.cmd == "subagent":
            return _cmd_subagent(root, args)

        if args.cmd == "context":
            return _cmd_context(root, args)

        if args.cmd == "memory":
            return _cmd_memory(root, args)

        if args.cmd == "scratch-dir":
            d = ws.scratch_dir_for(root, args.participant_id)
            d.mkdir(parents=True, exist_ok=True)
            print(d.resolve())
            return 0

        if args.cmd == "scratch-write":
            ws.validate_scratch_rel(args.name)
            dest = ws.scratch_write_stdin(root, args.participant_id, args.name)
            print(str(dest.resolve()), file=sys.stderr)
            return 0

        if args.cmd == "file":
            return _cmd_file(root, args)

    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2
    except (ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:  # pragma: no cover
        print(f"error: {e}", file=sys.stderr)
        return 1

    return 1


def _paths_arg(path_list: list[str] | None) -> list[tuple[str, str]] | None:
    if path_list is None:
        return None
    return [ws.parse_path_role(x) for x in path_list]


def _journal_presence(root: Path, actor_id: str, row: dict) -> None:
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
        event_line(type="presence", participant_id=actor_id, summary=json.dumps(snap, ensure_ascii=False)),
    )


def _cmd_presence(root: Path, args: argparse.Namespace) -> int:
    if args.presence_cmd == "list":
        doc = ws.load_presence(root)
        if args.json:
            print(json.dumps(doc, indent=2, ensure_ascii=False))
        else:
            for a in doc.get("actors", []):
                print(json.dumps(a, ensure_ascii=False))
        return 0
    if args.presence_cmd == "clear":
        if not ws.clear_actor(root, args.id):
            print(f"no actor {args.id!r}", file=sys.stderr)
            return 1
        return 0
    if args.presence_cmd == "set":
        if args.kind == "subagent" and not args.parent_id:
            print("--parent required for kind=subagent", file=sys.stderr)
            return 1
        if args.kind == "participant":
            args.parent_id = None
        row = ws.upsert_actor(
            root,
            actor_id=args.id,
            kind=args.kind,
            parent_id=args.parent_id,
            label=args.label,
            state=args.state,
            cwd=args.cwd,
            paths=_paths_arg(args.path),
            note=args.note,
        )
        if not args.no_journal:
            _journal_presence(root, args.id, row)
        print(json.dumps(row, indent=2, ensure_ascii=False))
        return 0
    return 1


def _cmd_subagent(root: Path, args: argparse.Namespace) -> int:
    if args.subagent_cmd == "list":
        doc = ws.load_presence(root)
        for a in doc.get("actors", []):
            if a.get("kind") != "subagent":
                continue
            if args.parent and a.get("parent_id") != args.parent:
                continue
            print(json.dumps(a, ensure_ascii=False))
        return 0
    if args.subagent_cmd == "add":
        row = ws.upsert_actor(
            root,
            actor_id=args.sub_id,
            kind="subagent",
            parent_id=args.parent,
            label=args.label,
            state=args.state,
            cwd=args.cwd,
            paths=_paths_arg(args.path),
            note=args.note,
        )
        if not args.no_journal:
            _journal_presence(root, args.sub_id, row)
        print(json.dumps(row, indent=2, ensure_ascii=False))
        return 0
    if args.subagent_cmd == "remove":
        if not ws.clear_subagent(root, args.parent, args.sub_id):
            print("subagent not found", file=sys.stderr)
            return 1
        return 0
    return 1


def _cmd_context(root: Path, args: argparse.Namespace) -> int:
    cp = ws.context_path(root)
    if args.ctx_cmd == "show":
        sys.stdout.write(ws.read_text_tail(cp, max_bytes=args.max_bytes) + "\n")
        return 0
    if args.ctx_cmd == "set":
        data = args.file.expanduser().read_bytes()
        ws.atomic_write_bytes(cp, data)
        return 0
    if args.ctx_cmd == "append":
        prev = cp.read_bytes().decode("utf-8", errors="replace") if cp.is_file() else ""
        block = prev.rstrip() + "\n\n" + args.text.strip() + "\n"
        ws.atomic_write_text(cp, block)
        return 0
    return 1


def _cmd_file(root: Path, args: argparse.Namespace) -> int:
    if args.file_cmd == "read":
        touch_participant(root, args.actor_id)
        rec = ft.record_read(root, actor_id=args.actor_id, path=args.path, cwd=args.cwd)
        print(json.dumps(rec.__dict__, indent=2, ensure_ascii=False))
        return 0
    if args.file_cmd == "check":
        stale = ft.list_stale(root, actor_id=args.actor_id, path=args.path, cwd=args.cwd)
        if args.json:
            rows = [s.__dict__ for s in stale]
            print(json.dumps(rows, indent=2, ensure_ascii=False))
            return 0
        if not stale:
            return 0
        for s in stale:
            print(s.format_message())
            print("---")
        return 0
    if args.file_cmd == "assert":
        try:
            stale = ft.assert_fresh(root, actor_id=args.actor_id, path=args.path, cwd=args.cwd)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        if stale:
            print(stale.format_message(), file=sys.stderr)
            return 1
        return 0
    return 1


def _cmd_memory(root: Path, args: argparse.Namespace) -> int:
    mp = ws.memory_path(root)
    if args.mem_cmd == "show":
        sys.stdout.write(ws.read_text_tail(mp, max_bytes=args.max_bytes) + "\n")
        return 0
    if args.mem_cmd == "append":
        prev = mp.read_bytes().decode("utf-8", errors="replace") if mp.is_file() else ""
        block = prev.rstrip() + "\n\n" + args.text.strip() + "\n"
        ws.atomic_write_text(mp, block)
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
