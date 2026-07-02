from __future__ import annotations

from pathlib import Path

import pytest

from deepiri_polylogue import registry as reg
from deepiri_polylogue.platform_detect import data_dir
from deepiri_polylogue.service_daemon import PolylogueHandler
from deepiri_polylogue.store import init_session


@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("POLYLOGUE_LEGACY_SIDECAR", raising=False)
    return tmp_path


def test_register_workspace(isolated_data, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "proj"
    repo.mkdir()
    entry = reg.register_workspace(repo, "test-session")
    assert entry["session"] == "test-session"
    assert Path(entry["session_root"]).is_dir()
    assert reg.resolve_session_root(repo) is None  # no meta yet


def test_init_session_service_mode(isolated_data, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "proj"
    repo.mkdir()
    root = init_session(Path("."), "svc-smoke", workspace=repo, use_service=True)
    assert root.is_dir()
    assert (root / "meta.json").is_file()
    assert not (repo / ".deepiri").exists()
    resolved = reg.resolve_session_root(repo)
    assert resolved == root


def test_polylogue_root_no_sidecar(isolated_data, tmp_path, monkeypatch):
    from deepiri_polylogue.paths import polylogue_root

    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "proj"
    repo.mkdir()
    init_session(Path("."), "root-test", workspace=repo, use_service=True)
    monkeypatch.chdir(repo)
    assert polylogue_root() == reg.resolve_session_root(repo)


def test_handler_health(isolated_data):
    class FakeHandler(PolylogueHandler):
        def __init__(self):
            self.headers = {}
            self.wfile = _FakeWriter()
            self.requestline = ""
            self.request_version = "HTTP/1.1"
            self.client_address = ("127.0.0.1", 12345)

    class _FakeWriter:
        def __init__(self):
            self.payload = b""

        def write(self, data: bytes) -> int:
            self.payload += data
            return len(data)

    h = FakeHandler()
    h._send_json(200, {"ok": True})
    assert b'"ok": true' in h.wfile.payload


def test_data_dir_under_xdg(isolated_data, tmp_path):
    assert data_dir().startswith(str(tmp_path))


def test_ensure_service_noop_when_running(isolated_data, monkeypatch):
    import subprocess

    from deepiri_polylogue import service_client as sc

    monkeypatch.setattr(sc, "is_running", lambda: True)

    def _no_spawn(*args, **kwargs):
        raise AssertionError("ensure_service must not spawn a daemon when one is running")

    monkeypatch.setattr(subprocess, "Popen", _no_spawn)
    assert sc.ensure_service() is True


def test_ensure_service_spawns_when_down(isolated_data, monkeypatch):
    import subprocess

    from deepiri_polylogue import service_client as sc

    calls = {"is_running": 0}

    def _is_running():
        # First call (guard) reports down; after the spawn it reports healthy.
        calls["is_running"] += 1
        return calls["is_running"] >= 2

    spawned = {"count": 0}

    class _FakePopen:
        def __init__(self, *args, **kwargs):
            spawned["count"] += 1

    monkeypatch.setattr(sc, "is_running", _is_running)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    assert sc.ensure_service(wait_s=2.0) is True
    assert spawned["count"] == 1
