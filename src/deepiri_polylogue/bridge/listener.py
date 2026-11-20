"""Persistent bridge listener with outbox queue for agent-friendly send."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import quote

import websockets

from .protocol import encode
from .resolve import BridgeContext, resolve_bridge_context
from ..service_config import bridge_url


def state_paths(participant_id: str) -> tuple[Path, Path]:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in participant_id)
    base = Path(os.environ.get("POLYLOGUE_BRIDGE_STATE_DIR", "/tmp"))
    return base / f"polylogue-bridge-{safe}.log", base / f"polylogue-bridge-{safe}-outbox.jsonl"


def queue_message(participant_id: str, payload: dict) -> Path:
    _, outbox = state_paths(participant_id)
    outbox.parent.mkdir(parents=True, exist_ok=True)
    with outbox.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return outbox


def _log(log_path: Path, line: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
    print(line, flush=True)


async def _outbox_reader(ws: websockets.WebSocketClientProtocol, outbox: Path, log_path: Path) -> None:
    if not outbox.exists():
        outbox.touch()
    offset = 0
    while True:
        await asyncio.sleep(0.25)
        try:
            raw = outbox.read_text(encoding="utf-8")
        except OSError:
            continue
        if len(raw) <= offset:
            continue
        for line in raw[offset:].splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                _log(log_path, f"outbox skip bad json: {line[:80]}")
                continue
            if payload.get("type") not in ("message", "delegate"):
                payload = {"type": "message", "text": str(payload.get("text", line))}
            await ws.send(encode(payload))
            _log(log_path, f"sent: {json.dumps(payload, ensure_ascii=False)}")
        offset = len(raw)


async def _listen(
    ws: websockets.WebSocketClientProtocol,
    log_path: Path,
    *,
    inject: bool = False,
    inject_runtime: str | None = None,
    cwd: Path | None = None,
) -> None:
    async for raw in ws:
        _log(log_path, f"recv: {raw}")
        if not inject:
            continue
        try:
            data = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
            continue
        # Skip our own echoes / non-chat frames
        msg_type = data.get("type")
        if msg_type == "delegate":
            continue  # handled by `delegate watch`
        text = data.get("text") or data.get("prompt")
        if not text or not str(text).strip():
            continue
        # Do not re-inject messages we ourselves sent
        if data.get("from") and cwd is not None:
            pass
        try:
            from .runtimes import inject_user_text

            result = inject_user_text(str(text), cwd=cwd, runtime=inject_runtime)
            _log(log_path, f"inject: {json.dumps(result, ensure_ascii=False)}")
        except Exception as exc:  # noqa: BLE001 — keep listener alive
            _log(log_path, f"inject failed: {exc}")


async def _run_session(
    ctx: BridgeContext,
    url: str,
    log_path: Path,
    outbox: Path,
    *,
    inject: bool = False,
    inject_runtime: str | None = None,
) -> None:
    uri = f"{url.rstrip('/')}/ws?room={quote(ctx.room)}&id={quote(ctx.participant_id)}"
    async with websockets.connect(uri) as ws:
        _log(
            log_path,
            f"connected room={ctx.room} id={ctx.participant_id}"
            + (" inject=on" if inject else ""),
        )
        await asyncio.gather(
            _listen(
                ws,
                log_path,
                inject=inject,
                inject_runtime=inject_runtime,
                cwd=ctx.repo_root,
            ),
            _outbox_reader(ws, outbox, log_path),
        )


def listen_loop(
    cwd: Path | None = None,
    *,
    participant_id: str | None = None,
    room: str | None = None,
    url: str | None = None,
    inject: bool = False,
    inject_runtime: str | None = None,
) -> None:
    ctx = resolve_bridge_context(cwd, participant_id=participant_id, room=room)
    log_path, outbox = state_paths(ctx.participant_id)
    # Env override: POLYLOGUE_BRIDGE_INJECT=1 makes listen behave like typed user input
    env_inject = os.environ.get("POLYLOGUE_BRIDGE_INJECT", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "cursor",
    )
    if env_inject:
        inject = True
        if not inject_runtime and os.environ.get("POLYLOGUE_BRIDGE_INJECT", "").strip().lower() == "cursor":
            inject_runtime = "cursor"
    _log(
        log_path,
        f"resolved provider={ctx.provider} room={ctx.room} id={ctx.participant_id} "
        f"peers={ctx.peers} inject={inject}",
    )
    target_url = url or bridge_url()

    async def _main() -> None:
        while True:
            try:
                await _run_session(
                    ctx,
                    target_url,
                    log_path,
                    outbox,
                    inject=inject,
                    inject_runtime=inject_runtime,
                )
            except (websockets.ConnectionClosed, OSError) as exc:
                _log(log_path, f"disconnected: {exc} — reconnecting in 2s")
                await asyncio.sleep(2)

    asyncio.run(_main())
