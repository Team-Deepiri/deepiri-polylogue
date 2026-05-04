# Implementation plan — Polylogue v1

## Milestone A — Schema and storage

- [x] Resolve root path: cwd `.deepiri/polylogue/` or `DEEPIRI_POLYLOGUE_ROOT`.
- [x] `meta.json` on `init` with `session`, `created_at`.
- [x] `participants.json` list with stable string ids.
- [x] `journal.jsonl` append with file lock; validate JSON lines on read.

## Milestone B — CLI

- [x] `polylogue init [--session NAME]`
- [x] `polylogue join --id ID --label L [--provider P]`
- [x] `polylogue say --id ID --role R --text "..."`
- [x] `polylogue tail [--lines N]`
- [x] `polylogue status`
- [x] `polylogue sync-pack [--lines N]` → stdout Markdown for paste-injection

## Milestone C — Quality and packaging

- [x] Stdlib-only runtime; `pyproject.toml` with `polylogue` console script.
- [x] Unit tests for journal append and tail.
- [x] README quickstart and workflow recipe.

## Follow-ups (not v1)

- Watch mode / `fswatch` recipe for auto-regenerating sync pack.
- Optional HTTP adapter sharing the same files behind a small server.
- Redaction plugin for utterances.
