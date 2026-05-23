# Deepiri Polylogue

Polylogue is a **filesystem-first shared journal** for coordinating multiple LLM surfaces (different providers, tabs, or tools) on the same mission. Surfaces stay mutually aware through explicit, inspectable files—no proprietary sync bus and no network requirement in the default path.

## Overview

When several chat windows work on the same repository or goal, context diverges quickly. Polylogue gives you a single append-only event log, a participant roster, shared Markdown for canonical context and long-lived memory, optional presence and scratch paths, and a **`sync-pack`** command that renders a paste-ready block so every surface can load the same picture before replying.

Design goals: provider-agnostic storage, human-auditable JSONL, agent-friendly commands, and safe-by-default operation (no secrets in journal events by convention).

## Requirements

- Python 3.10 or newer

## Installation

From a clone of this repository:

```bash
cd deepiri-polylogue
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -e ".[dev]" 2>/dev/null || python3 -m pip install -e .
python3 -m pip install pytest  # only if the optional `dev` extra was not installed
```

The primary CLI entry point is **`polylogue`**. You can also run `python3 -m deepiri_polylogue` or `python3 -m polylogue` for equivalent behavior.

## Quick start

```bash
polylogue init --session my-mission
polylogue join --id win-a --label "ChatGPT tab" --provider openai
polylogue sync-pack   # paste into each surface's context
polylogue say --id win-a --role assistant --text "Explored design; proposing JSONL journal."
```

Override the storage root (default is under the current working directory):

```bash
export DEEPIRI_POLYLOGUE_ROOT=/path/to/shared/polylogue
polylogue status
```

## Architecture

| Component | Role |
|-----------|------|
| **Journal** (`journal.jsonl`) | Append-only JSONL of utterances, handoffs, snapshots, and system events. |
| **Roster** (`participants.json`) | Registered participants (ids, labels, providers). |
| **Shared context** (`shared/context.md`) | Canonical context every surface should align on; embedded in `sync-pack` (or read directly). |
| **Shared memory** (`shared/memory.md`) | Durable decisions and long memory; append with `polylogue memory append`. |
| **Presence** (`presence.json`) | Who is reading or editing which paths, including **subagents** linked to a parent id. |
| **Scratch** (`scratch/<participant-id>/`) | Per-surface transient files (`scratch-write` writes stdin atomically into this tree). |
| **Sync pack** | `polylogue sync-pack` combines journal tail, roster, presence, context and memory tails, and scratch listings into one block for injection into prompts or first messages. |

All of the above live under `.deepiri/polylogue/` by default (or under `DEEPIRI_POLYLOGUE_ROOT`).

## Workspace workflow

Typical commands for full workspace awareness:

```bash
# Register that this surface is editing or reading specific paths (repeat --path as needed)
polylogue presence set --id cursor-gpt --state editing --cwd "$PWD" \
  --path src/cli.py:edit --path docs/DESIGN.md:read --note "refactor CLI"

# Register a subagent tied to a parent surface
polylogue subagent add --parent cursor-gpt --id explore-1 --label "Explore agent" \
  --path diri-lang/compiler:read --note "mapping tokens"

# Append shared canonical context (prefer files here for large blobs)
polylogue context append --text $'## Current goal\nShip workspace sync.\n'

# Write an ephemeral handoff under scratch/ (stdin → atomic file)
echo "WIP notes" | polylogue scratch-write --id cursor-gpt --name handoff/notes.md

polylogue sync-pack --context-bytes 32000
```

## Documentation

- [Design](docs/DESIGN.md) — model, event taxonomy, storage layout, and threat notes  
- [Streaming bridge](docs/STREAMING_BRIDGE.md) — optional streaming integration  
- [Cohesion recipe](examples/cohesion-recipe.md) — practical usage pattern  

## Security

Do not place API keys, tokens, or sensitive customer data in journal lines. Treat the polylogue directory as a shared workspace artifact. If you do not want polylogue state in version control, add `.deepiri/` to `.gitignore`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
# PolyBridge
