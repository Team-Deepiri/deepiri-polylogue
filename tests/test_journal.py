from __future__ import annotations

import json
from pathlib import Path

from deepiri_polylogue.journal import append_event, tail_events
from deepiri_polylogue.models import Participant, event_line
from deepiri_polylogue.participants import upsert_participant
from deepiri_polylogue.models import Participant
from deepiri_polylogue.store import init_session
from deepiri_polylogue.pack import render_sync_pack


def test_append_and_tail(tmp_path: Path) -> None:
    root = tmp_path / "p"
    init_session(root, "test")
    for i in range(5):
        append_event(root, event_line(type="utterance", participant_id="a", role="assistant", text=f"m{i}"))
    tail = tail_events(root, lines=3)
    assert len(tail) == 3
    assert tail[-1]["text"] == "m4"


def test_sync_pack_renders(tmp_path: Path) -> None:
    root = tmp_path / "p"
    init_session(root, "demo")
    upsert_participant(root, Participant(id="x", label="Claude tab", provider="anthropic"))
    append_event(root, event_line(type="utterance", participant_id="x", role="assistant", text="hello"))
    md = render_sync_pack(root, lines=10)
    assert "Claude tab" in md
    assert "hello" in md
    assert "journal.jsonl" in md
