"""Transport layer — TLS/mTLS, connection pooling, wire protocol for agent comms."""

from __future__ import annotations

import json
import logging
import socket
import ssl
import threading
from dataclasses import dataclass
from typing import Any, Callable


logger = logging.getLogger(__name__)


@dataclass
class TLSConfig:
    cert_path: str = ""
    key_path: str = ""
    ca_cert_path: str = ""
    verify_mode: str = "required"
    min_version: str = "TLSv1.2"
    ciphers: str = "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256"

    @property
    def enabled(self) -> bool:
        return bool(self.cert_path and self.key_path)

    @property
    def mutual(self) -> bool:
        return self.enabled and bool(self.ca_cert_path)

    def create_server_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(self.cert_path, self.key_path)
        if self.mutual:
            ctx.load_verify_locations(self.ca_cert_path)
            if self.verify_mode == "required":
                ctx.verify_mode = ssl.CERT_REQUIRED
            elif self.verify_mode == "optional":
                ctx.verify_mode = ssl.CERT_OPTIONAL
        ctx.minimum_version = getattr(ssl.TLSVersion, self.min_version.replace(".", "_"), ssl.TLSVersion.TLSv1_2)
        if self.ciphers:
            ctx.set_ciphers(self.ciphers)
        return ctx

    def create_client_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        if self.cert_path and self.key_path:
            ctx.load_cert_chain(self.cert_path, self.key_path)
        if self.ca_cert_path:
            ctx.load_verify_locations(self.ca_cert_path)
        else:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx


class WireMessage:
    """Binary wire format for agent-to-agent communication over TCP/TLS.

    Frame: [4-byte length][1-byte type][payload]
    """

    TYPE_TASK = 0x01
    TYPE_RESULT = 0x02
    TYPE_HEARTBEAT = 0x03
    TYPE_REGISTER = 0x04
    TYPE_CHUNK = 0x05
    TYPE_SYNC = 0x06

    def __init__(self, msg_type: int, payload: dict[str, Any]):
        self.type = msg_type
        self.payload = payload

    def encode(self) -> bytes:
        body = json.dumps(self.payload).encode("utf-8")
        header = len(body).to_bytes(4, "big") + self.type.to_bytes(1, "big")
        return header + body

    @classmethod
    def decode(cls, data: bytes) -> WireMessage:
        msg_type = data[4]
        body = data[5:]
        return cls(msg_type, json.loads(body.decode("utf-8")))

    @classmethod
    def from_frame(cls, data: bytes) -> WireMessage | None:
        if len(data) < 5:
            return None
        length = int.from_bytes(data[:4], "big")
        if len(data) < 5 + length:
            return None
        return cls.decode(data[:5 + length])


class WireConnection:
    """A single TCP/TLS connection with framed message read/write."""

    def __init__(self, sock: socket.socket, tls_config: TLSConfig | None = None):
        self._sock = sock
        self._tls_config = tls_config
        self._buf = b""
        self._lock = threading.Lock()

    @classmethod
    def connect(cls, host: str, port: int, tls_config: TLSConfig | None = None, timeout: float = 10.0) -> WireConnection:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        if tls_config and tls_config.enabled:
            ctx = tls_config.create_client_context()
            sock = ctx.wrap_socket(sock, server_hostname=host)
        sock.connect((host, port))
        return cls(sock, tls_config)

    def send(self, msg: WireMessage) -> None:
        data = msg.encode()
        with self._lock:
            self._sock.sendall(data)

    def recv(self, timeout: float = 1.0) -> WireMessage | None:
        self._sock.settimeout(timeout)
        try:
            data = self._sock.recv(4096)
            if not data:
                return None
            self._buf += data
        except socket.timeout:
            pass
        except ConnectionResetError:
            return None
        if len(self._buf) < 5:
            return None
        length = int.from_bytes(self._buf[:4], "big")
        frame_size = 5 + length
        if len(self._buf) < frame_size:
            return None
        frame = self._buf[:frame_size]
        self._buf = self._buf[frame_size:]
        try:
            return WireMessage.decode(frame)
        except Exception as e:
            logger.warning(f"Wire decode error: {e}")
            return None

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass


class WireServer:
    """TCP/TLS server that accepts agent connections."""

    def __init__(self, host: str = "127.0.0.1", port: int = 7848, tls_config: TLSConfig | None = None):
        self.host = host
        self.port = port
        self.tls_config = tls_config
        self._sock: socket.socket | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._connections: dict[int, WireConnection] = {}
        self._on_message: list[Callable[[int, WireMessage], None]] = []

    def on_message(self, cb: Callable[[int, WireMessage], None]) -> None:
        self._on_message.append(cb)

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(32)
        self._sock.settimeout(1.0)
        if self.tls_config and self.tls_config.enabled:
            ctx = self.tls_config.create_server_context()
            self._sock = ctx.wrap_socket(self._sock, server_side=True)
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="wire-server")
        self._thread.start()
        logger.info(f"Wire server listening on {self.host}:{self.port}")

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        for conn in self._connections.values():
            conn.close()
        if self._sock:
            self._sock.close()
        logger.info("Wire server stopped")

    def _accept_loop(self) -> None:
        next_id = 1
        while self._running and self._sock:
            try:
                client, addr = self._sock.accept()
                cid = next_id
                next_id += 1
                conn = WireConnection(client)
                self._connections[cid] = conn
                t = threading.Thread(target=self._client_loop, args=(cid, conn), daemon=True)
                t.start()
                logger.info(f"Wire client {cid} connected from {addr}")
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.warning(f"Accept error: {e}")

    def _client_loop(self, cid: int, conn: WireConnection) -> None:
        while self._running:
            msg = conn.recv(timeout=1.0)
            if msg is None:
                break
            for cb in self._on_message:
                try:
                    cb(cid, msg)
                except Exception as e:
                    logger.warning(f"Wire message handler error: {e}")
        self._connections.pop(cid, None)
        conn.close()
        logger.info(f"Wire client {cid} disconnected")


def create_tls_config(cfg: dict | None) -> TLSConfig:
    if not cfg:
        return TLSConfig()
    return TLSConfig(
        cert_path=cfg.get("cert_path", cfg.get("cert", "")),
        key_path=cfg.get("key_path", cfg.get("key", "")),
        ca_cert_path=cfg.get("ca_cert_path", cfg.get("ca_cert", "")),
        verify_mode=cfg.get("verify_mode", "required"),
        min_version=cfg.get("min_version", "TLSv1.2"),
        ciphers=cfg.get("ciphers", ""),
    )
