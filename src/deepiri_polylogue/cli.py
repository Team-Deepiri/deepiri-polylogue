from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .journal import append_event, journal_path, tail_events
from .models import Participant, event_line, utc_now_iso
from .pack import render_sync_pack
from .participants import load_participants, touch_participant, upsert_participant
from .paths import polylogue_root
from .store import init_session, load_meta


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="polylogue", description="Deepiri Polylogue — multi-LLM filesystem journal")
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

    s_status = sub.add_parser("status", help="Show meta + roster + journal path")

    s_pack = sub.add_parser("sync-pack", help="Render Markdown awareness pack for pasting")
    s_pack.add_argument("--lines", type=int, default=40)

    s_sys = sub.add_parser("system", help="Append a system/meta journal line")
    s_sys.add_argument("--text", required=True)

    args = p.parse_args(argv)
    root = args.root if args.root else polylogue_root(args.cwd)

    try:
        if args.cmd == "init":
            init_session(root, args.session)
            print(f"Initialized polylogue at {root}", file=sys.stderr)
            return 0

        load_meta(root)  # ensure exists

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
            print(json.dumps({"root": str(root), "meta": meta, "participants": [x.to_json() for x in people], "journal": str(jp)}, indent=2))
            return 0

        if args.cmd == "sync-pack":
            sys.stdout.write(render_sync_pack(root, lines=args.lines))
            return 0

    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:  # pragma: no cover
        print(f"error: {e}", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
