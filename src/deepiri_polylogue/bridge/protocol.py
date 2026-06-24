"""Wire format for the polylogue chat bridge."""
from __future__ import annotations

import json
from typing import Any

from ..models import utc_now_iso


def envelope(msg_type: str, *, room: str, sender: str, **fields: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": msg_type,
        "room": room,
        "from": sender,
        "ts": utc_now_iso(),
    }
    payload.update(fields)
    return payload


def parse_inbound(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("message must be a JSON object")
    return data


def encode(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)
