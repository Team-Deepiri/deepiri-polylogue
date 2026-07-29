# Cohesion recipe (multi-window / multi-provider)

Goal: several LLM surfaces — **any MCP host / provider** — co-own one task.

## MCP path (recommended)

1. Install once: `./install.sh` (links `deepiri-polylogue-mcp`).
2. Point **each** host at Polylogue using the matching file in [`examples/mcp/`](mcp/):
   - Cursor → `mcp/cursor.json`
   - Claude Desktop / Claude Code → `mcp/claude-desktop.json` / `mcp/claude-code.json`
   - OpenCode → `mcp/opencode.json`
   - Google Antigravity / Gemini CLI → `mcp/antigravity.json` / `mcp/gemini-cli.json`
   - Codex → `mcp/codex.toml` (TOML for `~/.codex/config.toml`, not JSON)
   - VS Code / Windsurf → `mcp/vscode.json` / `mcp/windsurf.json`
3. Set `POLYLOGUE_MCP_CWD` to the same repo and a distinct `POLYLOGUE_PROVIDER` per surface.
4. Tell each agent to use Polylogue. At turn start: **`polylogue_turn_aware`**.
5. After work: **`polylogue_say`** (durable) and/or **`polylogue_bridge_send`** (live).
6. Before overwriting shared files: **`polylogue_file_read`** then **`polylogue_file_assert`**.

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
