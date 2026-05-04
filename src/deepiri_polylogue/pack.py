from __future__ import annotations

import json
from pathlib import Path

from .journal import journal_path, tail_events
from .participants import load_participants
from .store import load_meta
from . import workspace as ws


def render_sync_pack(
    root: Path,
    *,
    lines: int = 40,
    context_max_bytes: int = 24_000,
    memory_max_bytes: int = 12_000,
) -> str:
    ws.workspace_init(root)
    meta = load_meta(root)
    people = load_participants(root)
    events = tail_events(root, lines=lines)
    jp = journal_path(root)
    presence = ws.load_presence(root)
    ctx = ws.read_text_tail(ws.context_path(root), max_bytes=context_max_bytes)
    mem = ws.read_text_tail(ws.memory_path(root), max_bytes=memory_max_bytes)
    scratch = ws.list_scratch_files(root)

    lines_out: list[str] = []
    lines_out.append("# Polylogue full sync pack (paste at top of any LLM turn)")
    lines_out.append("")
    lines_out.append(f"- **Session:** {meta.get('session', '?')}")
    lines_out.append(f"- **Root:** `{root}`")
    lines_out.append(f"- **Journal:** `{jp}` (last {len(events)} events below)")
    lines_out.append(f"- **Shared context file:** `{ws.context_path(root)}`")
    lines_out.append(f"- **Memory file:** `{ws.memory_path(root)}`")
    lines_out.append(f"- **Presence file:** `{ws.presence_path(root)}`")
    lines_out.append("")
    lines_out.append("## Who is where (presence + subagents)")
    actors = presence.get("actors", [])
    if not actors:
        lines_out.append("_(no presence rows — run `polylogue presence set` or `polylogue subagent add`)_")
    else:
        lines_out.append("| id | kind | parent | state | cwd | paths | note |")
        lines_out.append("|---|---|---|---|---|---|---|")
        for a in sorted(actors, key=lambda x: (str(x.get("kind")), str(x.get("id")))):
            pid = str(a.get("id", ""))
            kind = str(a.get("kind", ""))
            parent = str(a.get("parent_id") or "")
            state = str(a.get("state", ""))
            cwd = str(a.get("cwd") or "").replace("|", "\\|")
            paths = a.get("paths") or []
            ps = "; ".join(f"{p.get('path')}:{p.get('role')}" for p in paths if isinstance(p, dict))
            ps = ps.replace("|", "\\|")
            note = str(a.get("note", "")).replace("|", "\\|").replace("\n", " ")[:120]
            lines_out.append(f"| `{pid}` | {kind} | `{parent}` | {state} | `{cwd}` | {ps} | {note} |")
    lines_out.append("")
    lines_out.append("## Scratch dirs (per-participant temp notes)")
    if not scratch:
        lines_out.append("_(empty — use `polylogue scratch-dir` / `scratch-write`)_")
    else:
        for name, n in scratch:
            lines_out.append(f"- `{ws.scratch_root(root) / name}/` — {n} file(s)")
    lines_out.append("")
    lines_out.append("## Roster (registered LLM surfaces)")
    if not people:
        lines_out.append("_(no participants — run `polylogue join`)_")
    else:
        for p in people:
            lines_out.append(
                f"- **{p.label}** (`{p.id}`) — provider: {p.provider}; last_seen: {p.last_seen}"
            )
    lines_out.append("")
    lines_out.append("## Shared context (tail / truncated)")
    lines_out.append("```markdown")
    lines_out.append(ctx.rstrip() or "_(empty)_")
    lines_out.append("```")
    lines_out.append("")
    lines_out.append("## Long-term memory (tail / truncated)")
    lines_out.append("```markdown")
    lines_out.append(mem.rstrip() or "_(empty)_")
    lines_out.append("```")
    lines_out.append("")
    lines_out.append("## Recent journal (newest last)")
    if not events:
        lines_out.append("_(empty — log with `polylogue say`)_")
    else:
        for ev in events:
            lines_out.append(f"- `{ev.get('ts')}` **{ev.get('type')}** " + _one_line(ev))
    lines_out.append("")
    lines_out.append(
        "**Contract:** after material work, run `polylogue say --id YOUR_ID --role assistant --text \"...\"` "
        "and update `polylogue presence set` / `subagent` rows. Keep secrets out of repo-backed files."
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
