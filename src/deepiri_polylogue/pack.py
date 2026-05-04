from __future__ import annotations

import json
from pathlib import Path

from .journal import journal_path, tail_events
from .participants import load_participants
from .store import load_meta


def render_sync_pack(root: Path, *, lines: int = 40) -> str:
    meta = load_meta(root)
    people = load_participants(root)
    events = tail_events(root, lines=lines)
    jp = journal_path(root)

    lines_out: list[str] = []
    lines_out.append("# Polylogue sync pack (paste at top of any LLM turn)")
    lines_out.append("")
    lines_out.append(f"- **Session:** {meta.get('session', '?')}")
    lines_out.append(f"- **Journal:** `{jp}` (last {len(events)} events shown)")
    lines_out.append("")
    lines_out.append("## Roster (other voices in this thread)")
    if not people:
        lines_out.append("_(no participants registered yet — run `polylogue join`)_")
    else:
        for p in people:
            lines_out.append(
                f"- **{p.label}** (`{p.id}`) — provider: {p.provider}; last_seen: {p.last_seen}"
            )
    lines_out.append("")
    lines_out.append("## Recent journal (newest last)")
    if not events:
        lines_out.append("_(empty — log with `polylogue say`)_")
    else:
        for ev in events:
            lines_out.append(f"- `{ev.get('ts')}` **{ev.get('type')}** " + _one_line(ev))
    lines_out.append("")
    lines_out.append(
        "When you produce substantive output, append one line: "
        "`polylogue say --id YOUR_ID --role assistant --text \"...\"` "
        "(keep secrets out of the journal)."
    )
    return "\n".join(lines_out) + "\n"


def _one_line(ev: dict) -> str:
    tid = ev.get("participant_id")
    role = ev.get("role")
    parts: list[str] = []
    if tid:
        parts.append(f"@{tid}")
    if role:
        parts.append(f"role={role}")
    body = ev.get("text") or ev.get("summary") or ""
    if body:
        parts.append(json.dumps(body, ensure_ascii=False)[:240])
    return " ".join(parts) if parts else json.dumps(ev, ensure_ascii=False)[:200]
