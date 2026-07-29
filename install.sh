#!/usr/bin/env bash
# Install deepiri-polylogue to ~/.local/bin and start the background service.
#
# Usage:
#   ./install.sh              # venv + symlinks + service + MCP
#   ./install.sh --no-service # binaries only
#   ./install.sh --no-mcp     # skip MCP extra / mcp binary link
#
# Then from any git repo:
#   deepiri-polylogue --cwd /path/to/repo init --session myproject
#   deepiri-polylogue --cwd /path/to/repo bridge listen
#   deepiri-polylogue --cwd /path/to/repo bridge send --text "ping"
#
# Or configure Cursor/Claude with deepiri-polylogue-mcp (see docs/MCP.md).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${POLYLOGUE_INSTALL_DIR:-$HOME/.local}"
BIN_DIR="$INSTALL_DIR/bin"
VENV="$ROOT/.venv"
START_SERVICE=1
INSTALL_MCP=1

for arg in "$@"; do
  case "$arg" in
    --no-service) START_SERVICE=0 ;;
    --no-mcp) INSTALL_MCP=0 ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required." >&2
  exit 1
fi

echo "==> Creating venv at $VENV"
python3 -m venv "$VENV"
"$VENV/bin/pip" install -U pip wheel -q
if [[ "$INSTALL_MCP" -eq 1 ]]; then
  "$VENV/bin/pip" install -e "$ROOT[dev,mcp]" -q
else
  "$VENV/bin/pip" install -e "$ROOT[dev]" -q
fi

echo "==> Linking CLI into $BIN_DIR"
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/deepiri-polylogue" "$BIN_DIR/deepiri-polylogue"
ln -sf "$VENV/bin/polylogue" "$BIN_DIR/polylogue"
if [[ "$INSTALL_MCP" -eq 1 && -x "$VENV/bin/deepiri-polylogue-mcp" ]]; then
  ln -sf "$VENV/bin/deepiri-polylogue-mcp" "$BIN_DIR/deepiri-polylogue-mcp"
fi

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  echo ""
  echo "Add to your shell profile:"
  echo "  export PATH=\"$BIN_DIR:\$PATH\""
  echo ""
fi

export PATH="$BIN_DIR:$PATH"

if [[ "$START_SERVICE" -eq 1 ]]; then
  echo "==> Installing platform service hook"
  "$BIN_DIR/deepiri-polylogue" service install >/dev/null 2>&1 || true
  if ! curl -sf http://127.0.0.1:7849/health >/dev/null 2>&1; then
    echo "==> Starting polylogue service"
    "$BIN_DIR/deepiri-polylogue" service start
  else
    echo "==> Polylogue service already running"
  fi
fi

echo ""
echo "Installed deepiri-polylogue $(\"$BIN_DIR/deepiri-polylogue\" --version 2>/dev/null || echo unknown)"
echo ""
echo "Quick start (CLI):"
echo "  deepiri-polylogue --cwd /path/to/repo init --session myproject"
echo "  deepiri-polylogue --cwd /path/to/repo join --id cursor --label Cursor --provider cursor"
echo "  deepiri-polylogue --cwd /path/to/repo bridge listen"
echo "  deepiri-polylogue --cwd /path/to/repo bridge send --text \"hello\""
if [[ "$INSTALL_MCP" -eq 1 ]]; then
  echo ""
  echo "MCP (all hosts — Cursor, Claude, OpenCode, Antigravity/Gemini, Codex, VS Code, Windsurf):"
  echo "  command: $BIN_DIR/deepiri-polylogue-mcp"
  echo "  configs: examples/mcp/  (set POLYLOGUE_MCP_CWD + POLYLOGUE_PROVIDER per surface)"
  echo "  docs:    docs/MCP.md"
fi
