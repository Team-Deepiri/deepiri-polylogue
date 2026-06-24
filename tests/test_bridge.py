from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import pytest
import websockets

from deepiri_polylogue.bridge.client import send_message
from deepiri_polylogue.bridge.server import BridgeServer


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def bridge_server():
    port = _free_port()
    server = BridgeServer(host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.start, kwargs={"foreground": True}, daemon=True)
    thread.start()
    time.sleep(0.3)
    yield server, port
    server.stop()
    thread.join(timeout=3)


def test_bridge_broadcast(bridge_server):
    server, port = bridge_server
    received: list[dict] = []
    base = f"ws://127.0.0.1:{port}"

    async def run() -> None:
        uri = f"{base}/ws?room=test-room&id=listener"

        async def listener() -> None:
            async with websockets.connect(uri) as ws:
                async for raw in ws:
                    received.append(json.loads(raw))
                    if received and received[-1].get("type") == "message":
                        return

        listen_task = asyncio.create_task(listener())
        await asyncio.sleep(0.2)
        await _send_on_loop("test-room", "cursor", "hello opencode", url=base)
        await asyncio.wait_for(listen_task, timeout=3.0)

    asyncio.run(run())
    messages = [m for m in received if m.get("type") == "message"]
    assert messages
    assert messages[0]["from"] == "cursor"
    assert messages[0]["text"] == "hello opencode"


def test_bridge_direct_message(bridge_server):
    server, port = bridge_server
    received: list[dict] = []
    base = f"ws://127.0.0.1:{port}"

    async def run() -> None:
        uri = f"{base}/ws?room=dm-room&id=opencode"

        async def listener() -> None:
            async with websockets.connect(uri) as ws:
                async for raw in ws:
                    data = json.loads(raw)
                    if data.get("type") == "message":
                        received.append(data)
                        return

        listen_task = asyncio.create_task(listener())
        await asyncio.sleep(0.2)
        await _send_on_loop("dm-room", "cursor", "only for you", to="opencode", url=base)
        await asyncio.wait_for(listen_task, timeout=3.0)

    asyncio.run(run())
    assert received[0]["text"] == "only for you"
    assert received[0]["from"] == "cursor"


async def _send_on_loop(
    room: str,
    participant_id: str,
    text: str,
    *,
    to: str | None = None,
    url: str,
) -> None:
    from deepiri_polylogue.bridge.client import _send_async

    await _send_async(room, participant_id, text, to=to, url=url)
