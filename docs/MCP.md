# Polylogue MCP

The Polylogue MCP server is **one stdio binary for every MCP host** — Cursor, Claude Desktop,
Claude Code, OpenCode, Google Antigravity, Gemini CLI, Codex, VS Code agents, Windsurf, and
any other client that speaks MCP stdio. Agents on different providers join the same journal
+ live bridge and see each other via `polylogue_peers`.

## Install

```bash
cd deepiri-polylogue
./install.sh                 # includes MCP binary symlink
# or:
python3 -m pip install -e ".[mcp]"
```

Entry point: `deepiri-polylogue-mcp` (stdio).

## Configure every host

Copy the matching file from [`examples/mcp/`](../examples/mcp/) and set:

- `POLYLOGUE_MCP_CWD` — absolute path to the shared repo
- `POLYLOGUE_PROVIDER` — stable roster label (`cursor`, `claude`, `opencode`, `gemini`,
  `antigravity`, `codex`, `vscode`, `windsurf`, …)

| Host | Example | Config location |
|------|---------|-----------------|
| Cursor | [examples/mcp/cursor.json](../examples/mcp/cursor.json) | `~/.cursor/mcp.json` or `.cursor/mcp.json` |
| Claude Desktop | [examples/mcp/claude-desktop.json](../examples/mcp/claude-desktop.json) | macOS `~/Library/Application Support/Claude/claude_desktop_config.json`; Linux `~/.config/Claude/…`; Windows `%APPDATA%\Claude\…` |
| Claude Code | [examples/mcp/claude-code.json](../examples/mcp/claude-code.json) | project `.mcp.json` |
| OpenCode | [examples/mcp/opencode.json](../examples/mcp/opencode.json) | project `opencode.json` or `~/.config/opencode/opencode.json` |
| Google Antigravity | [examples/mcp/antigravity.json](../examples/mcp/antigravity.json) | `~/.gemini/config/mcp_config.json` or `.agents/mcp_config.json` |
| Gemini CLI | [examples/mcp/gemini-cli.json](../examples/mcp/gemini-cli.json) | same Gemini/Antigravity MCP config |
| OpenAI Codex | [examples/mcp/codex.json](../examples/mcp/codex.json) | Codex MCP settings |
| VS Code | [examples/mcp/vscode.json](../examples/mcp/vscode.json) | `.vscode/mcp.json` |
| Windsurf | [examples/mcp/windsurf.json](../examples/mcp/windsurf.json) | `~/.codeium/windsurf/mcp_config.json` |

Index + notes: [examples/mcp/README.md](../examples/mcp/README.md).

If `deepiri-polylogue-mcp` is not on `PATH`, point `command` at
`~/.local/bin/deepiri-polylogue-mcp` (after `./install.sh`) or your venv binary.

### Shared `mcpServers` snippet (most hosts)

```json
{
  "mcpServers": {
    "polylogue": {
      "command": "deepiri-polylogue-mcp",
      "args": [],
      "env": {
        "POLYLOGUE_MCP_CWD": "/absolute/path/to/your/repo",
        "POLYLOGUE_PROVIDER": "claude"
      }
    }
  }
}
```

OpenCode uses a different shape (`mcp` + `type: local` + `environment`) — see
[examples/mcp/opencode.json](../examples/mcp/opencode.json). VS Code uses `servers` —
see [examples/mcp/vscode.json](../examples/mcp/vscode.json).

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
