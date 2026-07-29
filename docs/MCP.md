# Polylogue MCP

The Polylogue MCP server is **one stdio binary for every MCP host** — Cursor, Claude Desktop,
Claude Code, OpenCode, Google Antigravity, Gemini CLI, Codex, VS Code, Windsurf, and
any other client that speaks MCP stdio. Agents on different providers join the same journal
+ live bridge and see each other via `polylogue_peers`.

## Install

```bash
cd deepiri-polylogue
./install.sh --mcp           # library + deps + CLI + MCP binary
# or:
python3 -m pip install -e ".[mcp]"
```

`./install.sh` alone installs the library/CLI/service without MCP.
`./install.sh --mcp` adds the `[mcp]` extra and links `deepiri-polylogue-mcp`.

Entry point: `deepiri-polylogue-mcp` (stdio).

## Configure every host (official schemas)

Copy the matching file from [`examples/mcp/`](../examples/mcp/) — each example matches that
host’s **documented** config shape and path. Set:

- `POLYLOGUE_MCP_CWD` — absolute path to the shared repo
- `POLYLOGUE_PROVIDER` — stable roster label (`cursor`, `claude`, `opencode`, `gemini`,
  `antigravity`, `codex`, `vscode`, `windsurf`, …)

| Host | Example | Config location | Shape |
|------|---------|-----------------|-------|
| Cursor | [cursor.json](../examples/mcp/cursor.json) | `~/.cursor/mcp.json` or `.cursor/mcp.json` | `mcpServers` + `command`/`args`/`env` ([docs](https://cursor.com/docs/mcp)) |
| Claude Desktop | [claude-desktop.json](../examples/mcp/claude-desktop.json) | macOS `~/Library/Application Support/Claude/claude_desktop_config.json`; Linux `~/.config/Claude/…`; Windows `%APPDATA%\Claude\…` | `mcpServers` |
| Claude Code | [claude-code.json](../examples/mcp/claude-code.json) | project `.mcp.json` or `~/.claude.json` | `mcpServers` + `"type": "stdio"` ([docs](https://code.claude.com/docs/en/mcp-servers)) |
| OpenCode v2 | [opencode.json](../examples/mcp/opencode.json) | `opencode.json` / `~/.config/opencode/opencode.json` | **`mcp.servers`** + `type: "local"` + `command: [...]` + `environment` ([docs](https://opencode.ai/v2/docs/mcp-servers)) |
| Google Antigravity | [antigravity.json](../examples/mcp/antigravity.json) | `~/.gemini/config/mcp_config.json` or `.agents/mcp_config.json` | `mcpServers` (+ remote `serverUrl`) ([docs](https://antigravity.google/docs/mcp)) |
| Gemini CLI | [gemini-cli.json](../examples/mcp/gemini-cli.json) | `~/.gemini/settings.json` or `.gemini/settings.json` | `mcpServers` **in settings.json** (not Antigravity’s `mcp_config.json`) ([docs](https://geminicli.com/docs/tools/mcp-server/)) |
| OpenAI Codex | [codex.toml](../examples/mcp/codex.toml) | `~/.codex/config.toml` or `.codex/config.toml` | **TOML** `[mcp_servers.polylogue]` ([docs](https://developers.openai.com/codex/mcp)) |
| VS Code | [vscode.json](../examples/mcp/vscode.json) | `.vscode/mcp.json` | **`servers`** + `"type": "stdio"` ([docs](https://code.visualstudio.com/docs/agent-customization/mcp-servers)) |
| Windsurf | [windsurf.json](../examples/mcp/windsurf.json) | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` ([docs](https://docs.devin.ai/desktop/cascade/mcp)) |

Verified notes + common pitfalls: [examples/mcp/README.md](../examples/mcp/README.md).

If `deepiri-polylogue-mcp` is not on `PATH`, point `command` at
`~/.local/bin/deepiri-polylogue-mcp` (after `./install.sh --mcp`) or your venv binary.

## Agent cohesion loop

Works the same no matter which host/provider you are:

1. **`polylogue_turn_aware`** — preferred start-of-turn (ensure + sync pack + peers + inbox)
2. Or: `polylogue_ensure` once, then `sync_pack` / `bridge_inbox` / `peers`
3. `polylogue_say` and/or `polylogue_bridge_send` — share conclusions (durable vs live)
4. Before overwriting shared files: `file_read` then `file_assert`
5. Never put secrets in journal or bridge messages

Prompts: `polylogue_cohesion`, `polylogue_turn_start`.  
Resources: `polylogue://sync-pack`, `polylogue://status`, `polylogue://presence`, `polylogue://peers`.

## Tools

### Bootstrap and discovery

- `polylogue_turn_aware` — one-shot awareness pack for a turn
- `polylogue_ensure` — service + session + join + detached `bridge listen`
- `polylogue_whoami` — room, participant id, provider, peers
- `polylogue_peers` — live bridge peers + full roster
- `polylogue_bridge_status` — room connection stats
- `polylogue_bridge_send` — live message (`to` or broadcast; auto-target if one peer)
- `polylogue_bridge_inbox` — poll unread inbound from the listener log

### Journal and workspace

- `polylogue_sync_pack`, `polylogue_join`, `polylogue_say`, `polylogue_handoff`
- `polylogue_snapshot`, `polylogue_system`, `polylogue_tail`, `polylogue_status`
- `polylogue_presence_list` / `set` / `clear`
- `polylogue_context_show` / `append` / `set`
- `polylogue_memory_show` / `append`
- `polylogue_subagent_list` / `add` / `remove`
- `polylogue_scratch_dir` / `write` / `list`
- `polylogue_file_read` / `check` / `assert`

## Cross-provider discovery

`ensure` / `whoami` honor `POLYLOGUE_PROVIDER` first, then env/process heuristics for
Cursor, Claude, OpenCode, Antigravity, Gemini, Codex, Windsurf, VS Code, and more.
Live peers come from the daemon’s bridge room on `ws://127.0.0.1:7850`.

Example: Cursor + Claude Code + Antigravity + OpenCode on the same repo each run
`polylogue_turn_aware` and appear in each other’s `polylogue_peers` — no human
`service start` or `bridge listen` required.

## Out of scope (CLI-only for now)

Delegate submit/watch/init remains CLI-only (signing + long-lived watch loops).
