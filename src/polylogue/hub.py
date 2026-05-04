"""Redis message hub for inter-agent communication."""
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

import redis


logger = logging.getLogger(__name__)


class MessageType(Enum):
    TASK = "task"
    RESULT = "result"
    HEARTBEAT = "heartbeat"
    REGISTER = "register"
    DEREGISTER = "deregister"
    STATUS = "status"


class Message:
    def __init__(
        self,
        msg_type: MessageType,
        sender: str,
        payload: Any,
        task_id: str | None = None,
        correlation_id: str | None = None,
    ):
        self.id = str(uuid.uuid4())
        self.type = msg_type
        self.sender = sender
        self.payload = payload
        self.task_id = task_id or str(uuid.uuid4())
        self.correlation_id = correlation_id or self.task_id
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "sender": self.sender,
            "payload": self.payload,
            "task_id": self.task_id,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        return cls(
            msg_type=MessageType(data["type"]),
            sender=data["sender"],
            payload=data["payload"],
            task_id=data.get("task_id"),
            correlation_id=data.get("correlation_id"),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, data: str) -> "Message":
        return cls.from_dict(json.loads(data))


class RedisHub:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        prefix: str = "polylogue",
    ):
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self.prefix = prefix

        self._redis: redis.Redis | None = None
        self._pubsub: redis.client.PubSub | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._handlers: dict[MessageType, list[Callable[[Message], None]]] = {}
        self._local_handlers: dict[str, list[Callable[[Message], None]]] = {}

    def connect(self) -> "RedisHub":
        self._redis = redis.Redis(
            host=self.host,
            port=self.port,
            db=self.db,
            password=self.password,
            decode_responses=True,
        )
        self._redis.ping()
        logger.info(f"Connected to Redis at {self.host}:{self.port}")
        return self

    def disconnect(self) -> None:
        self._running = False
        if self._pubsub:
            self._pubsub.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        if self._redis:
            self._redis.close()
        logger.info("Disconnected from Redis")

    def _get_channel(self, name: str) -> str:
        return f"{self.prefix}:{name}"

    def publish(self, channel: str, message: Message) -> int:
        if not self._redis:
            raise RuntimeError("Not connected to Redis")
        ch = self._get_channel(channel)
        count = self._redis.publish(ch, message.to_json())
        logger.debug(f"Published to {ch}: {message.type.value} from {message.sender}")
        return count

    def publish_task(self, message: Message) -> int:
        return self.publish("tasks", message)

    def publish_result(self, message: Message) -> int:
        return self.publish("results", message)

    def subscribe(
        self,
        channel: str,
        handler: Callable[[Message], None],
        local_only: bool = False,
    ) -> None:
        if channel == "tasks":
            self._handlers.setdefault(MessageType.TASK, []).append(handler)
        elif channel == "results":
            self._handlers.setdefault(MessageType.RESULT, []).append(handler)
        else:
            self._local_handlers.setdefault(channel, []).append(handler)
        
        if not local_only and self._pubsub:
            self._pubsub.subscribe(self._get_channel(channel))

    def subscribe_tasks(self, handler: Callable[[Message], None]) -> None:
        self.subscribe("tasks", handler)

    def subscribe_results(self, handler: Callable[[Message], None]) -> None:
        self.subscribe("results", handler)

    def start_listening(self) -> None:
        if not self._redis:
            raise RuntimeError("Not connected to Redis")
        
        self._running = True
        self._pubsub = self._redis.pubsub()
        
        for ch in ["tasks", "results"]:
            self._pubsub.subscribe(self._get_channel(ch))
        
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info("Started listening for messages")

    def _listen_loop(self) -> None:
        if not self._pubsub:
            return
        
        for msg in self._pubsub.listen():
            if not self._running:
                break
            if msg["type"] != "message":
                continue
            
            try:
                message = Message.from_json(msg["data"])
            except Exception as e:
                logger.warning(f"Failed to parse message: {e}")
                continue
            
            msg_type = message.type
            handlers = self._handlers.get(msg_type, [])
            
            for handler in handlers:
                try:
                    handler(message)
                except Exception as e:
                    logger.error(f"Handler error for {msg_type.value}: {e}")

    def register_agent(self, agent_id: str, metadata: dict) -> None:
        if not self._redis:
            raise RuntimeError("Not connected to Redis")
        
        key = f"{self.prefix}:agents"
        self._redis.hset(key, agent_id, json.dumps(metadata))
        logger.info(f"Registered agent: {agent_id}")

    def deregister_agent(self, agent_id: str) -> None:
        if not self._redis:
            raise RuntimeError("Not connected to Redis")
        
        key = f"{self.prefix}:agents"
        self._redis.hdel(key, agent_id)
        logger.info(f"Deregistered agent: {agent_id}")

    def list_agents(self) -> dict:
        if not self._redis:
            raise RuntimeError("Not connected to Redis")
        
        key = f"{self.prefix}:agents"
        data = self._redis.hgetall(key)
        return {k: json.loads(v) for k, v in data.items()}

    def enqueue_task(self, task: dict, priority: int = 0) -> str:
        if not self._redis:
            raise RuntimeError("Not connected to Redis")
        
        task_id = task.get("id", str(uuid.uuid4()))
        task["id"] = task_id
        queue_key = f"{self.prefix}:queue:tasks"
        
        if priority > 0:
            self._redis.zadd(queue_key, {json.dumps(task): priority})
        else:
            self._redis.lpush(queue_key, json.dumps(task))
        
        return task_id

    def dequeue_task(self, block: bool = True, timeout: int = 0) -> dict | None:
        if not self._redis:
            raise RuntimeError("Not connected to Redis")
        
        queue_key = f"{self.prefix}:queue:tasks"
        
        if block:
            result = self._redis.brpop(queue_key, timeout=timeout)
            if result:
                return json.loads(result[1])
            return None
        else:
            result = self._redis.rpop(queue_key)
            if result:
                return json.loads(result)
            return None

    def ack_task(self, task_id: str) -> None:
        if not self._redis:
            raise RuntimeError("Not connected to Redis")
        
        key = f"{self.prefix}:queue:completed"
        self._redis.sadd(key, task_id)
        self._redis.expire(key, 3600)

    def is_completed(self, task_id: str) -> bool:
        if not self._redis:
            raise RuntimeError("Not connected to Redis")
        
        key = f"{self.prefix}:queue:completed"
        return bool(self._redis.sismember(key, task_id))


def create_hub(config: dict | None = None, **kwargs) -> RedisHub:
    cfg = config or {}
    return RedisHub(
        host=cfg.get("host", kwargs.get("host", "127.0.0.1")),
        port=cfg.get("port", kwargs.get("port", 6379)),
        db=cfg.get("db", kwargs.get("db", 0)),
        password=cfg.get("password", kwargs.get("password")),
        prefix=cfg.get("prefix", kwargs.get("prefix", "polylogue")),
    ).connect()