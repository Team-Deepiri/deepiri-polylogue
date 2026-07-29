# Polylogue MCP

The Polylogue MCP server lets Cursor, Claude Desktop, and other MCP hosts join the same
shared journal + live bridge without shelling out to the CLI.

## Install

```bash
cd deepiri-polylogue
./install.sh                 # includes MCP binary symlink
# or:
python3 -m pip install -e ".[mcp]"
```

Entry point: `deepiri-polylogue-mcp` (stdio).

### Cursor

Copy [examples/mcp.cursor.json](../examples/mcp.cursor.json) into `~/.cursor/mcp.json`
(or project `.cursor/mcp.json`) and set `POLYLOGUE_MCP_CWD` to your repo:

```json
{
  "mcpServers": {
    "polylogue": {
      "command": "deepiri-polylogue-mcp",
      "args": [],
      "env": {
        "POLYLOGUE_MCP_CWD": "/absolute/path/to/your/repo"
      }
    }
  }
}
```

If `deepiri-polylogue-mcp` is not on `PATH`, point `command` at your venv or
`$HOME/.local/bin/deepiri-polylogue-mcp` after `./install.sh`.

### Claude Desktop

Same stdio shape under `mcpServers` in Claude’s config file.

## Agent cohesion loop

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

`ensure` / `whoami` use `detect_provider()` (Cursor, Claude, OpenCode, Codex, Gemini, …)
and the session roster. Live peers come from the daemon’s bridge room membership on
`ws://127.0.0.1:7850`. Two MCP-configured agents on the same repo call `ensure` (or
`turn_aware`) and appear in each other’s `polylogue_peers` without a human running
`service start` or `bridge listen`.

## Out of scope (CLI-only for now)

Delegate submit/watch/init remains CLI-only (signing + long-lived watch loops).
