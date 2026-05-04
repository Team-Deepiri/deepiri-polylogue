from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
import uuid


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


EventType = Literal["utterance", "handoff", "snapshot", "system", "presence"]


def new_event_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Participant:
    id: str
    label: str
    provider: str = "unknown"
    last_seen: str = field(default_factory=utc_now_iso)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "provider": self.provider,
            "last_seen": self.last_seen,
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> Participant:
        return Participant(
            id=str(d["id"]),
            label=str(d.get("label", d["id"])),
            provider=str(d.get("provider", "unknown")),
            last_seen=str(d.get("last_seen", utc_now_iso())),
        )


def event_line(
    *,
    type: EventType,
    participant_id: str | None = None,
    role: str | None = None,
    text: str | None = None,
    next_participant: str | None = None,
    summary: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": new_event_id(),
        "ts": utc_now_iso(),
        "type": type,
    }
    if participant_id is not None:
        row["participant_id"] = participant_id
    if role is not None:
        row["role"] = role
    if text is not None:
        row["text"] = text
    if next_participant is not None:
        row["next_participant"] = next_participant
    if summary is not None:
        row["summary"] = summary
    if extra:
        row.update(extra)
    return row
