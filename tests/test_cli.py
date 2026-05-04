from __future__ import annotations

from deepiri_polylogue.cli import main


def test_cli_happy_path(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--session", "cli-smoke"]) == 0
    assert main(["join", "--id", "p1", "--label", "One", "--provider", "test"]) == 0
    assert main(["say", "--id", "p1", "--role", "assistant", "--text", "hello polylogue"]) == 0
    assert main(["handoff", "--id", "p1", "--to", "p2", "--text", "your turn"]) == 0
    assert main(["snapshot", "--summary", "state ok"]) == 0
    assert main(["system", "--text", "marker"]) == 0
    assert main(["tail", "--lines", "10"]) == 0
    assert main(["status"]) == 0
    out = main(["sync-pack", "--lines", "5"])
    assert out == 0
    assert main(["presence", "set", "--id", "p1", "--state", "editing", "--path", "cli.py:edit", "--note", "here"]) == 0
    assert main(["presence", "list", "--json"]) == 0
    assert main(["context", "append", "--text", "## checkpoint"]) == 0
    assert main(["memory", "append", "--text", "- decided X"]) == 0
    assert main(["subagent", "add", "--parent", "p1", "--id", "subx", "--label", "Worker", "--path", "tests/:read"]) == 0
    assert main(["scratch-dir", "--id", "p1"]) == 0
    assert main(["subagent", "remove", "--parent", "p1", "--id", "subx"]) == 0
