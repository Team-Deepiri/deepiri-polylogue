from __future__ import annotations

from unittest.mock import patch

from deepiri_polylogue.bridge.resolve import detect_provider, find_repo_root, resolve_bridge_context


def test_detect_provider_cursor(monkeypatch):
    monkeypatch.setenv("CURSOR_AGENT", "1")
    assert detect_provider() == "cursor"


def test_detect_provider_explicit_override(monkeypatch):
    monkeypatch.setenv("CURSOR_AGENT", "1")
    monkeypatch.setenv("POLYLOGUE_PROVIDER", "antigravity")
    assert detect_provider() == "antigravity"


def test_detect_provider_opencode_env(monkeypatch):
    monkeypatch.delenv("CURSOR_AGENT", raising=False)
    monkeypatch.delenv("POLYLOGUE_PROVIDER", raising=False)
    monkeypatch.setenv("OPENCODE_SESSION", "1")
    assert detect_provider() == "opencode"


def test_detect_provider_antigravity_env(monkeypatch):
    monkeypatch.delenv("CURSOR_AGENT", raising=False)
    monkeypatch.delenv("POLYLOGUE_PROVIDER", raising=False)
    monkeypatch.delenv("OPENCODE_SESSION", raising=False)
    monkeypatch.setenv("ANTIGRAVITY_AGENT", "1")
    assert detect_provider() == "antigravity"


def test_find_repo_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert find_repo_root() == tmp_path.resolve()


def test_resolve_without_registry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("CURSOR_AGENT", "1")
    with patch("deepiri_polylogue.bridge.resolve.reg.lookup_workspace", return_value=None):
        ctx = resolve_bridge_context(tmp_path)
    assert ctx.participant_id == "cursor"
    assert ctx.room == "default"
