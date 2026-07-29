# Polylogue MCP

The Polylogue MCP server lets Cursor, Claude Desktop, and other MCP hosts join the same
shared journal + live bridge without shelling out to the CLI.

## Install

```bash
cd deepiri-polylogue
python3 -m pip install -e ".[mcp]"
# or with dev extras (includes mcp + pytest):
python3 -m pip install -e ".[dev]"
```

Entry point: `deepiri-polylogue-mcp` (stdio).

### Cursor

Add to your MCP config (e.g. `~/.cursor/mcp.json` or project `.cursor/mcp.json`):

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

If `deepiri-polylogue-mcp` is not on `PATH`, point `command` at your venv:

```json
"command": "/absolute/path/to/deepiri-polylogue/.venv/bin/deepiri-polylogue-mcp"
```

### Claude Desktop

Same stdio shape under `mcpServers` in Claude’s config file.

## Agent cohesion loop

1. `polylogue_ensure` — once per session (starts daemon + bridge listener, joins roster)
2. `polylogue_sync_pack` + `polylogue_bridge_inbox` — before substantive replies
3. `polylogue_peers` — see live agents across providers (cursor, claude, opencode, …)
4. `polylogue_say` and/or `polylogue_bridge_send` — share conclusions (durable vs live)
5. Never put secrets in journal or bridge messages

## Tools

### Bootstrap and discovery

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
- `polylogue_context_show` / `append`
- `polylogue_memory_show` / `append`

## Cross-provider discovery

`ensure` / `whoami` use `detect_provider()` (Cursor, Claude, OpenCode, Codex, Gemini, …)
and the session roster. Live peers come from the daemon’s bridge room membership on
`ws://127.0.0.1:7850`. Two MCP-configured agents on the same repo call `ensure` and
appear in each other’s `polylogue_peers` without a human running `service start` or
`bridge listen`.

## Out of scope (v1)

Delegate submit/watch, file assert/check, scratch write, and service install remain CLI-only.
