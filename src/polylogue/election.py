"""Leader election — high-availability master failover via Redis."""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class LeaderState:
    leader_id: str = ""
    hostname: str = ""
    pid: int = 0
    elected_at: str = ""
    term: int = 0
    heartbeat_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "leader_id": self.leader_id,
            "hostname": self.hostname,
            "pid": self.pid,
            "elected_at": self.elected_at,
            "term": self.term,
            "heartbeat_count": self.heartbeat_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> LeaderState:
        return cls(
            leader_id=d.get("leader_id", ""),
            hostname=d.get("hostname", ""),
            pid=d.get("pid", 0),
            elected_at=d.get("elected_at", ""),
            term=d.get("term", 0),
            heartbeat_count=d.get("heartbeat_count", 0),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LeaderElector:
    """Redis-based leader election with heartbeat renewal and automatic failover.

    Uses Redis SET NX (atomic create-if-not-exists) for the election and
    periodic heartbeats to maintain leadership. If the leader stops heartbeating,
    another instance can claim leadership.
    """

    def __init__(
        self,
        redis_conn: Any,
        node_id: str,
        prefix: str = "polylogue",
        lease_seconds: float = 15.0,
        heartbeat_interval: float = 5.0,
        hostname: str | None = None,
    ):
        self._redis = redis_conn
        self.node_id = node_id
        self.prefix = prefix
        self.lease_seconds = lease_seconds
        self.heartbeat_interval = heartbeat_interval
        self._hostname = hostname or socket.gethostname()
        self._pid = os.getpid()
        self._leader_key = f"{self.prefix}:leader"
        self._heartbeat_key = f"{self.prefix}:leader:heartbeat"
        self._term_key = f"{self.prefix}:leader:term"
        self._running = False
        self._is_leader = False
        self._term = 0
        self._heartbeat_count = 0
        self._election_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._on_elected: list[Callable[[], None]] = []
        self._on_deposed: list[Callable[[], None]] = []

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    def on_elected(self, cb: Callable[[], None]) -> None:
        self._on_elected.append(cb)

    def on_deposed(self, cb: Callable[[], None]) -> None:
        self._on_deposed.append(cb)

    def start(self) -> bool:
        self._running = True
        self._term = self._get_term()
        self._election_thread = threading.Thread(target=self._election_loop, daemon=True, name="leader-election")
        self._election_thread.start()
        logger.info(f"Leader elector started (node={self.node_id[:12]})")
        return self._try_claim()

    def stop(self) -> None:
        self._running = False
        if self._is_leader:
            self._resign()
        if self._election_thread and self._election_thread.is_alive():
            self._election_thread.join(timeout=5)
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=3)
        logger.info("Leader elector stopped")

    def _get_term(self) -> int:
        try:
            val = self._redis.get(self._term_key)
            return int(val) if val else 0
        except Exception:
            return 0

    def _increment_term(self) -> int:
        try:
            return int(self._redis.incr(self._term_key))
        except Exception:
            return self._term + 1

    def _try_claim(self) -> bool:
        term = self._increment_term()
        candidate = self._make_leader_state(term)
        try:
            claimed = self._redis.set(
                self._leader_key,
                candidate,
                nx=True,
            )
            if claimed:
                with self._lock:
                    self._is_leader = True
                    self._term = term
                    self._heartbeat_count = 0
                self._start_heartbeats()
                logger.info(f"ELECTED as leader (term={term}, node={self.node_id[:12]})")
                for cb in self._on_elected:
                    try:
                        cb()
                    except Exception as e:
                        logger.error(f"on_elected callback error: {e}")
                return True
            return False
        except Exception as e:
            logger.warning(f"Election attempt failed: {e}")
            return False

    def _make_leader_state(self, term: int) -> dict:
        return {
            "leader_id": self.node_id,
            "hostname": self._hostname,
            "pid": self._pid,
            "elected_at": _now_iso(),
            "term": term,
            "heartbeat_count": 0,
        }

    def _start_heartbeats(self) -> None:
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="leader-heartbeat"
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        while self._running and self._is_leader:
            try:
                state = self._make_leader_state(self._term)
                state["heartbeat_count"] = self._heartbeat_count
                self._redis.setex(self._leader_key, int(self.lease_seconds), state)
                self._heartbeat_count += 1
            except Exception as e:
                logger.warning(f"Leader heartbeat failed: {e}")
            time.sleep(self.heartbeat_interval)

    def _resign(self) -> None:
        try:
            current = self._redis.get(self._leader_key)
            if current and current.get("leader_id") == self.node_id:
                self._redis.delete(self._leader_key)
            with self._lock:
                self._is_leader = False
            for cb in self._on_deposed:
                try:
                    cb()
                except Exception as e:
                    logger.error(f"on_deposed callback error: {e}")
            logger.info(f"Resigned leadership (node={self.node_id[:12]})")
        except Exception as e:
            logger.warning(f"Resignation failed: {e}")

    def _election_loop(self) -> None:
        while self._running:
            if not self._is_leader:
                try:
                    current = self._redis.get(self._leader_key)
                    if not current:
                        logger.info("No leader detected, claiming...")
                        self._try_claim()
                    else:
                        leader = LeaderState.from_dict(current)
                        if leader.leader_id == self.node_id:
                            with self._lock:
                                self._is_leader = True
                                self._term = leader.term
                                self._heartbeat_count = leader.heartbeat_count
                            self._start_heartbeats()
                except Exception as e:
                    logger.warning(f"Election check failed: {e}")
                time.sleep(self.lease_seconds / 3)
            else:
                time.sleep(self.heartbeat_interval)

    def get_leader(self) -> LeaderState | None:
        try:
            data = self._redis.get(self._leader_key)
            if data:
                return LeaderState.from_dict(data)
        except Exception:
            pass
        return None

    def get_status(self) -> dict[str, Any]:
        leader = self.get_leader()
        return {
            "node_id": self.node_id,
            "is_leader": self._is_leader,
            "term": self._term,
            "heartbeat_count": self._heartbeat_count,
            "current_leader": leader.to_dict() if leader else None,
        }
