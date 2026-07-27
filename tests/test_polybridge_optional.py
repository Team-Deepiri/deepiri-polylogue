"""Smoke tests for optional PolyBridge imports (no Redis required)."""

from __future__ import annotations

import pytest


def test_polylogue_package_imports_without_connecting() -> None:
    import polylogue
    from polylogue import models

    assert models.new_id()
    assert hasattr(polylogue, "__doc__")


def test_create_hub_requires_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    import polylogue.hub as hub

    monkeypatch.setattr(hub, "redis", None)
    with pytest.raises(RuntimeError, match="redis package required"):
        hub.create_hub()
