"""WebSocket server for real-time polylogue chat."""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse

from .hub import RoomHub
from .protocol import parse_inbound
from ..service_config import bridge_host, bridge_port, bridge_url

logger = logging.getLogger(__name__)

_active: "BridgeServer | None" = None


def get_active_bridge() -> "BridgeServer | None":
    return _active


class BridgeServer:
    def __init__(self, host: str | None = None, port: int | None = None) -> None:
        self.host = host or bridge_host()
        self.port = port or bridge_port()
        self.hub = RoomHub()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutdown: asyncio.Event | None = None

    def status(self) -> dict[str, Any]:
        stats = self.hub.stats()
        return {
            "ok": True,
            "url": bridge_url(),
            "host": self.host,
            "port": self.port,
            **stats,
        }

    async def _connection(self, websocket: Any) -> None:
        if hasattr(websocket, "request"):
            path = websocket.request.path
        else:
            path = websocket.path
        parsed = urlparse(path)
        if parsed.path != "/ws":
            await websocket.close(code=4404, reason="use /ws")
            return
        qs = parse_qs(parsed.query)
        room_list = qs.get("room", qs.get("session", []))
        id_list = qs.get("id", qs.get("participant", []))
        if not room_list or not id_list:
            await websocket.close(code=4400, reason="room and id query params required")
            return
        room = room_list[0].strip()
        participant_id = id_list[0].strip()
        if not room or not participant_id:
            await websocket.close(code=4400, reason="room and id must be non-empty")
            return

        await self.hub.join(room, participant_id, websocket)
        logger.info("bridge join room=%s id=%s", room, participant_id)
        try:
            async for raw in websocket:
                try:
                    data = parse_inbound(raw)
                except (ValueError, TypeError):
                    continue
                await self.hub.handle_client_message(room, participant_id, data)
        finally:
            await self.hub.leave(room, participant_id)
            logger.info("bridge leave room=%s id=%s", room, participant_id)

    async def _serve(self) -> None:
        import websockets

        self._shutdown = asyncio.Event()
        async with websockets.serve(self._connection, self.host, self.port):
            logger.info("Polylogue chat bridge listening on ws://%s:%s/ws", self.host, self.port)
            await self._shutdown.wait()

    def start(self, *, foreground: bool = False) -> None:
        global _active
        _active = self

        def _run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._serve())
            finally:
                self._loop.close()
                self._loop = None

        if foreground:
            _run()
        else:
            self._thread = threading.Thread(target=_run, daemon=True, name="polylogue-bridge")
            self._thread.start()

    def stop(self) -> None:
        global _active
        if self._loop and self._loop.is_running() and self._shutdown is not None:
            self._loop.call_soon_threadsafe(self._shutdown.set)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        if _active is self:
            _active = None
