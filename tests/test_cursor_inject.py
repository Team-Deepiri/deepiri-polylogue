from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from deepiri_polylogue.bridge.delegate import build_delegate, init_delegate_identity
from deepiri_polylogue.bridge.runtimes import (
    _list_agent_ttys,
    _resolve_tty,
    inject_delegate,
    inject_user_text,
)


def test_tty_inject_writes_device(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("POLYLOGUE_BRIDGE_STATE_DIR", str(tmp_path / "state"))
    init_delegate_identity(user="tester")
    fake_tty = tmp_path / "fake_pts"
    fake_tty.write_bytes(b"")

    written: list[bytes] = []

    def fake_open(path, flags):
        assert str(path) == str(fake_tty)
        return 99

    def fake_write(fd, data):
        assert fd == 99
        written.append(data)
        return len(data)

    def fake_close(fd):
        assert fd == 99

    monkeypatch.setenv("POLYLOGUE_CURSOR_TTY", str(fake_tty))
    monkeypatch.setenv("POLYLOGUE_CURSOR_INJECT", "tty")
    with (
        patch("deepiri_polylogue.bridge.runtimes.os.open", fake_open),
        patch("deepiri_polylogue.bridge.runtimes.os.write", fake_write),
        patch("deepiri_polylogue.bridge.runtimes.os.close", fake_close),
    ):
        req = build_delegate(
            room="r",
            sender="a",
            target="b",
            prompt="hello peer",
            cwd=str(tmp_path),
            sender_provider="cursor",
        )
        result = inject_delegate(req, runtime="cursor")
    assert result["method"] == "tty"
    assert written and b"hello peer" in written[0]
    assert written[0].endswith(b"\n")


def test_inject_user_text_inbox_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("POLYLOGUE_BRIDGE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("POLYLOGUE_CURSOR_INJECT", "spawn")
    with patch("deepiri_polylogue.bridge.runtimes.shutil.which", return_value=None):
        result = inject_user_text("ping", cwd=tmp_path, runtime="cursor")
    assert result["method"] == "inbox"
    assert Path(result["inbox"]).is_file()


def test_workspace_tty_config(tmp_path, monkeypatch):
    cfg_home = tmp_path / "config"
    poly = cfg_home / "polylogue"
    poly.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
    work = tmp_path / "proj"
    work.mkdir()
    (poly / "cursor_inject.json").write_text(
        json.dumps({"workspaces": {str(work): {"tty": "pts/42", "mode": "tty"}}}),
        encoding="utf-8",
    )
    with patch("deepiri_polylogue.bridge.runtimes._list_agent_ttys", return_value=[]):
        assert _resolve_tty(work, {"tty": "pts/42"}) == "pts/42"


def test_list_agent_ttys_parses_ps():
    sample = " 123 pts/3 agent --resume\n 456 ? agent --help\n 789 pts/1 vim foo\n"
    with patch(
        "deepiri_polylogue.bridge.runtimes.subprocess.check_output",
        return_value=sample,
    ):
        rows = _list_agent_ttys()
    assert len(rows) == 1
    assert rows[0]["tty"] == "pts/3"
