"""Data models for the PolyBridge master/slave agent orchestration system."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class AgentRole(Enum):
    MASTER = "master"
    SLAVE = "slave"
    DIRECTOR = "director"
    WORKER = "worker"
    BRIDGE = "bridge"


class AgentState(Enum):
    OFFLINE = "offline"
    STARTING = "starting"
    ONLINE = "online"
    BUSY = "busy"
    DEGRADED = "degraded"
    ERROR = "error"
    TERMINATED = "terminated"


class AgentCapability(Enum):
    CODE_EDIT = "code_edit"
    FILE_OPS = "file_operations"
    TERMINAL = "terminal"
    CODE_COMPLETION = "code_completion"
    CODE_SUGGEST = "code_suggestions"
    REVIEW = "review"
    DEBUG = "debug"
    TEST = "test"
    DOCS = "documentation"
    REASONING = "reasoning"
    SEARCH = "search"
    COORDINATION = "coordination"


class TaskState(Enum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class Strategy(Enum):
    PIPELINE = "pipeline"
    PARALLEL = "parallel"
    HYBRID = "hybrid"
    LEADER_ELECT = "leader_elect"
    CONSENSUS = "consensus"
    ALL = "all"


class MessageType(Enum):
    TASK = "task"
    RESULT = "result"
    HEARTBEAT = "heartbeat"
    REGISTER = "register"
    DEREGISTER = "deregister"
    STATUS = "status"
    DIRECT = "direct"
    BROADCAST = "broadcast"
    SYNC = "sync"
    ELECT = "elect"
    ALERT = "alert"
    CONTEXT_SYNC = "context_sync"


@dataclass
class Heartbeat:
    agent_id: str
    role: AgentRole
    state: AgentState
    pid: int | None
    slots_used: int
    slots_total: int
    load: float
    timestamp: str = field(default_factory=lambda: utcnow().isoformat())

    def is_stale(self, max_age_seconds: float = 15.0) -> bool:
        t = datetime.fromisoformat(self.timestamp)
        return (utcnow() - t).total_seconds() > max_age_seconds


@dataclass
class AgentNode:
    id: str
    name: str
    role: AgentRole
    state: AgentState = AgentState.OFFLINE
    capabilities: set[AgentCapability] = field(default_factory=set)
    priority: int = 10
    slots: int = 1
    slots_used: int = 0
    pid: int | None = None
    label: str = ""
    tags: dict[str, str] = field(default_factory=dict)
    last_heartbeat: Heartbeat | None = None
    parent_id: str | None = None
    children: list[str] = field(default_factory=list)
    context_scope: str = "isolated"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.state in (AgentState.ONLINE, AgentState.DEGRADED) and self.slots_used < self.slots

    @property
    def load_pct(self) -> float:
        return (self.slots_used / self.slots * 100) if self.slots > 0 else 0.0


@dataclass
class Task:
    id: str = field(default_factory=new_id)
    description: str = ""
    payload: dict = field(default_factory=dict)
    state: TaskState = TaskState.PENDING
    required_capabilities: list[AgentCapability] = field(default_factory=list)
    assigned_to: list[str] = field(default_factory=list)
    results: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    context_snapshot: dict[str, Any] = field(default_factory=dict)
    strategy: Strategy = Strategy.PARALLEL
    timeout_seconds: float = 300.0
    created_at: str = field(default_factory=lambda: utcnow().isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    priority: int = 5
    agent_filter: list[str] | None = None

    @property
    def is_done(self) -> bool:
        return self.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED, TaskState.TIMEOUT)

    @property
    def elapsed(self) -> float:
        start = self.started_at or self.created_at
        t = datetime.fromisoformat(self.completed_at or utcnow().isoformat())
        return (t - datetime.fromisoformat(start)).total_seconds()


@dataclass
class Topology:
    master: AgentNode
    slaves: dict[str, AgentNode] = field(default_factory=dict)
    directors: dict[str, AgentNode] = field(default_factory=dict)
    bridges: dict[str, AgentNode] = field(default_factory=dict)

    def all_agents(self) -> dict[str, AgentNode]:
        agents = {}
        for a in [self.master] + list(self.slaves.values()) + list(self.directors.values()) + list(self.bridges.values()):
            agents[a.id] = a
        return agents

    def available_agents(self, capability: AgentCapability | None = None) -> list[AgentNode]:
        candidates = []
        for agent in self.all_agents().values():
            if not agent.available:
                continue
            if capability and capability not in agent.capabilities:
                continue
            candidates.append(agent)
        return sorted(candidates, key=lambda a: a.priority, reverse=True)


@dataclass
class ContextScope:
    agent_id: str
    shared_id: str | None = None
    keys: dict[str, str] = field(default_factory=dict)

    @property
    def namespace(self) -> str:
        if self.shared_id:
            return f"shared:{self.shared_id}"
        return f"agent:{self.agent_id}"
