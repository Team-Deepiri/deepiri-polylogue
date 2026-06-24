from __future__ import annotations

import time
from pathlib import Path

from deepiri_polylogue import filetime as ft
from deepiri_polylogue.pack import render_sync_pack
from deepiri_polylogue.store import init_session


def test_record_and_detect_stale(tmp_path: Path) -> None:
    root = tmp_path / "p"
    init_session(root, "ft", use_service=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "a.py"
    target.write_text("v1\n", encoding="utf-8")

    ft.record_read(root, actor_id="cursor", path="a.py", cwd=repo)
    assert ft.list_stale(root) == []

    time.sleep(0.05)
    target.write_text("v2\n", encoding="utf-8")
    stale = ft.list_stale(root)
    assert len(stale) == 1
    assert stale[0].actor_id == "cursor"
    assert "modified since it was last read" in stale[0].format_message()
    assert stale[0].last_modification >= stale[0].read_at


def test_assert_fresh_requires_read(tmp_path: Path) -> None:
    root = tmp_path / "p"
    init_session(root, "ft", use_service=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "b.py"
    target.write_text("x\n", encoding="utf-8")

    try:
        ft.assert_fresh(root, actor_id="oc", path="b.py", cwd=repo)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "must read file" in str(e)


def test_assert_fresh_detects_external_edit(tmp_path: Path) -> None:
    root = tmp_path / "p"
    init_session(root, "ft", use_service=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "c.py"
    target.write_text("one\n", encoding="utf-8")
    ft.record_read(root, actor_id="oc", path="c.py", cwd=repo)

    time.sleep(0.05)
    target.write_text("two\n", encoding="utf-8")
    stale = ft.assert_fresh(root, actor_id="oc", path="c.py", cwd=repo)
    assert stale is not None
    assert "Last modification:" in stale.format_message()


def test_sync_pack_includes_stale_section(tmp_path: Path) -> None:
    root = tmp_path / "p"
    init_session(root, "ft", use_service=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "d.py"
    target.write_text("a\n", encoding="utf-8")
    ft.record_read(root, actor_id="p1", path="d.py", cwd=repo)
    time.sleep(0.05)
    target.write_text("b\n", encoding="utf-8")

    md = render_sync_pack(root, lines=3)
    assert "Stale reads" in md
    assert "d.py" in md
