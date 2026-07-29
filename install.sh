#!/usr/bin/env bash
# One-stop install for Deepiri Polylogue: venv, dependencies, library, CLI links, service.
#
# Usage:
#   ./install.sh              # library + all core/dev deps + CLI + background service
#   ./install.sh --mcp        # same as above, plus MCP SDK extra + deepiri-polylogue-mcp
#   ./install.sh --no-service # skip platform service install / start
#   ./install.sh --mcp --no-service
#
# After install (from any git repo):
#   deepiri-polylogue --cwd /path/to/repo init --session myproject
#   deepiri-polylogue --cwd /path/to/repo bridge listen
#   deepiri-polylogue --cwd /path/to/repo bridge send --text "ping"
#
# With --mcp, also configure hosts via examples/mcp/ (see docs/MCP.md).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${POLYLOGUE_INSTALL_DIR:-$HOME/.local}"
BIN_DIR="$INSTALL_DIR/bin"
VENV="$ROOT/.venv"
START_SERVICE=1
INSTALL_MCP=0

for arg in "$@"; do
  case "$arg" in
    --mcp) INSTALL_MCP=1 ;;
    --no-service) START_SERVICE=0 ;;
    --no-mcp)
      # Backward-compatible no-op: MCP is opt-in via --mcp now.
      INSTALL_MCP=0
      ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg (try --help)" >&2
      exit 2
      ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required (3.10+)." >&2
  exit 1
fi

PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
# shellcheck disable=SC2072
if python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  :
else
  echo "python3 >= 3.10 required (found $PY_VER)." >&2
  exit 1
fi

echo "==> Creating venv at $VENV"
python3 -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install -U pip setuptools wheel -q

if [[ "$INSTALL_MCP" -eq 1 ]]; then
  echo "==> Installing deepiri-polylogue (editable) with [dev,mcp] extras"
  python -m pip install -e "$ROOT[dev,mcp]" -q
else
  echo "==> Installing deepiri-polylogue (editable) with [dev] extras"
  python -m pip install -e "$ROOT[dev]" -q
fi

echo "==> Linking CLI into $BIN_DIR"
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/deepiri-polylogue" "$BIN_DIR/deepiri-polylogue"
ln -sf "$VENV/bin/polylogue" "$BIN_DIR/polylogue"

if [[ "$INSTALL_MCP" -eq 1 ]]; then
  if [[ ! -x "$VENV/bin/deepiri-polylogue-mcp" ]]; then
    echo "error: deepiri-polylogue-mcp missing after [mcp] install" >&2
    exit 1
  fi
  ln -sf "$VENV/bin/deepiri-polylogue-mcp" "$BIN_DIR/deepiri-polylogue-mcp"
  echo "==> Linked MCP server: $BIN_DIR/deepiri-polylogue-mcp"
else
  # Avoid a stale MCP symlink from a previous --mcp install.
  if [[ -L "$BIN_DIR/deepiri-polylogue-mcp" ]]; then
    rm -f "$BIN_DIR/deepiri-polylogue-mcp"
  fi
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
  if command -v curl >/dev/null 2>&1 && curl -sf http://127.0.0.1:7849/health >/dev/null 2>&1; then
    echo "==> Polylogue service already running"
  else
    echo "==> Starting polylogue service"
    "$BIN_DIR/deepiri-polylogue" service start || {
      echo "warning: service start failed — run: deepiri-polylogue service start --foreground" >&2
    }
  fi
fi

VERSION="$("$BIN_DIR/deepiri-polylogue" --version 2>/dev/null || echo unknown)"
echo ""
echo "Installed deepiri-polylogue $VERSION"
echo "  library:  editable install from $ROOT"
echo "  venv:     $VENV"
echo "  CLI:      $BIN_DIR/deepiri-polylogue"
echo "  extras:   $([ "$INSTALL_MCP" -eq 1 ] && echo 'dev,mcp' || echo 'dev')"
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
else
  echo ""
  echo "MCP (optional): re-run with  ./install.sh --mcp"
fi
