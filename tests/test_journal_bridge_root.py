from __future__ import annotations

from polylogue.journal_bridge import JournalBridge


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("DEEPIRI_POLYLOGUE_ROOT", raising=False)
    monkeypatch.delenv("POLYLOGUE_LEGACY_SIDECAR", raising=False)


def test_detect_root_never_writes_into_project_dir(monkeypatch, tmp_path):
    """No env/sidecar → journal root must land in the per-user data dir, never the cwd."""
    _isolate(monkeypatch, tmp_path)
    project = tmp_path / "some-project"
    project.mkdir()
    monkeypatch.chdir(project)

    jb = JournalBridge()
    assert jb.detect_root() is True

    # Never create a stray sidecar inside the working directory.
    assert not (project / "polylogue").exists()
    assert not (project / ".deepiri").exists()
    # Root resolves outside the project dir.
    try:
        jb._root.relative_to(project.resolve())
        raise AssertionError(f"journal root {jb._root} is inside the project dir {project}")
    except ValueError:
        pass


def test_detect_root_reuses_existing_sidecar(monkeypatch, tmp_path):
    """An already-present repo sidecar is honored (backwards compat)."""
    _isolate(monkeypatch, tmp_path)
    project = tmp_path / "legacy-project"
    project.mkdir()
    sidecar = project / "polylogue"
    sidecar.mkdir()
    monkeypatch.chdir(project)

    jb = JournalBridge()
    assert jb.detect_root() is True
    assert jb._root == sidecar.resolve()


def test_detect_root_honors_env_override(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    override = tmp_path / "explicit-root"
    monkeypatch.setenv("DEEPIRI_POLYLOGUE_ROOT", str(override))
    monkeypatch.chdir(tmp_path)

    jb = JournalBridge()
    assert jb.detect_root() is True
    assert jb._root == override.resolve()
