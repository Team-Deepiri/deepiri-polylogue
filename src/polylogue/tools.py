"""Tool process manager - manages AI coding tool subprocesses."""
import asyncio
import json
import logging
import os
import shlex
import signal
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable


logger = logging.getLogger(__name__)


class ToolState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    BUSY = "busy"
    ERROR = "error"
    TERMINATED = "terminated"


@dataclass
class ToolConfig:
    name: str
    type: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    workdir: str = "."
    startup_delay: int = 2
    health_check: dict = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)
    slots: int = 1
    priority: int = 10


@dataclass
class ToolStatus:
    name: str
    state: ToolState = ToolState.STOPPED
    pid: int | None = None
    last_heartbeat: datetime | None = None
    error: str | None = None
    slots_used: int = 0
    metadata: dict = field(default_factory=dict)


class ProcessAdapter:
    def __init__(self, config: ToolConfig):
        self.config = config
        self.status = ToolStatus(name=config.name)
        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._output_callback: Callable[[str], None] | None = None

    def set_output_handler(self, callback: Callable[[str], None]) -> None:
        self._output_callback = callback

    def start(self) -> bool:
        if self._process and self._process.poll() is None:
            logger.warning(f"{self.config.name} already running")
            return False

        cmd = [self.config.command] + self.config.args
        env = os.environ.copy()
        env.update(self.config.env)

        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=self.config.workdir,
                text=False,
                bufsize=1,
            )
            self.status.pid = self._process.pid
            self.status.state = ToolState.RUNNING
            logger.info(f"Started {self.config.name} (pid={self._process.pid})")
            
            self._reader_thread = threading.Thread(
                target=self._read_output,
                daemon=True,
            )
            self._reader_thread.start()
            return True

        except FileNotFoundError:
            self.status.state = ToolState.ERROR
            self.status.error = f"Command not found: {self.config.command}"
            logger.error(f"{self.config.name}: {self.status.error}")
            return False
        except Exception as e:
            self.status.state = ToolState.ERROR
            self.status.error = str(e)
            logger.error(f"{self.config.name} failed to start: {e}")
            return False

    def _read_output(self) -> None:
        if not self._process:
            return
        
        import select
        
        fd_set = [self._process.stdout.fileno(), self._process.stderr.fileno()]
        
        while self._process and self._process.poll() is None:
            ready, _, _ = select.select(fd_set, [], [], 1)
            for fd in ready:
                if fd == self._process.stdout.fileno():
                    line = self._process.stdout.readline()
                    if line and self._output_callback:
                        self._output_callback(line.decode("utf-8", errors="replace"))
                elif fd == self._process.stderr.fileno():
                    line = self._process.stderr.readline()
                    if line and self._output_callback:
                        self._output_callback(line.decode("utf-8", errors="replace"))

    def stop(self, timeout: int = 5) -> None:
        if not self._process:
            return

        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        except Exception as e:
            logger.warning(f"Error stopping {self.config.name}: {e}")
        finally:
            self.status.state = ToolState.STOPPED
            self.status.pid = None

    def write(self, data: str) -> int:
        if not self._process or not self._process.stdin:
            return -1
        try:
            return self._process.stdin.write(data.encode("utf-8"))
        except Exception as e:
            logger.error(f"Write error to {self.config.name}: {e}")
            return -1

    def write_line(self, data: str) -> int:
        return self.write(data + "\n")

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def poll(self) -> bool:
        if not self._process:
            return False
        
        ret = self._process.poll() is not None
        if ret:
            self.status.state = ToolState.TERMINATED
        return ret

    def get_metadata(self) -> dict:
        return {
            "name": self.config.name,
            "type": self.config.type,
            "command": self.config.command,
            "args": self.config.args,
            "capabilities": self.config.capabilities,
            "slots": self.config.slots,
            "priority": self.config.priority,
            "state": self.status.state.value,
            "pid": self.status.pid,
        }


class ToolManager:
    def __init__(self):
        self._tools: dict[str, ProcessAdapter] = {}
        self._health_threads: dict[str, threading.Thread] = {}

    def add_tool(self, config: ToolConfig) -> ProcessAdapter:
        adapter = ProcessAdapter(config)
        self._tools[config.name] = adapter
        logger.info(f"Registered tool: {config.name}")
        return adapter

    def remove_tool(self, name: str) -> None:
        if name in self._tools:
            self._tools[name].stop()
            del self._tools[name]

    def get_tool(self, name: str) -> ProcessAdapter | None:
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def start_tool(self, name: str) -> bool:
        tool = self._tools.get(name)
        if not tool:
            logger.error(f"Tool not found: {name}")
            return False
        return tool.start()

    def start_all(self) -> dict[str, bool]:
        results = {}
        for name, tool in self._tools.items():
            results[name] = tool.start()
        return results

    def stop_tool(self, name: str, timeout: int = 5) -> None:
        tool = self._tools.get(name)
        if tool:
            tool.stop(timeout=timeout)

    def stop_all(self, timeout: int = 5) -> None:
        for tool in self._tools.values():
            tool.stop(timeout=timeout)

    def get_status(self, name: str) -> ToolStatus | None:
        tool = self._tools.get(name)
        return tool.status if tool else None

    def list_status(self) -> dict[str, ToolStatus]:
        return {name: tool.status for name, tool in self._tools.items()}

    def find_available(self, capabilities: list[str] | None = None) -> list[ProcessAdapter]:
        available = []
        for tool in self._tools.values():
            if tool.status.state != ToolState.RUNNING:
                continue
            if capabilities:
                tool_caps = set(tool.config.capabilities)
                required = set(capabilities)
                if not required.issubset(tool_caps):
                    continue
            if tool.status.slots_used < tool.config.slots:
                available.append(tool)
        
        available.sort(key=lambda t: t.config.priority, reverse=True)
        return available

    def allocate(self, name: str) -> bool:
        tool = self._tools.get(name)
        if not tool:
            return False
        if tool.status.slots_used >= tool.config.slots:
            return False
        tool.status.slots_used += 1
        if tool.status.slots_used >= tool.config.slots:
            tool.status.state = ToolState.BUSY
        return True

    def release(self, name: str) -> None:
        tool = self._tools.get(name)
        if tool and tool.status.slots_used > 0:
            tool.status.slots_used -= 1
            if tool.status.state == ToolState.BUSY:
                tool.status.state = ToolState.RUNNING

    def get_metadata(self) -> list[dict]:
        return [tool.get_metadata() for tool in self._tools.values()]


def create_tool(config_dict: dict) -> ToolConfig:
    return ToolConfig(
        name=config_dict["name"],
        type=config_dict.get("type", "cli"),
        command=config_dict["command"],
        args=config_dict.get("args", []),
        env=config_dict.get("env", {}),
        workdir=config_dict.get("workdir", "."),
        startup_delay=config_dict.get("startup_delay", 2),
        health_check=config_dict.get("health_check", {}),
        capabilities=config_dict.get("capabilities", []),
        slots=config_dict.get("slots", 1),
        priority=config_dict.get("priority", 10),
    )


def create_manager_from_config(config: dict) -> ToolManager:
    manager = ToolManager()
    for name, tool_config in config.get("tools", {}).items():
        tool_config["name"] = name
        manager.add_tool(create_tool(tool_config))
    return manager