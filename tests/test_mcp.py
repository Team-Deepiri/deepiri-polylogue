from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp")
from mcp import Client  # noqa: E402

from deepiri_polylogue.bridge.listener import state_paths  # noqa: E402
from deepiri_polylogue.bridge.server import BridgeServer  # noqa: E402
from deepiri_polylogue.mcp.server import create_server  # noqa: E402
from deepiri_polylogue.mcp import session as sess  # noqa: E402


@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("POLYLOGUE_BRIDGE_STATE_DIR", str(tmp_path / "bridge-state"))
    monkeypatch.delenv("POLYLOGUE_LEGACY_SIDECAR", raising=False)
    monkeypatch.delenv("DEEPIRI_POLYLOGUE_ROOT", raising=False)
    return tmp_path


@pytest.fixture
def repo(isolated_data, tmp_path, monkeypatch):
    repo = tmp_path / "mcp-proj"
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setenv("POLYLOGUE_MCP_CWD", str(repo))
    return repo


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def no_daemon(monkeypatch):
    monkeypatch.setattr(sess, "ensure_service", lambda wait_s=6.0: True)
    monkeypatch.setattr(sess, "is_running", lambda: False)
    monkeypatch.setattr(sess, "health", lambda: None)
    monkeypatch.setattr(
        sess,
        "start_listener",
        lambda *a, **k: {"started": False, "skipped": True},
    )


def test_mcp_journal_cohesion(repo, no_daemon):
    server = create_server()

    async def run() -> None:
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools.tools}
            assert "polylogue_ensure" in names
            assert "polylogue_sync_pack" in names
            assert "polylogue_bridge_send" in names

            ensured = await client.call_tool(
                "polylogue_ensure",
                {
                    "cwd": str(repo),
                    "session": "mcp-smoke",
                    "participant_id": "cursor",
                    "label": "Cursor MCP",
                    "start_listen": False,
                },
            )
            body = json.loads(ensured.content[0].text)
            assert body["ok"] is True
            assert body["participant_id"] == "cursor"
            assert body["room"] == "mcp-smoke"

            joined = await client.call_tool(
                "polylogue_join",
                {
                    "participant_id": "claude",
                    "label": "Claude",
                    "provider": "claude",
                    "cwd": str(repo),
                },
            )
            assert json.loads(joined.content[0].text)["ok"] is True

            said = await client.call_tool(
                "polylogue_say",
                {"text": "hello from mcp", "participant_id": "cursor", "cwd": str(repo)},
            )
            assert json.loads(said.content[0].text)["ok"] is True

            await client.call_tool(
                "polylogue_handoff",
                {
                    "text": "your turn",
                    "next_participant": "claude",
                    "participant_id": "cursor",
                    "cwd": str(repo),
                },
            )
            await client.call_tool(
                "polylogue_snapshot",
                {"summary": "mcp green", "cwd": str(repo)},
            )
            await client.call_tool(
                "polylogue_system",
                {"text": "marker", "cwd": str(repo)},
            )
            await client.call_tool(
                "polylogue_context_append",
                {"text": "## mission", "cwd": str(repo)},
            )
            await client.call_tool(
                "polylogue_memory_append",
                {"text": "- use mcp", "cwd": str(repo)},
            )
            await client.call_tool(
                "polylogue_presence_set",
                {
                    "actor_id": "cursor",
                    "state": "editing",
                    "path_specs": ["src/foo.py:edit"],
                    "cwd": str(repo),
                },
            )

            pack = await client.call_tool("polylogue_sync_pack", {"cwd": str(repo), "lines": 20})
            md = pack.content[0].text
            assert "Polylogue full sync pack" in md
            assert "hello from mcp" in md

            tail = await client.call_tool("polylogue_tail", {"cwd": str(repo), "lines": 20})
            events = json.loads(tail.content[0].text)["events"]
            assert any(e.get("text") == "hello from mcp" for e in events)

            status = await client.call_tool("polylogue_status", {"cwd": str(repo)})
            assert json.loads(status.content[0].text)["meta"]["session"] == "mcp-smoke"

            peers = await client.call_tool("polylogue_peers", {"cwd": str(repo)})
            roster_ids = {p["id"] for p in json.loads(peers.content[0].text)["roster"]}
            assert "cursor" in roster_ids
            assert "claude" in roster_ids

    asyncio.run(run())

def test_bridge_inbox_reads_listener_log(repo, no_daemon, monkeypatch):
    monkeypatch.setenv("CURSOR_AGENT", "1")
    out = sess.ensure_session(
        cwd=str(repo),
        session="inbox-sess",
        participant_id="cursor",
        start_listen=False,
    )
    assert out["ok"] is True
    log_path, _ = state_paths("cursor")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"type": "message", "from": "claude", "text": "ping peer", "room": "inbox-sess"}
    log_path.write_text(f"connected room=inbox-sess id=cursor\nrecv: {json.dumps(payload)}\n", encoding="utf-8")

    first = sess.bridge_inbox(cwd=str(repo), participant_id="cursor")
    assert first["count"] == 1
    assert first["messages"][0]["text"] == "ping peer"

    second = sess.bridge_inbox(cwd=str(repo), participant_id="cursor")
    assert second["count"] == 0

    third = sess.bridge_inbox(cwd=str(repo), participant_id="cursor", reset=True)
    assert third["count"] == 1


