# Agent inject — peer messages as user input

Polylogue can wake a peer Cursor/Claude/OpenCode agent by injecting text into
their **agent terminal as keyboard input** — the same path as the human typing.

## Quick setup (two Cursor agent terminals)

**Terminal A** (slight-of-ice / receiving agent):

```bash
cd /path/to/slight-of-ice
deepiri-polylogue --cwd . init --session slight-of-ice
deepiri-polylogue --cwd . join --id slight-of-ice-cursor --provider cursor
# Inject ON — inbound bridge text is typed into this (or mapped) agent TTY:
deepiri-polylogue --cwd . bridge listen --inject --inject-runtime cursor --id slight-of-ice-cursor
```

Or rely on MCP `polylogue_ensure` (default since 0.4.1 starts listen with `--inject`).

**Optional TTY pin** (`~/.config/polylogue/cursor_inject.json`):

```json
{
  "workspaces": {
    "/home/you/Documents/slight-of-ice": {
      "tty": "pts/8",
      "mode": "tty"
    }
  }
}
```

`mode`: `auto` (TTY → resume → spawn → inbox) | `tty` | `resume` | `continue` | `spawn` | `all`

**Terminal B** (general-jump / sending agent):

```bash
cd /path/to/general-jump
deepiri-polylogue --cwd /path/to/slight-of-ice bridge send --room slight-of-ice \
  --text "poly-ack request: sync Comfy workflows 01/02/03"
# or signed wake-and-act:
deepiri-polylogue --cwd /path/to/slight-of-ice delegate submit \
  --to slight-of-ice-cursor \
  --text "Reply poly-ack on polylogue and confirm GPU availability."
```

MCP (sending side): `polylogue_ping_peer` or `polylogue_delegate_submit`.

## Env knobs

| Variable | Effect |
|----------|--------|
| `POLYLOGUE_BRIDGE_INJECT` | `1` (default for MCP listeners) enables inject; `0` disables |
| `POLYLOGUE_DELEGATE_RUNTIME` | `cursor` / `claude` / `opencode` / `inbox` |
| `POLYLOGUE_CURSOR_TTY` | Force `/dev/pts/N` or `pts/N` |
| `POLYLOGUE_CURSOR_CHAT_ID` | `agent --resume <id>` fallback |
| `POLYLOGUE_CURSOR_CONTINUE` | Prefer `agent --continue` |
| `POLYLOGUE_CURSOR_INJECT` | Override mode: `auto`/`tty`/`resume`/`spawn`/`all` |

## systemd user unit (always-on watch)

```bash
./scripts/install-agent-inject.sh --cwd /path/to/repo --id my-agent
```

## Security

Injection only writes to local TTYs you own / spawns local CLIs. Do not put secrets
in bridge messages. Delegate submit is HMAC-signed (`delegate init`).
