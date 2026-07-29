# Cohesion recipe (multi-window)

Goal: several LLM surfaces (any providers) co-own one task.

## MCP path (recommended)

1. Install once: `./install.sh` (links `deepiri-polylogue-mcp`).
2. Point Cursor/Claude at [examples/mcp.cursor.json](mcp.cursor.json) with `POLYLOGUE_MCP_CWD` set to the repo.
3. Tell each agent to use Polylogue. At turn start they should call **`polylogue_turn_aware`** (ensure + sync pack + peers + inbox).
4. After meaningful work: **`polylogue_say`** (durable) and/or **`polylogue_bridge_send`** (live).
5. Before overwriting files another agent may touch: **`polylogue_file_read`** then **`polylogue_file_assert`**.

See [docs/MCP.md](../docs/MCP.md) for the full tool list.

## CLI path

1. In the repo root, run once:

   ```bash
   polylogue init --session my-feature
   polylogue join --id cursor-gpt --label "Cursor GPT" --provider openai
   polylogue join --id web-claude --label "Browser Claude" --provider anthropic
   polylogue join --id local-llm --label "Ollama Qwen" --provider ollama
   ```

2. Before each model replies, paste the output of:

   ```bash
   polylogue sync-pack
   ```

3. After each model finishes a meaningful slice of work:

   ```bash
   polylogue say --id cursor-gpt --role assistant --text "Implemented CLI; tests pass."
   ```

4. When explicitly passing work:

   ```bash
   polylogue handoff --id cursor-gpt --to web-claude --text "Please review error handling in cli.py."
   ```

5. Optional state pin:

   ```bash
   polylogue snapshot --summary "CI green on main @ abc1234"
   ```

Every surface reads the same files; cohesion is **protocol + habit**, not vendor magic.
