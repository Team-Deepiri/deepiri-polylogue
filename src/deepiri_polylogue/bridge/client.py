"""Client helpers for the polylogue chat bridge."""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from typing import Any, Callable
from urllib.parse import quote

from .protocol import encode
from .server import get_active_bridge
from ..service_config import bridge_url


def bridge_status() -> dict[str, Any]:
    active = get_active_bridge()
    if active is not None:
        return active.status()
    return {"ok": False, "url": bridge_url(), "connections": 0, "rooms": {}}


def _run_sync(coro: Any) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return
    error: list[BaseException] = []

    def _worker() -> None:
        try:
            asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]


async def _send_async(
    room: str,
    participant_id: str,
    text: str,
    *,
    to: str | None = None,
    url: str | None = None,
) -> None:
    import websockets

    base = (url or bridge_url()).rstrip("/")
    uri = f"{base}/ws?room={quote(room)}&id={quote(participant_id)}"
    payload: dict[str, Any] = {"type": "message", "text": text}
    if to:
        payload["to"] = to
    async with websockets.connect(uri) as ws:
        await ws.send(encode(payload))
        try:
            await asyncio.wait_for(ws.recv(), timeout=1.0)
        except TimeoutError:
            pass


def send_message(
    room: str,
    participant_id: str,
    text: str,
    *,
    to: str | None = None,
    url: str | None = None,
) -> None:
    _run_sync(_send_async(room, participant_id, text, to=to, url=url))


async def _connect_async(
    room: str,
    participant_id: str,
    on_message: Callable[[dict[str, Any]], None],
    *,
    url: str | None = None,
    stdin: bool = False,
) -> None:
    import websockets

    base = (url or bridge_url()).rstrip("/")
    uri = f"{base}/ws?room={quote(room)}&id={quote(participant_id)}"
    async with websockets.connect(uri) as ws:
        reader: asyncio.Task[None] | None = None
        if stdin:

            async def _stdin_reader() -> None:
                loop = asyncio.get_running_loop()
                while True:
                    line = await loop.run_in_executor(None, sys.stdin.readline)
                    if not line:
                        break
                    text = line.rstrip("\n")
                    if not text:
                        continue
                    await ws.send(encode({"type": "message", "text": text}))

            reader = asyncio.create_task(_stdin_reader())
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                on_message(data)
        finally:
            if reader is not None:
                reader.cancel()


def connect_loop(
    room: str,
    participant_id: str,
    on_message: Callable[[dict[str, Any]], None],
    *,
    url: str | None = None,
    stdin: bool = False,
) -> None:
    _run_sync(_connect_async(room, participant_id, on_message, url=url, stdin=stdin))
