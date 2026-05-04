from __future__ import annotations

import json
from pathlib import Path

from deepiri_polylogue.pack import render_sync_pack
from deepiri_polylogue.store import init_session
from deepiri_polylogue import workspace as ws


def test_presence_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "p"
    init_session(root, "w")
    ws.upsert_actor(
        root,
        actor_id="main",
        kind="participant",
        parent_id=None,
        label="Main",
        state="editing",
        cwd="/proj",
        paths=[("src/a.py", "edit")],
        note="on it",
    )
    ws.upsert_actor(
        root,
        actor_id="sub1",
        kind="subagent",
        parent_id="main",
        label="Explore",
        state="reading",
        cwd="/proj",
        paths=[("diri-lang/", "read")],
        note="task from parent",
    )
    doc = ws.load_presence(root)
    assert len(doc["actors"]) == 2
    assert ws.clear_subagent(root, "main", "sub1")
    assert len(ws.load_presence(root)["actors"]) == 1


def test_sync_pack_includes_presence_and_shared(tmp_path: Path) -> None:
    root = tmp_path / "p"
    init_session(root, "x")
    ws.atomic_write_text(ws.context_path(root), "# ctx\nhello shared\n")
    ws.upsert_actor(
        root,
        actor_id="p1",
        kind="participant",
        parent_id=None,
        label="L",
        state="editing",
        cwd=str(tmp_path),
        paths=[("README.md", "read")],
        note=None,
    )
    md = render_sync_pack(root, lines=5)
    assert "Who is where" in md
    assert "hello shared" in md
    assert "p1" in md


def test_parse_path_role() -> None:
    assert ws.parse_path_role("foo:read") == ("foo", "read")
    assert ws.parse_path_role("bar") == ("bar", "edit")
