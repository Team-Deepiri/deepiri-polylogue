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

## Milestone D — PolyBridge (landed on main)

- [x] Redis hub + context isolation (`src/polylogue/hub.py`)
- [x] Master/slave DAG orchestration, election, retry, monitoring
- [x] Journal bridge writing orchestration events into the filesystem journal
- [x] Native TCP `polybridge` + optional WebSocket/TLS transport
- [x] Optional extras: `[redis]`, `[orchestration]` (Redis stays out of the default path)

## Follow-ups (next)

- Watch mode / `fswatch` recipe for auto-regenerating sync pack.
- Optional HTTP adapter sharing the same files behind a small server (no Redis).
- Redaction plugin for utterances.
- First-class Cursor/Claude/OpenCode adapters that auto `say` + paste `sync-pack`.
- Optional MemoryMesh hook for durable `shared/memory.md`.
- Hub/orchestrator unit tests with a Redis fake or testcontainer.
