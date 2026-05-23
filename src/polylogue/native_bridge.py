"""Native bridge — launches and manages the polybridge C TCP fan-out hub."""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _find_binary() -> str | None:
    locations = [
        os.environ.get("POLYBRIDGE_BIN", ""),
        "native/polybridge/polybridge",
        "./polybridge",
        os.path.expanduser("~/.local/bin/polybridge"),
        "/usr/local/bin/polybridge",
        "/usr/bin/polybridge",
    ]
    for loc in locations:
        if loc and Path(loc).is_file() and os.access(loc, os.X_OK):
            return str(Path(loc).resolve())
    which = shutil.which("polybridge")
    if which:
        return which
    build_dir = Path.cwd() / "native" / "polybridge"
    for candidate in [build_dir / "polybridge", build_dir.parent / "polybridge"]:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    return None


def _build_binary() -> bool:
    makefile = Path.cwd() / "native" / "polybridge" / "Makefile"
    if not makefile.exists():
        return False
    try:
        result = subprocess.run(
            ["make", "-C", str(makefile.parent)],
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception as e:
        logger.warning(f"Failed to build polybridge: {e}")
        return False


class PolyBridgeProcess:
    """Manages the native polybridge TCP fan-out hub subprocess."""

    def __init__(
        self,
        bind_host: str = "127.0.0.1",
        port: int = 7847,
        binary_path: str | None = None,
        auto_build: bool = True,
    ):
        self.bind_host = bind_host
        self.port = port
        self.binary_path = binary_path or ""
        self.auto_build = auto_build
        self._process: subprocess.Popen | None = None
        self._monitor_thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    def start(self) -> bool:
        with self._lock:
            if self.is_running:
                logger.warning("PolyBridge already running")
                return True
            binary = self.binary_path or _find_binary() if not _find_binary() else _find_binary()
            if not binary:
                if self.auto_build and _build_binary():
                    binary = _find_binary()
            if not binary:
                logger.error("PolyBridge binary not found. Build with: cd native/polybridge && make")
                return False
            try:
                self._process = subprocess.Popen(
                    [binary, "-l", self.bind_host, "-p", str(self.port)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid,
                )
                logger.info(f"PolyBridge native hub started (pid={self._process.pid}, {self.bind_host}:{self.port})")
                self._running = True
                self._monitor_thread = threading.Thread(
                    target=self._monitor_loop, daemon=True, name="native-bridge-monitor"
                )
                self._monitor_thread.start()
                return True
            except FileNotFoundError:
                logger.error(f"PolyBridge binary not found: {binary}")
                return False
            except Exception as e:
                logger.error(f"Failed to start polybridge: {e}")
                return False

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            if not self._process:
                return
            self._running = False
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
                logger.warning(f"Error stopping polybridge: {e}")
            finally:
                self._process = None
            logger.info(f"PolyBridge native hub stopped (pid={pid})")

    def restart(self, timeout: float = 5.0) -> bool:
        self.stop(timeout=timeout)
        time.sleep(1)
        return self.start()

    def _monitor_loop(self) -> None:
        while self._running and self._process:
            ret = self._process.poll()
            if ret is not None:
                logger.warning(f"PolyBridge native hub terminated (exit={ret})")
                self._process = None
                break
            time.sleep(5)

    def get_stats(self) -> dict[str, Any]:
        return {
            "running": self.is_running,
            "pid": self.pid,
            "bind_host": self.bind_host,
            "port": self.port,
            "binary": self.binary_path or _find_binary() or "not found",
        }

    def read_stderr(self) -> str:
        if not self._process or not self._process.stderr:
            return ""
        try:
            lines = []
            while True:
                line = self._process.stderr.readline()
                if not line:
                    break
                lines.append(line.decode("utf-8", errors="replace").rstrip())
            return "\n".join(lines)
        except Exception:
            return ""
