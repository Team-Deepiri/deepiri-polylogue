"""Journal bridge — syncs orchestration events to the deepiri_polylogue filesystem journal."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from polylogue.models import AgentState, utcnow

logger = logging.getLogger(__name__)

try:
    import fcntl  # type: ignore[import-not-found]
except ImportError:
    fcntl = None  # type: ignore[assignment]


def _journal_path(root: Path) -> Path:
    return root / "journal.jsonl"


def _append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False) + "\n"
    data = line.encode("utf-8")
    flags = os.O_CREAT | os.O_WRONLY | os.O_APPEND
    fd = os.open(path, flags, 0o644)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, data)
    finally:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


_EVENT_TYPE_MAP = {
    "utterance": "utterance",
    "handoff": "handoff",
    "snapshot": "snapshot",
    "system": "system",
    "presence": "presence",
    "task_dispatched": "system",
    "task_completed": "system",
    "task_failed": "system",
    "agent_online": "presence",
    "agent_offline": "presence",
    "agent_error": "system",
}

_AGENT_STATE_MAP = {
    AgentState.ONLINE: "online",
    AgentState.BUSY: "online",
    AgentState.DEGRADED: "online",
    AgentState.ERROR: "error",
    AgentState.TERMINATED: "offline",
    AgentState.OFFLINE: "offline",
}


class JournalBridge:
    """Bi-directional bridge between Redis orchestration and the filesystem journal."""

    def __init__(self, journal_root: str | Path | None = None):
        self._root = Path(journal_root).resolve() if journal_root else None
        self._path: Path | None = None
        self._running = False
        self._flush_thread: threading.Thread | None = None
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._flush_interval = 2.0

    @property
    def active(self) -> bool:
        return self._root is not None

    def set_root(self, path: str | Path) -> None:
        self._root = Path(path).resolve()
        self._path = _journal_path(self._root)
        self._root.mkdir(parents=True, exist_ok=True)
        logger.info(f"Journal bridge root: {self._root}")

    def detect_root(self) -> bool:
        env = os.environ.get("DEEPIRI_POLYLOGUE_ROOT", "").strip()
        if env:
            self.set_root(env)
            return True
        # Reuse an already-present repo sidecar if one exists, but NEVER create a
        # `polylogue/` (or `.deepiri/polylogue/`) directory inside a project working
        # directory. The previous logic keyed on `c.is_dir()`, and Path.cwd() is always
        # a directory, so it dropped a stray `polylogue/journal.jsonl` into whatever repo
        # the orchestrator happened to run in.
        for existing in (Path.cwd() / "polylogue", Path.cwd() / ".deepiri" / "polylogue"):
            if existing.is_dir() or (existing / "journal.jsonl").exists():
                self.set_root(existing)
                return True
        # Otherwise use the canonical per-user root resolved by deepiri_polylogue
        # (service/XDG data dir) — the same place its journal readers look. If that
        # resolver would fall back to a path INSIDE the project dir (its no-service
        # `.deepiri/polylogue` fallback), redirect to the shared data dir so we never
        # write into the working directory.
        from deepiri_polylogue.platform_detect import data_dir

        cwd = Path.cwd().resolve()
        try:
            from deepiri_polylogue.paths import polylogue_root

            root = Path(polylogue_root(cwd)).resolve()
        except Exception:
            root = (Path(data_dir()) / "polylogue").resolve()
        try:
            root.relative_to(cwd)
            # resolved inside the project dir → keep it out of the repo
            root = (Path(data_dir()) / "polylogue").resolve()
        except ValueError:
            pass
        self.set_root(root)
        return True

    def start(self) -> None:
        if not self._root or not self._path:
            logger.warning("Journal bridge: no root set, not starting")
            return
        self._running = True
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True, name="journal-flush")
        self._flush_thread.start()
        self._write_system("journal_bridge", "Journal bridge started")
        logger.info(f"Journal bridge started -> {self._path}")

    def stop(self) -> None:
        self._running = False
        if self._flush_thread and self._flush_thread.is_alive():
            self._flush_thread.join(timeout=5)
        self.flush()
        if self._path:
            self._write_system("journal_bridge", "Journal bridge stopped")

    def flush(self) -> None:
        if self._path is None:
            return
        with self._lock:
            buf = self._buffer[:]
            self._buffer.clear()
        for event in buf:
            try:
                _append_event(self._path, event)
            except Exception as e:
                logger.error(f"Journal write error: {e}")

    def _write(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._buffer.append(event)

    def _write_system(self, participant_id: str, text: str, extra: dict | None = None) -> None:
        event = {
            "id": self._new_id(),
            "ts": utcnow().isoformat(),
            "type": "system",
            "participant_id": participant_id,
            "text": text,
        }
        if extra:
            event.update(extra)
        self._write(event)

    def _flush_loop(self) -> None:
        while self._running:
            time.sleep(self._flush_interval)
            try:
                self.flush()
            except Exception as e:
                logger.error(f"Journal flush loop: {e}")

    def write_task_dispatched(self, task_id: str, description: str, agents: list[str], strategy: str) -> None:
        self._write_system("orchestrator", f"Task {task_id[:8]} dispatched", {
            "task_id": task_id,
            "description": description[:200],
            "agents": agents,
            "strategy": strategy,
        })

    def write_task_completed(self, task_id: str, description: str, results: dict, elapsed: float) -> None:
        self._write_system("orchestrator", f"Task {task_id[:8]} completed in {elapsed:.1f}s", {
            "task_id": task_id,
            "description": description[:200],
            "results": {k: str(v)[:100] for k, v in results.items()},
            "elapsed_seconds": round(elapsed, 2),
        })

    def write_task_failed(self, task_id: str, description: str, errors: dict, elapsed: float) -> None:
        self._write_system("orchestrator", f"Task {task_id[:8]} failed in {elapsed:.1f}s", {
            "task_id": task_id,
            "description": description[:200],
            "errors": errors,
            "elapsed_seconds": round(elapsed, 2),
        })

    def write_agent_state(self, agent_id: str, name: str, state: AgentState) -> None:
        self._write_system(name, f"Agent state: {state.value}", {
            "agent_id": agent_id,
            "agent_name": name,
            "agent_state": state.value,
        })

    def write_utterance(self, agent_id: str, agent_name: str, text: str) -> None:
        event = {
            "id": self._new_id(),
            "ts": utcnow().isoformat(),
            "type": "utterance",
            "participant_id": agent_id,
            "role": "assistant",
            "text": text[:2000],
        }
        self._write(event)

    def write_handoff(self, from_agent: str, to_agent: str, summary: str) -> None:
        event = {
            "id": self._new_id(),
            "ts": utcnow().isoformat(),
            "type": "handoff",
            "participant_id": from_agent,
            "next_participant": to_agent,
            "summary": summary[:500],
        }
        self._write(event)

    def write_presence(self, agents: dict[str, dict]) -> None:
        event = {
            "id": self._new_id(),
            "ts": utcnow().isoformat(),
            "type": "presence",
            "participant_id": "orchestrator",
            "text": json.dumps({
                aid: {"name": info.get("name"), "state": info.get("state"), "role": info.get("role")}
                for aid, info in agents.items()
            }),
        }
        self._write(event)

    @staticmethod
    def _new_id() -> str:
        import uuid
        return str(uuid.uuid4())