def test_bridge_send_to_peer(repo, no_daemon, monkeypatch):
    port = _free_port()
    monkeypatch.setenv("POLYLOGUE_BRIDGE_HOST", "127.0.0.1")
    monkeypatch.setenv("POLYLOGUE_BRIDGE_PORT", str(port))
    monkeypatch.setattr(sess, "ensure_service", lambda wait_s=6.0: True)

    server = BridgeServer(host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.start, kwargs={"foreground": True}, daemon=True)
    thread.start()
    time.sleep(0.3)

    sess.ensure_session(
        cwd=str(repo),
        session="send-sess",
        participant_id="cursor",
        start_listen=False,
    )
    sess.do_join(
        participant_id="opencode",
        label="OpenCode",
        provider="opencode",
        cwd=str(repo),
    )

    received: list[dict] = []
    base = f"ws://127.0.0.1:{port}"

    async def run() -> None:
        import websockets

        uri = f"{base}/ws?room=send-sess&id=opencode"

        async def listener() -> None:
            async with websockets.connect(uri) as ws:
                async for raw in ws:
                    data = json.loads(raw)
                    if data.get("type") == "message":
                        received.append(data)
                        return

        task = asyncio.create_task(listener())
        await asyncio.sleep(0.2)
        sess.bridge_send(
            "hello opencode",
            cwd=str(repo),
            participant_id="cursor",
            to="opencode",
        )
        await asyncio.wait_for(task, timeout=3.0)

    try:
        asyncio.run(run())
        assert received
        assert received[0]["text"] == "hello opencode"
        assert received[0]["from"] == "cursor"
    finally:
        server.stop()
        thread.join(timeout=3)


def test_mcp_workspace_file_and_scratch(repo, no_daemon):
    sess.ensure_session(
        cwd=str(repo),
        session="ws-sess",
        participant_id="cursor",
        start_listen=False,
    )
    target = repo / "tracked.txt"
    target.write_text("v1\n", encoding="utf-8")

    rec = sess.file_read("tracked.txt", actor_id="cursor", cwd=str(repo))
    assert rec["ok"] is True
    assert sess.file_assert("tracked.txt", actor_id="cursor", cwd=str(repo))["ok"] is True

    target.write_text("v2\n", encoding="utf-8")
    stale = sess.file_check(actor_id="cursor", cwd=str(repo))
    assert stale["stale_count"] == 1
    assert sess.file_assert("tracked.txt", actor_id="cursor", cwd=str(repo))["stale"] is True

    sub = sess.subagent_add(
        parent_id="cursor",
        sub_id="explore-1",
        label="Explore",
        path_specs=["src/:read"],
        cwd=str(repo),
    )
    assert sub["id"] == "explore-1"
    assert sess.subagent_list(parent="cursor", cwd=str(repo))["count"] == 1

    wrote = sess.scratch_write("notes/hello.md", "temp notes", participant_id="cursor", cwd=str(repo))
    assert Path(wrote["path"]).is_file()
    assert sess.scratch_list(cwd=str(repo))["count"] == 1
    assert sess.subagent_remove(parent_id="cursor", sub_id="explore-1", cwd=str(repo))["ok"] is True


def test_mcp_resources_prompts_and_turn_aware(repo, no_daemon):
    server = create_server()

    async def run() -> None:
        async with Client(server) as client:
            prompts = await client.list_prompts()
            prompt_names = {p.name for p in prompts.prompts}
            assert "polylogue_cohesion" in prompt_names
            assert "polylogue_turn_start" in prompt_names

            resources = await client.list_resources()
            uris = {str(r.uri) for r in resources.resources}
            assert "polylogue://sync-pack" in uris
            assert "polylogue://peers" in uris

            result = await client.call_tool(
                "polylogue_turn_aware",
                {
                    "cwd": str(repo),
                    "session": "aware-sess",
                    "participant_id": "cursor",
                    "lines": 10,
                },
            )
            body = json.loads(result.content[0].text)
            assert body["ok"] is True
            assert "Polylogue full sync pack" in body["sync_pack"]
            assert body["identity"]["participant_id"] == "cursor"

            got = await client.get_prompt("polylogue_cohesion")
            text = " ".join(
                (m.content.text if hasattr(m.content, "text") else str(m.content))
                for m in got.messages
            )
            assert "Polylogue" in text

    asyncio.run(run())
