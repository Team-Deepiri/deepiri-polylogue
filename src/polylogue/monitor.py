"""Resource monitor — tracks CPU/memory usage per agent process."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ResourceSnapshot:
    pid: int | None
    cpu_percent: float = 0.0
    memory_rss_mb: float = 0.0
    memory_vms_mb: float = 0.0
    num_threads: int = 0
    open_fds: int = 0
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_rss_mb": round(self.memory_rss_mb, 1),
            "memory_vms_mb": round(self.memory_vms_mb, 1),
            "num_threads": self.num_threads,
            "open_fds": self.open_fds,
            "timestamp": self.timestamp,
        }


def _read_proc_status(pid: int) -> dict[str, str]:
    result = {}
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if ":" in line:
                    k, v = line.split(":", 1)
                    result[k.strip()] = v.strip()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        pass
    return result


def _read_proc_stat(pid: int) -> dict[str, str]:
    result = {}
    try:
        with open(f"/proc/{pid}/stat") as f:
            parts = f.read().split()
            if len(parts) > 22:
                result["utime"] = parts[13]
                result["stime"] = parts[14]
                result["cutime"] = parts[15]
                result["cstime"] = parts[16]
                result["num_threads"] = parts[19]
                result["starttime"] = parts[21]
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        pass
    return result


def _read_proc_io(pid: int) -> dict[str, str]:
    result = {}
    try:
        with open(f"/proc/{pid}/io") as f:
            for line in f:
                if ":" in line:
                    k, v = line.split(":", 1)
                    result[k.strip()] = v.strip()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        pass
    return result


def _read_proc_fds(pid: int) -> int:
    try:
        return len(os.listdir(f"/proc/{pid}/fd"))
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return 0


def snapshot_process(pid: int | None) -> ResourceSnapshot:
    snap = ResourceSnapshot(pid=pid, timestamp=time.time())
    if pid is None or pid <= 0:
        return snap
    try:
        status = _read_proc_status(pid)
        stat = _read_proc_stat(pid)
        vm_rss_kb = status.get("VmRSS", "0 kB").split()[0]
        vm_size_kb = status.get("VmSize", "0 kB").split()[0]
        snap.memory_rss_mb = float(vm_rss_kb) / 1024.0 if vm_rss_kb.isdigit() else 0.0
        snap.memory_vms_mb = float(vm_size_kb) / 1024.0 if vm_size_kb.isdigit() else 0.0
        snap.num_threads = int(stat.get("num_threads", 0))
        snap.open_fds = _read_proc_fds(pid)
    except Exception as e:
        logger.debug(f"Failed to snapshot pid {pid}: {e}")
    return snap


class ResourceMonitor:
    """Periodically snapshots resource usage for all managed agent processes."""

    def __init__(self, interval: float = 10.0):
        self.interval = interval
        self._pids: dict[str, int | None] = {}
        self._history: dict[str, list[ResourceSnapshot]] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    def track(self, name: str, pid: int | None) -> None:
        with self._lock:
            self._pids[name] = pid
            self._history.setdefault(name, [])

    def untrack(self, name: str) -> None:
        with self._lock:
            self._pids.pop(name, None)
            self._history.pop(name, None)

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="resource-monitor")
        self._thread.start()
        logger.info(f"Resource monitor started (interval={self.interval}s)")

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("Resource monitor stopped")

    def _loop(self) -> None:
        while self._running:
            self._snapshot_all()
            time.sleep(self.interval)

    def _snapshot_all(self) -> None:
        with self._lock:
            for name, pid in self._pids.items():
                snap = snapshot_process(pid)
                self._history[name].append(snap)
                if len(self._history[name]) > 100:
                    self._history[name] = self._history[name][-100:]

    def get_latest(self, name: str) -> ResourceSnapshot | None:
        with self._lock:
            hist = self._history.get(name, [])
            return hist[-1] if hist else None

    def get_history(self, name: str, count: int = 10) -> list[ResourceSnapshot]:
        with self._lock:
            hist = self._history.get(name, [])
            return hist[-count:]

    def get_all_latest(self) -> dict[str, dict]:
        result = {}
        with self._lock:
            for name in self._pids:
                snap = self.get_latest(name)
                result[name] = snap.to_dict() if snap else {"pid": None}
        return result

    def summary(self) -> dict[str, Any]:
        latest = self.get_all_latest()
        total_rss = sum(p.get("memory_rss_mb", 0) for p in latest.values())
        total_vms = sum(p.get("memory_vms_mb", 0) for p in latest.values())
        return {
            "agents": latest,
            "total_rss_mb": round(total_rss, 1),
            "total_vms_mb": round(total_vms, 1),
            "agent_count": len(latest),
        }
