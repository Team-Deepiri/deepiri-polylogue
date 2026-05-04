# Native streaming bridge (`polybridge`)

## What this is

`native/polybridge/polybridge.c` is a **TCP fan-out hub in C**: every connected peer sends length-framed **chunks**; the server **relays** each chunk to every other peer in real time. New connections are **detected** and announced to the room (`PEER_UP` / `PEER_DOWN`). Same `id` reconnecting **steals** the older session (old socket closed).

This is the **streaming** counterpart to the on-disk journal: bytes move now, not only when someone runs `polylogue say`.

## What this is not (hard boundary)

- **No assembly in the hot path** — syscalls and `poll(2)` already dominate; ASM would not make vendor UIs legible.
- **No magic “tap all LLM apps”** — ChatGPT in the browser, Cursor’s internal model traffic, etc. are not exposed as a POSIX fd you can `read()` without a **browser extension**, **vendor API client**, or **OS-level hook** you do not get from this repo alone.
- **The bridge is the contract** — anything that can open `127.0.0.1:PORT` and speak the wire format can participate: your Go/Python/Rust shim that calls OpenAI/Anthropic streaming APIs can **duplex** tokens through this hub so every adapter sees every stream.

## Wire protocol (binary)

### Client → server

1. **HELLO** `0x01`  
   `u16be id_len` `u16be label_len` `u16be prov_len` `id` `label` `prov` (UTF-8, no embedded NUL requirement)

2. **CHUNK** `0x02`  
   `u32be payload_len` `payload` (opaque; e.g. UTF-8 token, JSON line, SSE line)

### Server → client

1. **PEER_UP** `0x81`  
   `u16be slot` `u16be id_len` `id` `u16be label_len` `label` `u16be prov_len` `prov`

2. **PEER_DOWN** `0x82`  
   `u16be slot` `u16be id_len` `id`

3. **RELAY** `0x83`  
   `u16be from_slot` `u32be len` `payload` (same bytes the peer sent in CHUNK)

Default bind: `127.0.0.1:7847`. Override with `-l` / `-p` or `POLYBRIDGE_BIND` / `POLYBRIDGE_PORT`.

## Build

```bash
cd native/polybridge
make
./polybridge -h
```

## Smoke test (two terminals)

Terminal A (listener):

```bash
./polybridge -p 7848
```

Terminal B:

```bash
./polyclient 127.0.0.1 7848 beta "Window B" anthropic
```

Terminal C:

```bash
./polyclient 127.0.0.1 7848 alpha "Window A" openai 'hello from A'
```

Terminal B should print `hello from A` (one RELAY line) after C runs.

## Cohesion pattern

1. One **polybridge** per mission (or per repo).
2. Each “LLM window” runs an **adapter process** that:
   - connects with HELLO (`id` stable for that surface),
   - forwards **outbound** model tokens as CHUNK frames,
   - forwards **inbound** RELAY payloads into that UI (only your adapter knows how).
3. Optional: still use `polylogue` on disk for durable checkpoints; the C hub is for **live** fan-out.

## Security

Bind to loopback unless you know why you need LAN exposure. Payloads are **not** encrypted. Do not stream secrets through the hub.
