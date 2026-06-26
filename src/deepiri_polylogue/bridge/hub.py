"""In-memory room hub for live chat fan-out."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .protocol import encode, envelope

logger = logging.getLogger(__name__)


class RoomHub:
    def __init__(self) -> None:
        self._rooms: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def join(self, room: str, participant_id: str, ws: Any) -> None:
        async with self._lock:
            peers = self._rooms.setdefault(room, {})
            previous = peers.get(participant_id)
            if previous is not None and previous is not ws:
                try:
                    await previous.close(code=4000, reason="replaced by new connection")
                except Exception:
                    logger.debug("could not close previous socket for %s", participant_id, exc_info=True)
            peers[participant_id] = ws
        await self.broadcast(
            room,
            envelope("join", room=room, sender=participant_id),
            exclude=participant_id,
        )

    async def leave(self, room: str, participant_id: str) -> None:
        async with self._lock:
            peers = self._rooms.get(room)
            if not peers:
                return
            peers.pop(participant_id, None)
            if not peers:
                self._rooms.pop(room, None)
        await self.broadcast(
            room,
            envelope("leave", room=room, sender=participant_id),
            exclude=None,
        )

    async def handle_client_message(
        self,
        room: str,
        sender_id: str,
        data: dict[str, Any],
    ) -> None:
        msg_type = data.get("type", "message")
        if msg_type == "ping":
            await self._send_to(room, sender_id, envelope("pong", room=room, sender="bridge"))
            return
        if msg_type == "delegate":
            target = data.get("to")
            prompt = str(data.get("prompt", "")).strip()
            if not target or not prompt:
                return
            outbound = {**data, "type": "delegate", "room": room, "from": sender_id}
            await self._send_to(room, str(target), outbound)
            await self._send_to(
                room,
                sender_id,
                envelope(
                    "ack",
                    room=room,
                    sender="bridge",
                    delivered_to=target,
                    delegate_id=data.get("delegate_id"),
                ),
            )
            return
        if msg_type != "message":
            return
        text = str(data.get("text", ""))
        if not text:
            return
        target = data.get("to")
        outbound = envelope("message", room=room, sender=sender_id, text=text, to=target)
        if target:
            await self._send_to(room, str(target), outbound)
            await self._send_to(room, sender_id, envelope("ack", room=room, sender="bridge", delivered_to=target))
        else:
            await self.broadcast(room, outbound, exclude=sender_id)

    async def broadcast(self, room: str, payload: dict[str, Any], *, exclude: str | None) -> None:
        async with self._lock:
            peers = dict(self._rooms.get(room, {}))
        raw = encode(payload)
        for pid, ws in peers.items():
            if exclude and pid == exclude:
                continue
            try:
                await ws.send(raw)
            except Exception:
                logger.debug("broadcast to %s failed", pid, exc_info=True)

    async def _send_to(self, room: str, participant_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            ws = self._rooms.get(room, {}).get(participant_id)
        if ws is None:
            return
        try:
            await ws.send(encode(payload))
        except Exception:
            logger.debug("send to %s failed", participant_id, exc_info=True)

    def stats(self) -> dict[str, Any]:
        return {
            "rooms": {room: sorted(peers.keys()) for room, peers in self._rooms.items()},
            "connections": sum(len(peers) for peers in self._rooms.values()),
        }
