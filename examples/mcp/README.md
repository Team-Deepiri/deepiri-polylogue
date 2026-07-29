# Polylogue MCP host configs (verified against official docs)

Same stdio binary: `deepiri-polylogue-mcp`. Set `POLYLOGUE_MCP_CWD` to the shared
repo absolute path, and `POLYLOGUE_PROVIDER` to a stable roster label.

If the binary is not on `PATH`, replace `command` with
`~/.local/bin/deepiri-polylogue-mcp` (after `./install.sh`) or your venv path.

| Host | Example file | Official config location | Schema notes | Source |
|------|--------------|--------------------------|--------------|--------|
| **Cursor** | [cursor.json](cursor.json) | `~/.cursor/mcp.json` or project `.cursor/mcp.json` | Top-level `mcpServers`; stdio via `command` / `args` / `env` | [cursor.com/docs/mcp](https://cursor.com/docs/mcp) |
| **Claude Desktop** | [claude-desktop.json](claude-desktop.json) | macOS `~/Library/Application Support/Claude/claude_desktop_config.json`; Linux `~/.config/Claude/claude_desktop_config.json`; Windows `%APPDATA%\Claude\claude_desktop_config.json` | Top-level `mcpServers` | Claude Desktop → Settings → Developer → Edit Config |
| **Claude Code** | [claude-code.json](claude-code.json) | Project `.mcp.json` (team) or user `~/.claude.json` | Top-level `mcpServers`; stdio may include `"type": "stdio"` | [code.claude.com/docs/en/mcp-servers](https://code.claude.com/docs/en/mcp-servers) |
| **OpenCode (v2)** | [opencode.json](opencode.json) | Project `opencode.json` / `~/.config/opencode/opencode.json` | **`mcp.servers.<name>`** (not bare `mcp.<name>`); `type: "local"`; `command` is an **array**; env key is **`environment`** | [opencode.ai/v2/docs/mcp-servers](https://opencode.ai/v2/docs/mcp-servers) |
| **Google Antigravity** | [antigravity.json](antigravity.json) | Global `~/.gemini/config/mcp_config.json` or workspace `.agents/mcp_config.json` | Top-level `mcpServers`; stdio `command`/`args`/`env`/`cwd`; remote uses **`serverUrl`** (not `url`) | [antigravity.google/docs/mcp](https://antigravity.google/docs/mcp) |
| **Gemini CLI** | [gemini-cli.json](gemini-cli.json) | User `~/.gemini/settings.json` or project `.gemini/settings.json` | Top-level `mcpServers` **inside settings.json** (different file from Antigravity’s `mcp_config.json`); optional `trust` / `cwd` | [geminicli.com/docs/tools/mcp-server](https://geminicli.com/docs/tools/mcp-server/) |
| **OpenAI Codex** | [codex.toml](codex.toml) | `~/.codex/config.toml` or project `.codex/config.toml` | **TOML** `[mcp_servers.<name>]` with `command` / `args` / nested `[mcp_servers.<name>.env]` — not JSON `mcpServers` | [developers.openai.com/codex/mcp](https://developers.openai.com/codex/mcp) |
| **VS Code** | [vscode.json](vscode.json) | Workspace `.vscode/mcp.json` (or user profile mcp.json) | Top-level **`servers`** (not `mcpServers`); stdio requires **`"type": "stdio"`** | [code.visualstudio.com/docs/agent-customization/mcp-servers](https://code.visualstudio.com/docs/agent-customization/mcp-servers) |
| **Windsurf (Cascade)** | [windsurf.json](windsurf.json) | `~/.codeium/windsurf/mcp_config.json` (Windows: `%USERPROFILE%\.codeium\windsurf\mcp_config.json`) | Top-level `mcpServers`; stdio `command`/`args`/`env`; remote uses **`serverUrl`** | [docs.devin.ai/desktop/cascade/mcp](https://docs.devin.ai/desktop/cascade/mcp) |

## Common mistakes this folder avoids

- OpenCode v2: servers must live under `mcp.servers`, not directly under `mcp`.
- Codex: use TOML `mcp_servers`, not a Cursor-style JSON file.
- Gemini CLI vs Antigravity: both use Gemini paths under `~/.gemini/`, but **different filenames** (`settings.json` vs `config/mcp_config.json`).
- VS Code: root key is `servers`, and `type: "stdio"` is required for local processes.
- Windsurf / Antigravity remotes: prefer `serverUrl` over Cursor’s `url`.

Full tool docs: [docs/MCP.md](../../docs/MCP.md).
