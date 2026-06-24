"""Watch bridge for signed delegate tasks and inject into the local agent runtime."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

import websockets

from .delegate import verify_delegate
from .listener import _log, state_paths
from .resolve import resolve_bridge_context
from .runtimes import inject_delegate
from ..service_config import bridge_url

logger = logging.getLogger(__name__)


async def _handle_delegate(
    payload: dict[str, Any],
    participant_id: str,
    log_path: Path,
    runtime: str | None,
) -> None:
    if not verify_delegate(payload):
        _log(log_path, f"delegate reject invalid sig id={payload.get('delegate_id')}")
        return
    if str(payload.get("to")) != participant_id:
        return
    _log(log_path, f"delegate accept id={payload.get('delegate_id')} from={payload.get('from')}")
    try:
        result = inject_delegate(payload, runtime=runtime)
        _log(log_path, f"delegate injected: {json.dumps(result)}")
    except Exception as exc:
        _log(log_path, f"delegate inject failed: {exc}")
        logger.exception("delegate inject failed")


async def _watch_session(ctx, url: str, log_path: Path, runtime: str | None) -> None:
    uri = f"{url.rstrip('/')}/ws?room={quote(ctx.room)}&id={quote(ctx.participant_id)}"
    async with websockets.connect(uri) as ws:
        _log(log_path, f"delegate watch room={ctx.room} id={ctx.participant_id}")
        async for raw in ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "delegate":
                await _handle_delegate(data, ctx.participant_id, log_path, runtime)


def delegate_watch_loop(
    cwd: Path | None = None,
    *,
    participant_id: str | None = None,
    room: str | None = None,
    url: str | None = None,
    runtime: str | None = None,
) -> None:
    ctx = resolve_bridge_context(cwd, participant_id=participant_id, room=room)
    log_path, _ = state_paths(ctx.participant_id)
    target_url = url or bridge_url()
    chosen_runtime = runtime

    async def _main() -> None:
        while True:
            try:
                await _watch_session(ctx, target_url, log_path, chosen_runtime)
            except (websockets.ConnectionClosed, OSError) as exc:
                _log(log_path, f"delegate watch disconnected: {exc}")
                await asyncio.sleep(2)

    asyncio.run(_main())
