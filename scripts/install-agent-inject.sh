#!/usr/bin/env bash
# Install a user systemd unit that keeps `bridge listen --inject` running for a repo.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CWDIR=""
PARTICIPANT=""
ROOM=""
RUNTIME="cursor"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cwd) CWDIR="$2"; shift 2 ;;
    --id) PARTICIPANT="$2"; shift 2 ;;
    --room) ROOM="$2"; shift 2 ;;
    --runtime) RUNTIME="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,8p' "$0"
      exit 0
      ;;
    *) echo "Unknown: $1" >&2; exit 2 ;;
  esac
done

CWDIR="$(cd "${CWDIR:-.}" && pwd)"
PARTICIPANT="${PARTICIPANT:-$(hostname)-cursor}"
BIN="${HOME}/.local/bin/deepiri-polylogue"
if [[ ! -x "$BIN" ]]; then
  BIN="$ROOT/.venv/bin/deepiri-polylogue"
fi
if [[ ! -x "$BIN" ]]; then
  echo "deepiri-polylogue not found — run ./install.sh --mcp first" >&2
  exit 1
fi

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"
SAFE="$(echo "$PARTICIPANT" | tr -c 'A-Za-z0-9_-' '_')"
UNIT="polylogue-inject-${SAFE}.service"

CMD="$BIN --cwd $CWDIR bridge listen --inject --inject-runtime $RUNTIME --id $PARTICIPANT"
if [[ -n "$ROOM" ]]; then
  CMD="$CMD --room $ROOM"
fi

cat >"$UNIT_DIR/$UNIT" <<EOF
[Unit]
Description=Deepiri Polylogue agent inject listener ($PARTICIPANT)
After=network.target deepiri-polylogue.service

[Service]
Type=simple
Environment=POLYLOGUE_BRIDGE_INJECT=1
Environment=POLYLOGUE_DELEGATE_RUNTIME=$RUNTIME
ExecStart=$CMD
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$UNIT"
systemctl --user status "$UNIT" --no-pager || true
echo "Installed $UNIT_DIR/$UNIT"
echo "Peer ping: deepiri-polylogue --cwd $CWDIR bridge send --text 'hello' --to $PARTICIPANT"
