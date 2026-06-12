"""PolyBridge - Redis-backed message hub with context isolation & heartbeat monitoring."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable

import redis  # type: ignore

from polylogue.models import (
    AgentCapability,
    AgentNode,
    AgentRole,
    AgentState,
    Heartbeat,
    MessageType,
    new_id,
    utcnow,
)

logger = logging.getLogger(__name__)


class Message:
    def __init__(
        self,
        msg_type: MessageType,
        sender: str,
        payload: Any,
        task_id: str | None = None,
        correlation_id: str | None = None,
        target: str | None = None,
        context_scope: str | None = None,
        ttl_seconds: float = 0.0,
    ):
        self.id = new_id()
        self.type = msg_type
        self.sender = sender
        self.payload = payload
        self.task_id = task_id or new_id()
        self.correlation_id = correlation_id or self.task_id
        self.target = target
        self.context_scope = context_scope
        self.ttl_seconds = ttl_seconds
        self.timestamp = utcnow().isoformat()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "sender": self.sender,
            "payload": self.payload,
            "task_id": self.task_id,
            "correlation_id": self.correlation_id,
            "target": self.target,
            "context_scope": self.context_scope,
            "ttl_seconds": self.ttl_seconds,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> Message:
        return cls(
            msg_type=MessageType(data["type"]),
            sender=data["sender"],
            payload=data["payload"],
            task_id=data.get("task_id"),
            correlation_id=data.get("correlation_id"),
            target=data.get("target"),
            context_scope=data.get("context_scope"),
            ttl_seconds=data.get("ttl_seconds", 0.0),
        )

    @classmethod
    def from_json(cls, data: str) -> Message:
        return cls.from_dict(json.loads(data))


class ContextIsolator:
    def __init__(self, redis_conn: redis.Redis, prefix: str = "polylogue"):
        self._redis: Any = redis_conn
        self._prefix = prefix

    def _ckey(self, agent_id: str, key: str) -> str:
        return f"{self._prefix}:ctx:{agent_id}:{key}"

    def _skey(self, shared_scope: str, key: str) -> str:
        return f"{self._prefix}:ctx:shared:{shared_scope}:{key}"

    def set(self, agent_id: str, key: str, value: Any, shared_scope: str | None = None) -> None:
        k = self._skey(shared_scope, key) if shared_scope else self._ckey(agent_id, key)
        self._redis.set(k, json.dumps(value))

    def get(self, agent_id: str, key: str, shared_scope: str | None = None) -> Any | None:
        k = self._skey(shared_scope, key) if shared_scope else self._ckey(agent_id, key)
        v = self._redis.get(k)
        return json.loads(v) if v else None

    def delete(self, agent_id: str, key: str, shared_scope: str | None = None) -> None:
        k = self._skey(shared_scope, key) if shared_scope else self._ckey(agent_id, key)
        self._redis.delete(k)

    def keys(self, agent_id: str, shared_scope: str | None = None) -> list[str]:
        pattern = f"{self._prefix}:ctx:shared:{shared_scope}:*" if shared_scope else f"{self._prefix}:ctx:{agent_id}:*"
        return list(self._redis.keys(pattern))

    def push_event(self, agent_id: str, event: dict, maxlen: int = 1000) -> None:
        key = f"{self._prefix}:journal:{agent_id}"
        self._redis.lpush(key, json.dumps(event))
        self._redis.ltrim(key, 0, maxlen - 1)

    def tail_events(self, agent_id: str, count: int = 50) -> list[dict]:
        key = f"{self._prefix}:journal:{agent_id}"
        items = self._redis.lrange(key, 0, count - 1)
        return [json.loads(i) for i in items]

    def publish_state(self, agent_id: str, state: dict) -> None:
        key = f"{self._prefix}:state:{agent_id}"
        self._redis.hset(key, mapping={k: json.dumps(v) for k, v in state.items()})
        self._redis.expire(key, 600)

    def get_state(self, agent_id: str) -> dict:
        key = f"{self._prefix}:state:{agent_id}"
        raw = self._redis.hgetall(key)
        return {k.decode() if isinstance(k, bytes) else k: json.loads(v) for k, v in raw.items()}

    def clear_agent(self, agent_id: str) -> None:
        for k in self.keys(agent_id):
            self._redis.delete(k)
        self._redis.delete(f"{self._prefix}:journal:{agent_id}")
        self._redis.delete(f"{self._prefix}:state:{agent_id}")


class AgentRegistry:
    def __init__(self, redis_conn: redis.Redis, prefix: str = "polylogue"):
        self._redis: Any = redis_conn
        self._prefix = prefix
        self._agents_key = f"{self._prefix}:agents"
        self._heartbeats_key = f"{self._prefix}:heartbeats"
        self._lock = threading.Lock()
        self._local_agents: dict[str, AgentNode] = {}

    def register(self, agent: AgentNode) -> None:
        key = f"{self._agents_key}:{agent.id}"
        self._redis.hset(key, mapping={
            "id": agent.id,
            "name": agent.name,
            "role": agent.role.value,
            "state": agent.state.value,
            "capabilities": json.dumps([c.value for c in agent.capabilities]),
            "priority": str(agent.priority),
            "slots": str(agent.slots),
            "slots_used": str(agent.slots_used),
            "pid": str(agent.pid or ""),
            "parent_id": agent.parent_id or "",
            "context_scope": agent.context_scope,
            "label": agent.label,
            "tags": json.dumps(agent.tags),
            "metadata": json.dumps(agent.metadata),
        })
        self._redis.expire(key, 120)
        with self._lock:
            self._local_agents[agent.id] = agent
        logger.info(f"Registered agent: {agent.name} ({agent.id[:8]}) as {agent.role.value}")

    def deregister(self, agent_id: str) -> None:
        self._redis.delete(f"{self._agents_key}:{agent_id}")
        self._redis.hdel(self._heartbeats_key, agent_id)
        with self._lock:
            self._local_agents.pop(agent_id, None)
        logger.info(f"Deregistered agent: {agent_id[:8]}")

    def heartbeat(self, hb: Heartbeat) -> None:
        self._redis.hset(self._heartbeats_key, hb.agent_id, hb.timestamp)
        state_key = f"{self._agents_key}:{hb.agent_id}"
        self._redis.hset(state_key, "state", hb.state.value)
        self._redis.hset(state_key, "slots_used", str(hb.slots_used))
        self._redis.expire(state_key, 120)

    def get_agent(self, agent_id: str) -> AgentNode | None:
        raw = self._redis.hgetall(f"{self._agents_key}:{agent_id}")
        if not raw:
            return None
        return self._dict_to_agent(raw)

    def list_agents(self) -> dict[str, AgentNode]:
        agents = {}
        pattern = f"{self._agents_key}:*"
        for key in self._redis.keys(pattern):
            agent_id = key.split(":")[-1]
            agent = self.get_agent(agent_id)
            if agent:
                agents[agent_id] = agent
        return agents

    def get_stale_agents(self, max_age: float = 15.0) -> list[str]:
        now = utcnow()
        stale = []
        raw = self._redis.hgetall(self._heartbeats_key)
        for agent_id, ts_str in raw.items():
            ts = datetime.fromisoformat(ts_str)
            if (now - ts).total_seconds() > max_age:
                stale.append(agent_id)
        return stale

    def find_by_capability(self, capability: str) -> list[AgentNode]:
        matches = []
        for agent in self.list_agents().values():
            caps = {c.value for c in agent.capabilities}
            if capability in caps and agent.available:
                matches.append(agent)
        return sorted(matches, key=lambda a: a.priority, reverse=True)

    def all_available(self, required_capabilities: list[str] | None = None) -> list[AgentNode]:
        agents = list(self.list_agents().values())
        if required_capabilities:
            required = set(required_capabilities)
            agents = [a for a in agents if required.issubset({c.value for c in a.capabilities})]
        return sorted([a for a in agents if a.available], key=lambda a: a.priority, reverse=True)

    @staticmethod
    def _dict_to_agent(raw: dict[str, str]) -> AgentNode:
        return AgentNode(
            id=raw.get("id", ""),
            name=raw.get("name", ""),
            role=AgentRole(raw.get("role", "slave")),
            state=AgentState(raw.get("state", "offline")),
            capabilities={AgentCapability(c) for c in json.loads(raw.get("capabilities", "[]"))},
            priority=int(raw.get("priority", 10)),
            slots=int(raw.get("slots", 1)),
            slots_used=int(raw.get("slots_used", 0)),
            pid=int(raw["pid"]) if raw.get("pid") else None,
            parent_id=raw.get("parent_id") or None,
            context_scope=raw.get("context_scope", "isolated"),
            label=raw.get("label", ""),
            tags=json.loads(raw.get("tags", "{}")),
            metadata=json.loads(raw.get("metadata", "{}")),
        )


class RedisHub:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        prefix: str = "polylogue",
        heartbeat_interval: float = 5.0,
        stale_timeout: float = 15.0,
    ):
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self.prefix = prefix
        self.heartbeat_interval = heartbeat_interval
        self.stale_timeout = stale_timeout

        self._redis: Any = None
        self._pubsub: Any = None
        self._listen_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._reaper_thread: threading.Thread | None = None
        self._running = False

        self._msg_handlers: dict[str, list[Callable[[Message], None]]] = defaultdict(list)
        self._type_handlers: dict[MessageType, list[Callable[[Message], None]]] = defaultdict(list)
        self._lock = threading.Lock()

        self.registry: AgentRegistry | None = None
        self.context: ContextIsolator = None  # type: ignore[assignment]

    def connect(self) -> RedisHub:
        self._redis = redis.Redis(
            host=self.host,
            port=self.port,
            db=self.db,
            password=self.password,
            decode_responses=True,
            socket_keepalive=True,
            socket_connect_timeout=5,
        )
        self._redis.ping()
        self.registry = AgentRegistry(self._redis, self.prefix)
        self.context = ContextIsolator(self._redis, self.prefix)
        logger.info("Bridge connected to Redis")
        return self

    def disconnect(self) -> None:
        self._running = False
        if self._pubsub:
            self._pubsub.close()
        for t in [self._listen_thread, self._heartbeat_thread, self._reaper_thread]:
            if t and t.is_alive():
                t.join(timeout=3)
        if self._redis:
            self._redis.close()
        logger.info("Bridge disconnected from Redis")

    def _ch(self, name: str) -> str:
        return f"{self.prefix}:{name}"

    def publish(self, channel: str, message: Message) -> int:
        if not self._redis:
            raise RuntimeError("Not connected")
        ch = self._ch(channel)
        count = self._redis.publish(ch, message.to_json())
        return count

    def publish_task(self, message: Message) -> int:
        return self.publish("tasks", message)

    def publish_result(self, message: Message) -> int:
        return self.publish("results", message)

    def publish_direct(self, target: str, message: Message) -> int:
        if not message.target:
            message.target = target
        return self.publish(f"direct:{target}", message)

    def publish_broadcast(self, message: Message) -> int:
        message.type = MessageType.BROADCAST
        return self.publish("broadcast", message)

    def subscribe(self, channel: str, handler: Callable[[Message], None]) -> None:
        with self._lock:
            self._msg_handlers[channel].append(handler)

    def subscribe_type(self, msg_type: MessageType, handler: Callable[[Message], None]) -> None:
        with self._lock:
            self._type_handlers[msg_type].append(handler)

    def subscribe_tasks(self, handler: Callable[[Message], None]) -> None:
        self.subscribe("tasks", handler)
        self.subscribe_type(MessageType.TASK, handler)

    def subscribe_results(self, handler: Callable[[Message], None]) -> None:
        self.subscribe("results", handler)
        self.subscribe_type(MessageType.RESULT, handler)

    def subscribe_direct(self, agent_id: str, handler: Callable[[Message], None]) -> None:
        self.subscribe(f"direct:{agent_id}", handler)

    def start_listening(self) -> None:
        if not self._redis:
            raise RuntimeError("Not connected")
        self._running = True
        self._pubsub = self._redis.pubsub()
        channels = ["tasks", "results", "broadcast", "control"]
        for ch in channels:
            self._pubsub.subscribe(self._ch(ch))
        self._pubsub.psubscribe(self._ch("direct:*"))
        self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True, name="hub-listen")
        self._listen_thread.start()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True, name="hub-heartbeat")
        self._heartbeat_thread.start()
        self._reaper_thread = threading.Thread(target=self._reaper_loop, daemon=True, name="hub-reaper")
        self._reaper_thread.start()
        logger.info("Hub listening on tasks, results, broadcast, control, direct:*")

    def _listen_loop(self) -> None:
        while self._running and self._pubsub:
            try:
                msg = self._pubsub.get_message(timeout=1.0)
                if msg is None or msg["type"] != "message":
                    continue
                channel = msg["channel"].decode() if isinstance(msg["channel"], bytes) else msg["channel"]
                raw_channel = channel
                if channel.startswith(self.prefix):
                    channel = channel[len(self.prefix) + 1:]
                message = Message.from_json(msg["data"])
                with self._lock:
                    handlers = list(self._msg_handlers.get(channel, []))
                    handlers.extend(self._msg_handlers.get(raw_channel, []))
                    handlers.extend(self._type_handlers.get(message.type, []))
                for handler in handlers:
                    try:
                        handler(message)
                    except Exception as e:
                        logger.error(f"Handler error on {channel}: {e}")
            except redis.ConnectionError:
                logger.warning("Redis connection lost, reconnecting...")
                time.sleep(2)
                try:
                    self._redis.ping()
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"Listen loop error: {e}")
                time.sleep(0.5)

    def _heartbeat_loop(self) -> None:
        while self._running:
            time.sleep(self.heartbeat_interval)
            try:
                if self._redis:
                    self._redis.ping()
            except Exception:
                pass

    def _reaper_loop(self) -> None:
        while self._running:
            time.sleep(self.stale_timeout)
            if not self.registry:
                continue
            try:
                stale = self.registry.get_stale_agents(self.stale_timeout)
                for agent_id in stale:
                    logger.warning(f"Stale agent detected: {agent_id[:8]} — marking offline")
                    alert = Message(
                        msg_type=MessageType.ALERT,
                        sender="hub",
                        payload={"event": "agent_stale", "agent_id": agent_id},
                    )
                    self.publish("control", alert)
            except Exception as e:
                logger.error(f"Reaper error: {e}")

    def enqueue_task(self, task: dict, priority: int = 5) -> str:
        if not self._redis:
            raise RuntimeError("Not connected")
        task_id = task.get("id", new_id())
        task["id"] = task_id
        queue_key = f"{self.prefix}:queue:tasks"
        self._redis.zadd(queue_key, {json.dumps(task): float(priority)})
        return task_id

    def dequeue_task(self, timeout: int = 5) -> dict | None:
        if not self._redis:
            raise RuntimeError("Not connected")
        queue_key = f"{self.prefix}:queue:tasks"
        result = self._redis.bzpopmin(queue_key, timeout=timeout)
        if result:
            return json.loads(result[1])
        return None

    def register_agent(self, agent: AgentNode) -> None:
        if self.registry:
            self.registry.register(agent)

    def deregister_agent(self, agent_id: str) -> None:
        if self.registry:
            self.registry.deregister(agent_id)

    def send_heartbeat(self, agent_id: str, role: AgentRole, state: AgentState, pid: int | None, slots_used: int, slots_total: int, load: float) -> None:
        if not self.registry:
            return
        hb = Heartbeat(
            agent_id=agent_id,
            role=role,
            state=state,
            pid=pid,
            slots_used=slots_used,
            slots_total=slots_total,
            load=load,
        )
        self.registry.heartbeat(hb)

    def get_agent(self, agent_id: str) -> AgentNode | None:
        return self.registry.get_agent(agent_id) if self.registry else None

    def list_agents(self) -> dict[str, AgentNode]:
        return self.registry.list_agents() if self.registry else {}

    def ack_task(self, task_id: str, ttl: int = 86400) -> None:
        if not self._redis:
            return
        key = f"{self.prefix}:queue:completed"
        self._redis.sadd(key, task_id)
        self._redis.expire(key, ttl)

    def is_completed(self, task_id: str) -> bool:
        if not self._redis:
            return False
        key = f"{self.prefix}:queue:completed"
        return bool(self._redis.sismember(key, task_id))

    def store_result(self, task_id: str, agent_id: str, output: Any, ttl: int = 86400) -> None:
        if not self._redis:
            return
        key = f"{self.prefix}:results:{task_id}"
        self._redis.hset(key, agent_id, json.dumps(output))
        self._redis.expire(key, ttl)

    def get_results(self, task_id: str) -> dict[str, Any]:
        if not self._redis:
            return {}
        key = f"{self.prefix}:results:{task_id}"
        raw = self._redis.hgetall(key)
        return {k: json.loads(v) for k, v in raw.items()}

    def durable_enqueue(self, queue_name: str, item: dict, ttl: int = 86400) -> None:
        if not self._redis:
            return
        key = f"{self.prefix}:durable:{queue_name}"
        self._redis.rpush(key, json.dumps(item))
        self._redis.expire(key, ttl)

    def durable_dequeue(self, queue_name: str, timeout: int = 5) -> dict | None:
        if not self._redis:
            return None
        key = f"{self.prefix}:durable:{queue_name}"
        result = self._redis.blpop(key, timeout=timeout)
        if result:
            return json.loads(result[1])
        return None

    def durable_queue_len(self, queue_name: str) -> int:
        if not self._redis:
            return 0
        key = f"{self.prefix}:durable:{queue_name}"
        return int(self._redis.llen(key))

    def list_durable_queues(self) -> list[str]:
        if not self._redis:
            return []
        pattern = f"{self.prefix}:durable:*"
        keys = self._redis.keys(pattern)
        return [k.split(":", 2)[2] for k in keys]

    def store_artifact(self, task_id: str, name: str, data: str, ttl: int = 86400) -> None:
        if not self._redis:
            return
        key = f"{self.prefix}:artifacts:{task_id}:{name}"
        self._redis.setex(key, ttl, data)

    def get_artifact(self, task_id: str, name: str) -> str | None:
        if not self._redis:
            return None
        key = f"{self.prefix}:artifacts:{task_id}:{name}"
        return self._redis.get(key)

    def list_artifacts(self, task_id: str) -> list[str]:
        if not self._redis:
            return []
        pattern = f"{self.prefix}:artifacts:{task_id}:*"
        keys = self._redis.keys(pattern)
        return [k.rsplit(":", 1)[1] for k in keys]


def create_hub(config: dict | None = None, **kwargs) -> RedisHub:
    cfg = config or {}
    return RedisHub(
        host=cfg.get("host", kwargs.get("host", "127.0.0.1")),
        port=cfg.get("port", kwargs.get("port", 6379)),
        db=cfg.get("db", kwargs.get("db", 0)),
        password=cfg.get("password", kwargs.get("password")),
        prefix=cfg.get("prefix", kwargs.get("prefix", "polylogue")),
        heartbeat_interval=cfg.get("heartbeat_interval", kwargs.get("heartbeat_interval", 5.0)),
        stale_timeout=cfg.get("stale_timeout", kwargs.get("stale_timeout", 15.0)),
    ).connect()
