#!/usr/bin/env bash
# Install deepiri-polylogue to ~/.local/bin and start the background service.
#
# Usage:
#   ./install.sh              # venv + symlinks + service
#   ./install.sh --no-service # binaries only
#
# Then from any git repo:
#   deepiri-polylogue --cwd /path/to/repo init --session myproject
#   deepiri-polylogue --cwd /path/to/repo bridge listen
#   deepiri-polylogue --cwd /path/to/repo bridge send --text "ping"

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${POLYLOGUE_INSTALL_DIR:-$HOME/.local}"
BIN_DIR="$INSTALL_DIR/bin"
VENV="$ROOT/.venv"
START_SERVICE=1

for arg in "$@"; do
  case "$arg" in
    --no-service) START_SERVICE=0 ;;
    -h|--help)
      sed -n '2,12p' "$0"
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
"$VENV/bin/pip" install -e "$ROOT[dev]" -q

echo "==> Linking CLI into $BIN_DIR"
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/deepiri-polylogue" "$BIN_DIR/deepiri-polylogue"
ln -sf "$VENV/bin/polylogue" "$BIN_DIR/polylogue"

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
echo "Installed deepiri-polylogue $(\"$BIN_DIR/deepiri-polylogue\" --version 2>/dev/null || echo 0.3.1)"
echo ""
echo "Quick start:"
echo "  deepiri-polylogue --cwd /path/to/repo init --session myproject"
echo "  deepiri-polylogue --cwd /path/to/repo join --id cursor --label Cursor --provider cursor"
echo "  deepiri-polylogue --cwd /path/to/repo bridge listen"
echo "  deepiri-polylogue --cwd /path/to/repo bridge send --text \"hello\""
