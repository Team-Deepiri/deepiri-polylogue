# deepiri-polylogue

**Many LLM windows, one thread of truth** — a Deepiri-flavored, filesystem-first shared journal so models across providers can stay mutually aware without a proprietary sync bus.

## Install (dev)

```bash
cd deepiri-polylogue
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -e ".[dev]" 2>/dev/null || python3 -m pip install -e .
python3 -m pip install pytest  # if optional dev extra not used
```

The console script is **`polylogue`**. Equivalent: `python3 -m deepiri_polylogue --help`.

## Quickstart

```bash
polylogue init --session my-mission
polylogue join --id win-a --label "ChatGPT tab" --provider openai
polylogue sync-pack   # paste into each surface's context
polylogue say --id win-a --role assistant --text "Explored design; proposing JSONL journal."
```

Override storage location:

```bash
export DEEPIRI_POLYLOGUE_ROOT=/path/to/shared/polylogue
polylogue status
```

## How cohesion works

1. **Journal** — append-only `journal.jsonl` (utterances, handoffs, snapshots, presence pings).
2. **Roster** — `participants.json` lists who is in the room.
3. **Shared files** (under `.deepiri/polylogue/`):
   - `shared/context.md` — canonical context every surface should load (via `sync-pack` or direct read).
   - `shared/memory.md` — durable decisions / long memory (append with `polylogue memory append`).
   - `presence.json` — who is **editing or reading which paths**, including **subagents** tied to a parent id.
   - `scratch/<participant-id>/` — temp notes per surface (`scratch-write` pipes stdin to a file there).
4. **Sync pack** — `polylogue sync-pack` folds journal + roster + presence table + context/memory tails + scratch listing into one paste block.

### Workspace commands (full sync)

```bash
# Register that this surface is editing specific paths (repeat --path)
polylogue presence set --id cursor-gpt --state editing --cwd "$PWD" \
  --path src/cli.py:edit --path docs/DESIGN.md:read --note "refactor CLI"

# Subagent spawned by that surface, reading a subtree
polylogue subagent add --parent cursor-gpt --id explore-1 --label "Explore agent" \
  --path diri-lang/compiler:read --note "mapping tokens"

# Shared canonical context (large blobs go here instead of chat)
polylogue context append --text $'## Current goal\nShip workspace sync.\n'

# Ephemeral handoff file (stdin → atomic file under scratch/)
echo "WIP notes" | polylogue scratch-write --id cursor-gpt --name handoff/notes.md

polylogue sync-pack --context-bytes 32000
```

See [docs/DESIGN.md](docs/DESIGN.md), [docs/STREAMING_BRIDGE.md](docs/STREAMING_BRIDGE.md), and [examples/cohesion-recipe.md](examples/cohesion-recipe.md).

## Security

Do not put API keys, tokens, or private customer data in journal lines. Treat the polylogue directory like a shared scratchpad in git (add `.deepiri/` to `.gitignore` if you do not want it committed).

## License

MIT — see [LICENSE](LICENSE).