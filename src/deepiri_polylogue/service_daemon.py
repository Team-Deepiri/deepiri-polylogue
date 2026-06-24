"""Background HTTP service for workspace-scoped polylogue sessions."""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import registry as reg
from .platform_detect import data_dir
from .fsutil import ensure_dir
from .service_config import pid_path, service_host, service_port

logger = logging.getLogger(__name__)


class PolylogueHandler(BaseHTTPRequestHandler):
    server_version = "deepiri-polylogue/0.2"

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _validated_cwd(self, raw_cwd: str) -> Path | None:
        base = Path.cwd().resolve()
        candidate = Path(raw_cwd).expanduser()
        if not candidate.is_absolute():
            candidate = (base / candidate).resolve()
        else:
            candidate = candidate.resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            return None
        return candidate

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(200, {"ok": True, "data_dir": data_dir()})
            return
        if parsed.path == "/resolve":
            qs = parse_qs(parsed.query)
            cwd_list = qs.get("cwd", [str(Path.cwd())])
            cwd = self._validated_cwd(cwd_list[0])
            if cwd is None:
                self._send_json(400, {"error": "invalid cwd"})
                return
            entry = reg.lookup_workspace(cwd)
            root = reg.resolve_session_root(cwd)
            if not entry or not root:
                self._send_json(404, {"error": "workspace not registered", "cwd": str(cwd)})
                return
            self._send_json(200, {**entry, "session_root": str(root)})
            return
        if parsed.path == "/registry":
            self._send_json(200, {"workspaces": reg.list_workspaces()})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/register":
            body = self._read_json()
            cwd = Path(body.get("cwd", str(Path.cwd())))
            session = str(body.get("session", "default"))
            entry = reg.register_workspace(cwd, session)
            self._send_json(200, entry)
            return
        self._send_json(404, {"error": "not found"})


class PolylogueService:
    def __init__(self, host: str | None = None, port: int | None = None):
        self.host = host or service_host()
        self.port = port or service_port()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self, *, foreground: bool = False) -> None:
        ensure_dir(Path(data_dir()))
        self._httpd = ThreadingHTTPServer((self.host, self.port), PolylogueHandler)
        pid_path().write_text(str(os.getpid()), encoding="utf-8")
        logger.info("Polylogue service listening on %s:%s (data: %s)", self.host, self.port, data_dir())
        if foreground:
            self._install_signal_handlers()
            self._httpd.serve_forever()
        else:
            self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True, name="polylogue-service")
            self._thread.start()

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        if pid_path().is_file():
            pid_path().unlink(missing_ok=True)

    def _install_signal_handlers(self) -> None:
        def _shutdown(signum: int, _frame: Any) -> None:
            logger.info("Signal %s — shutting down", signum)
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)


def run_foreground() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    PolylogueService().start(foreground=True)


if __name__ == "__main__":
    run_foreground()
