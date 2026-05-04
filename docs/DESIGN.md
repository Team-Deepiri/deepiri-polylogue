# Polylogue — design

## Name and intent

**Polylogue** (Deepiri): many LLM surfaces speaking into one durable thread so they stay loosely coupled but mutually aware—without depending on a single vendor’s multi-tab feature.

This is not real-time mind-meld. It is **explicit, inspectable shared state** that any client (Cursor, web UIs, terminals, scripts) can read and append to.

## Problem

You often have:

- several chat windows across providers (and local models),
- the same repo or mission,
- no native way for window A to know what window B concluded five minutes ago.

Copy-pasting context scales poorly and diverges.

## Design goals

1. **Provider-agnostic**: only needs a filesystem path the human (or agent) can reach.
2. **Cohesion without a hub server**: optional later; v1 is directory + JSONL.
3. **Human-auditable**: open `journal.jsonl` in an editor; understand the story.
4. **Agent-friendly**: stable event schema; a single command emits a paste-ready “awareness pack”.
5. **Safe by default**: no network; no secrets in events by convention (documented).

## Non-goals (v1)

- Automatic scraping of proprietary chat UIs.
- Cryptographic attribution of utterances.
- Sub-millisecond sync; file polling / manual refresh is fine.

## Cohesion model

Three primitives:

1. **Journal** — append-only JSONL of typed events (`utterance`, `handoff`, `snapshot`, `system`).
2. **Participants** — who is in the room (`participant_id`, `label`, `provider`, `last_seen`).
3. **Context pack** — rendered Markdown summarizing recent journal + roster for injection into any system prompt or first message.

Cross-awareness rule: each surface pastes or loads the latest **sync pack** before replying, and **says** (logs) material conclusions back to the journal.

## Event taxonomy

| `type`        | Meaning |
|---------------|---------|
| `utterance`   | Free-form text attributed to a `participant_id` and `role` (`user` / `assistant` / `meta`). |
| `handoff`     | Structured “I pass to X because …” with optional `next_participant` id. |
| `snapshot`    | Pointer or inline summary of external state (e.g. “tests green at commit abc”). |
| `system`      | Tooling or human notes (e.g. “rotated API key”, “session forked”). |

Each line is one JSON object with at least: `ts` (ISO8601 UTC), `type`, `id` (UUID4).

## Storage layout

Default root: `<cwd>/.deepiri/polylogue/` (override with `DEEPIRI_POLYLOGUE_ROOT`).

```
.deepiri/polylogue/
  meta.json           # session name, created_at
  participants.json   # roster
  journal.jsonl       # append-only log
```

## Threats and mitigations

- **Secret leakage in utterances**: convention + README warning; optional redact hook later.
- **Concurrent writers**: POSIX `fcntl` advisory locks on journal append; readers never lock for long. On platforms without `fcntl` (typical native Windows Python), appends are best-effort without an OS lock—still fine for human-paced multi-window use.
- **Journal growth**: rotation policy deferred; `tail` filters by default.

## Relation to “Deepiri vibe”

Prismatic, multi-path intelligence that still **grounds** in one observable plane—here, the repo-local polylogue directory.
