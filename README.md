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

1. **Journal** — append-only `journal.jsonl` (utterances, handoffs, snapshots).
2. **Roster** — `participants.json` lists who is in the room.
3. **Sync pack** — `polylogue sync-pack` renders Markdown you paste so every model sees recent history + roster.

See [docs/DESIGN.md](docs/DESIGN.md) and [examples/cohesion-recipe.md](examples/cohesion-recipe.md).

## Security

Do not put API keys, tokens, or private customer data in journal lines. Treat the polylogue directory like a shared scratchpad in git (add `.deepiri/` to `.gitignore` if you do not want it committed).

## License

MIT — see [LICENSE](LICENSE).
