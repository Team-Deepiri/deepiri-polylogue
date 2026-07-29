"""Polylogue MCP server — journal cohesion + live bridge for LLM agents."""

from __future__ import annotations

__all__ = ["create_server", "main"]


def create_server():
    from .server import create_server as _create

    return _create()


def main() -> None:
    from .server import main as _main

    _main()
