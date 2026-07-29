# Polylogue MCP host configs

Same stdio server (`deepiri-polylogue-mcp`) — different host config shapes.

Set `POLYLOGUE_MCP_CWD` to the absolute path of the repo you want agents to share.
Set `POLYLOGUE_PROVIDER` so peers show up under the right label in the roster
(`cursor`, `claude`, `opencode`, `gemini`, `antigravity`, `codex`, `vscode`, `windsurf`, …).

| Host | Example file | Typical config path |
|------|--------------|---------------------|
| Cursor | [cursor.json](cursor.json) | `~/.cursor/mcp.json` or `.cursor/mcp.json` |
| Claude Desktop | [claude-desktop.json](claude-desktop.json) | macOS `~/Library/Application Support/Claude/claude_desktop_config.json`; Linux `~/.config/Claude/claude_desktop_config.json`; Windows `%APPDATA%\Claude\claude_desktop_config.json` |
| Claude Code | [claude-code.json](claude-code.json) | project `.mcp.json` or Claude Code MCP settings |
| OpenCode | [opencode.json](opencode.json) | project `opencode.json` / `~/.config/opencode/opencode.json` |
| Google Antigravity | [antigravity.json](antigravity.json) | `~/.gemini/config/mcp_config.json` or workspace `.agents/mcp_config.json` |
| Gemini CLI | [gemini-cli.json](gemini-cli.json) | Gemini / Antigravity shared MCP config (`~/.gemini/config/mcp_config.json`) |
| OpenAI Codex | [codex.json](codex.json) | Codex MCP settings (stdio `mcpServers` shape) |
| VS Code (Copilot / agents) | [vscode.json](vscode.json) | `.vscode/mcp.json` |
| Windsurf | [windsurf.json](windsurf.json) | `~/.codeium/windsurf/mcp_config.json` |

If `deepiri-polylogue-mcp` is not on `PATH`, replace `command` with the absolute path from `./install.sh` (`~/.local/bin/deepiri-polylogue-mcp` or your venv).

Full tool docs: [docs/MCP.md](../../docs/MCP.md).
