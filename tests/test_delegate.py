from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import websockets

from deepiri_polylogue.bridge.client import send_delegate
from deepiri_polylogue.bridge.delegate import (
    build_delegate,
    init_delegate_identity,
    secret_path,
    user_path,
    verify_delegate,
)
from deepiri_polylogue.bridge.runtimes import inject_delegate
from deepiri_polylogue.bridge.server import BridgeServer


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def delegate_keys(tmp_path, monkeypatch):
    cfg = tmp_path / "config" / "polylogue"
    cfg.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("POLYLOGUE_BRIDGE_STATE_DIR", str(tmp_path / "state"))
    init_delegate_identity(user="joe")
    return cfg


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


def test_delegate_sign_verify(delegate_keys):
    req = build_delegate(
        room="lydlr",
        sender="cursor-lydlr",
        target="opencode-lydlr",
        prompt="commit and push",
        cwd="/tmp/lydlr",
        sender_provider="cursor",
    )
    wire = req.to_wire()
    assert verify_delegate(wire)
    wire["prompt"] = "tampered"
    assert not verify_delegate(wire)


def test_delegate_inbox_inject(delegate_keys):
    req = build_delegate(
        room="lydlr",
        sender="opencode-lydlr",
        target="cursor-lydlr",
        prompt="merge the PR",
        cwd="/tmp/lydlr",
        sender_provider="opencode",
    )
    with patch("deepiri_polylogue.bridge.runtimes.detect_local_runtime", return_value="inbox"):
        result = inject_delegate(req.to_wire(), runtime="inbox")
    assert result["runtime"] == "inbox"
    inbox = Path(result["inbox"])
    assert inbox.is_file()
    row = json.loads(inbox.read_text(encoding="utf-8").strip())
    assert "Polylogue delegate" in row["prompt"]
    assert "merge the PR" in row["prompt"]


def test_bridge_delegate_routing(bridge_server, delegate_keys):
    server, port = bridge_server
    received: list[dict] = []
    base = f"ws://127.0.0.1:{port}"

    req = build_delegate(
        room="delegate-room",
        sender="cursor-lydlr",
        target="opencode-lydlr",
        prompt="run tests",
        cwd="/tmp/lydlr",
        sender_provider="cursor",
    )

    async def run() -> None:
        uri = f"{base}/ws?room=delegate-room&id=opencode-lydlr"

        async def listener() -> None:
            async with websockets.connect(uri) as ws:
                async for raw in ws:
                    data = json.loads(raw)
                    if data.get("type") == "delegate":
                        received.append(data)
                        return

        listen_task = asyncio.create_task(listener())
        await asyncio.sleep(0.2)
        send_delegate(
            "delegate-room",
            "cursor-lydlr",
            req.to_wire(),
            url=base,
            prefer_outbox=False,
        )
        await asyncio.wait_for(listen_task, timeout=3.0)

    asyncio.run(run())
    assert received
    assert received[0]["prompt"] == "run tests"
    assert received[0]["from"] == "cursor-lydlr"
    assert verify_delegate(received[0])


def test_delegate_init_creates_files(delegate_keys):
    assert secret_path().is_file()
    assert user_path().read_text(encoding="utf-8").strip() == "joe"
