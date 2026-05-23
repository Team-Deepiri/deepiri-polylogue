"""PolyBridge process supervisor - manages agent subprocesses with health checks & restart policies."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

from polylogue.models import AgentState

logger = logging.getLogger(__name__)

ProcessHandler = Callable[[str], None]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProcessAdapter:
    """Manages a single tool/agent subprocess with full lifecycle."""

    def __init__(
        self,
        agent_id: str,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        workdir: str = ".",
        slots: int = 1,
        startup_delay: float = 2.0,
    ):
        self.agent_id = agent_id
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.workdir = workdir
        self.slots = slots
        self.startup_delay = startup_delay

        self.state = AgentState.OFFLINE
        self.pid: int | None = None
        self.slots_used = 0
        self._process: subprocess.Popen | None = None
        self._output_handler: ProcessHandler | None = None
        self._lock = threading.Lock()
        self._readers: list[threading.Thread] = []

    def set_output_handler(self, handler: ProcessHandler | None) -> None:
        self._output_handler = handler

    def start(self) -> bool:
        with self._lock:
            if self._process and self._process.poll() is None:
                logger.warning(f"{self.name} already running (pid={self.pid})")
                return False
            cmd = [self.command] + self.args
            merged_env = os.environ.copy()
            merged_env.update(self.env)
            try:
                self.state = AgentState.STARTING
                self._process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=merged_env,
                    cwd=self.workdir,
                    text=False,
                    bufsize=1,
                    preexec_fn=os.setsid,
                )
                self.pid = self._process.pid
                logger.info(f"Started {self.name} (agent={self.agent_id[:8]}, pid={self.pid})")
                time.sleep(self.startup_delay)
                if self._process.poll() is None:
                    self.state = AgentState.ONLINE
                else:
                    self.state = AgentState.ERROR
                    return False

                self._readers = []
                for fdesc, label in [(self._process.stdout, "stdout"), (self._process.stderr, "stderr")]:
                    t = threading.Thread(
                        target=self._pipe_reader,
                        args=(fdesc, label),
                        daemon=True,
                        name=f"{self.name}-{label}",
                    )
                    t.start()
                    self._readers.append(t)
                return True
            except FileNotFoundError:
                self.state = AgentState.ERROR
                logger.error(f"{self.name}: command not found: {self.command}")
                return False
            except Exception as e:
                self.state = AgentState.ERROR
                logger.error(f"{self.name} start failed: {e}")
                return False

    def _pipe_reader(self, fdesc, label: str) -> None:
        try:
            for line in iter(fdesc.readline, b""):
                text = line.decode("utf-8", errors="replace").rstrip()
                if text and self._output_handler:
                    try:
                        self._output_handler(text)
                    except Exception:
                        pass
        except Exception:
            pass

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            if not self._process:
                return
            pid = self._process.pid
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGTERM)
                try:
                    self._process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(pgid, signal.SIGKILL)
                    self._process.wait()
            except ProcessLookupError:
                pass
            except Exception as e:
                logger.warning(f"Error stopping {self.name}: {e}")
            finally:
                self.state = AgentState.OFFLINE
                self.pid = None
                self._process = None
            logger.info(f"Stopped {self.name} (pid={pid})")

    def restart(self, timeout: float = 5.0) -> bool:
        self.stop(timeout=timeout)
        time.sleep(1)
        return self.start()

    def write(self, data: str) -> int:
        if not self._process or not self._process.stdin:
            return -1
        try:
            return self._process.stdin.write(data.encode("utf-8"))
        except Exception as e:
            logger.error(f"Write error to {self.name}: {e}")
            return -1

    def write_line(self, data: str) -> int:
        return self.write(data + "\n")

    def poll(self) -> bool:
        if not self._process:
            return True
        ret = self._process.poll()
        if ret is not None:
            with self._lock:
                self.state = AgentState.TERMINATED
                self.pid = None
            return True
        return False

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def get_load(self) -> float:
        total = max(self.slots, 1)
        return self.slots_used / total


class Supervisor:
    """Process supervisor with health checks and auto-restart policies."""

    def __init__(self, max_restarts: int = 5, restart_window: float = 60.0, health_interval: float = 15.0):
        self.max_restarts = max_restarts
        self.restart_window = restart_window
        self.health_interval = health_interval
        self._adapters: dict[str, ProcessAdapter] = {}
        self._restart_counts: dict[str, list[float]] = defaultdict(list)
        self._health_threads: dict[str, threading.Thread] = {}
        self._running = False
        self._lock = threading.Lock()
        self._on_state_change: list[Callable[[str, AgentState, AgentState], None]] = []

    def on_state_change(self, callback: Callable[[str, AgentState, AgentState], None]) -> None:
        self._on_state_change.append(callback)

    def add(self, adapter: ProcessAdapter) -> None:
        with self._lock:
            self._adapters[adapter.name] = adapter

    def remove(self, name: str) -> None:
        with self._lock:
            if name in self._adapters:
                self._adapters[name].stop()
                del self._adapters[name]

    def get(self, name: str) -> ProcessAdapter | None:
        with self._lock:
            return self._adapters.get(name)

    def list(self) -> list[str]:
        with self._lock:
            return list(self._adapters.keys())

    def start(self, name: str) -> bool:
        adapter = self.get(name)
        if not adapter:
            return False
        old_state = adapter.state
        ok = adapter.start()
        if ok and old_state != adapter.state:
            self._notify_state(name, old_state, adapter.state)
        return ok

    def start_all(self) -> dict[str, bool]:
        results = {}
        for name in self.list():
            results[name] = self.start(name)
        return results

    def stop(self, name: str, timeout: float = 5.0) -> None:
        adapter = self.get(name)
        if adapter:
            old_state = adapter.state
            adapter.stop(timeout=timeout)
            self._notify_state(name, old_state, adapter.state)

    def stop_all(self, timeout: float = 5.0) -> None:
        for name in self.list():
            self.stop(name, timeout=timeout)

    def restart(self, name: str, timeout: float = 5.0) -> bool:
        adapter = self.get(name)
        if not adapter:
            return False
        old_state = adapter.state
        ok = adapter.restart(timeout=timeout)
        self._notify_state(name, old_state, adapter.state)
        return ok

    def can_restart(self, name: str) -> bool:
        now = time.time()
        recent = [t for t in self._restart_counts[name] if now - t < self.restart_window]
        self._restart_counts[name] = recent
        return len(recent) < self.max_restarts

    def start_health_checks(self) -> None:
        self._running = True
        for name in self.list():
            t = threading.Thread(
                target=self._health_loop,
                args=(name,),
                daemon=True,
                name=f"health-{name}",
            )
            t.start()
            self._health_threads[name] = t
        logger.info(f"Health checks started for {len(self._health_threads)} agents")

    def stop_health_checks(self) -> None:
        self._running = False
        for t in self._health_threads.values():
            if t.is_alive():
                t.join(timeout=3)

    def _health_loop(self, name: str) -> None:
        while self._running:
            time.sleep(self.health_interval)
            adapter = self.get(name)
            if not adapter:
                break
            if adapter.poll():
                old_state = adapter.state
                if old_state not in (AgentState.OFFLINE, AgentState.ERROR):
                    logger.warning(f"{name} terminated, state={old_state.value}")
                    adapter.state = AgentState.TERMINATED
                    self._restart_counts[name].append(time.time())
                    if self.can_restart(name):
                        logger.info(f"Auto-restarting {name} (attempt {len(self._restart_counts[name])})")
                        ok = adapter.restart()
                        if not ok and self.can_restart(name):
                            logger.info(f"Retry restart {name}...")
                            time.sleep(2)
                            adapter.restart()
                    else:
                        logger.error(f"{name}: max restarts exceeded ({self.max_restarts}/{self.restart_window}s)")
                        adapter.state = AgentState.ERROR
                    self._notify_state(name, old_state, adapter.state)

    def status_all(self) -> dict[str, dict]:
        return {
            name: {
                "state": adapter.state.value,
                "pid": adapter.pid,
                "slots_used": adapter.slots_used,
                "slots": adapter.slots,
                "running": adapter.is_running(),
            }
            for name, adapter in self._adapters.items()
        }

    def _notify_state(self, name: str, old: AgentState, new: AgentState) -> None:
        for cb in self._on_state_change:
            try:
                cb(name, old, new)
            except Exception as e:
                logger.error(f"State change callback error: {e}")


class ToolRecord:
    """Agent metadata for registry."""

    def __init__(self, name: str, adapter: ProcessAdapter, capabilities: list[str], priority: int = 10, label: str = ""):
        self.name = name
        self.adapter = adapter
        self.capabilities = capabilities
        self.priority = priority
        self.label = label
        self.metadata: dict[str, Any] = {}


class ToolManager:
    """Manages all agent processes and their capabilities."""

    def __init__(self):
        self.supervisor = Supervisor()
        self._records: dict[str, ToolRecord] = {}
        self._lock = threading.Lock()

    def add_tool(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        workdir: str = ".",
        slots: int = 1,
        startup_delay: float = 2.0,
        capabilities: list[str] | None = None,
        priority: int = 10,
        label: str = "",
    ) -> str:
        agent_id = f"agent:{name}"
        adapter = ProcessAdapter(
            agent_id=agent_id,
            name=name,
            command=command,
            args=args,
            env=env,
            workdir=workdir,
            slots=slots,
            startup_delay=startup_delay,
        )
        with self._lock:
            self.supervisor.add(adapter)
            record = ToolRecord(
                name=name,
                adapter=adapter,
                capabilities=capabilities or [],
                priority=priority,
                label=label,
            )
            self._records[name] = record
        logger.info(f"Registered tool: {name} (capabilities={capabilities})")
        return agent_id

    def remove_tool(self, name: str) -> None:
        with self._lock:
            self.supervisor.remove(name)
            self._records.pop(name, None)

    def get_adapter(self, name: str) -> ProcessAdapter | None:
        with self._lock:
            r = self._records.get(name)
            return r.adapter if r else None

    def get_record(self, name: str) -> ToolRecord | None:
        with self._lock:
            return self._records.get(name)

    def list_tools(self) -> list[str]:
        with self._lock:
            return list(self._records.keys())

    def start_all(self) -> dict[str, bool]:
        results = self.supervisor.start_all()
        self.supervisor.start_health_checks()
        return results

    def stop_all(self, timeout: float = 5.0) -> None:
        self.supervisor.stop_health_checks()
        self.supervisor.stop_all(timeout=timeout)

    def status_all(self) -> dict[str, dict]:
        return self.supervisor.status_all()

    def find_available(self, required_capabilities: list[str] | None = None) -> list[tuple[str, ProcessAdapter, ToolRecord]]:
        results = []
        with self._lock:
            for name, rec in self._records.items():
                adapter = rec.adapter
                if adapter.state not in (AgentState.ONLINE, AgentState.DEGRADED):
                    continue
                if adapter.slots_used >= adapter.slots:
                    continue
                if required_capabilities:
                    rec_caps = set(rec.capabilities)
                    if not set(required_capabilities).issubset(rec_caps):
                        continue
                results.append((name, adapter, rec))
        results.sort(key=lambda x: x[2].priority, reverse=True)
        return results

    def allocate(self, name: str) -> bool:
        adapter = self.supervisor.get(name)
        if not adapter:
            return False
        if adapter.slots_used >= adapter.slots:
            return False
        adapter.slots_used += 1
        if adapter.slots_used >= adapter.slots:
            adapter.state = AgentState.BUSY
        return True

    def release(self, name: str) -> None:
        adapter = self.supervisor.get(name)
        if adapter and adapter.slots_used > 0:
            adapter.slots_used -= 1
            if adapter.state == AgentState.BUSY:
                adapter.state = AgentState.ONLINE

    def on_state_change(self, callback: Callable[[str, AgentState, AgentState], None]) -> None:
        self.supervisor.on_state_change(callback)


def create_tool_config(cfg: dict) -> dict:
    return {
        "command": cfg.get("command", cfg["command"]),
        "args": cfg.get("args", []),
        "env": cfg.get("env", {}),
        "workdir": cfg.get("workdir", "."),
        "slots": cfg.get("slots", 1),
        "startup_delay": cfg.get("startup_delay", 2.0),
        "capabilities": cfg.get("capabilities", []),
        "priority": cfg.get("priority", 10),
        "label": cfg.get("label", ""),
    }


def create_manager_from_config(config: dict) -> ToolManager:
    manager = ToolManager()
    for name, tool_cfg in config.get("tools", {}).items():
        if not tool_cfg.get("enabled", True):
            continue
        params = create_tool_config(tool_cfg)
        manager.add_tool(name, **params)
    return manager
