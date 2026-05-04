from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Participant, utc_now_iso


def participants_path(root: Path) -> Path:
    return root / "participants.json"


def load_participants(root: Path) -> list[Participant]:
    path = participants_path(root)
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("participants", [])
    return [Participant.from_json(x) for x in items]


def save_participants(root: Path, people: list[Participant]) -> None:
    path = participants_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "participants": [p.to_json() for p in people],
        "updated_at": utc_now_iso(),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def upsert_participant(root: Path, p: Participant) -> list[Participant]:
    people = load_participants(root)
    by_id = {x.id: x for x in people}
    by_id[p.id] = p
    out = sorted(by_id.values(), key=lambda x: x.label.lower())
    save_participants(root, out)
    return out


def touch_participant(root: Path, participant_id: str) -> None:
    people = load_participants(root)
    changed = False
    for i, p in enumerate(people):
        if p.id == participant_id:
            people[i] = Participant(
                id=p.id,
                label=p.label,
                provider=p.provider,
                last_seen=utc_now_iso(),
            )
            changed = True
            break
    if changed:
        save_participants(root, people)
