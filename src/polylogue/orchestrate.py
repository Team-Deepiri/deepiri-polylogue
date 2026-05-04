"""Orchestration coordinator - coordinates tasks across multiple AI tools."""
import asyncio
import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from polylogue.hub import Message, MessageType, RedisHub
from polylogue.tools import ProcessAdapter, ToolManager, ToolState


logger = logging.getLogger(__name__)


class Strategy(Enum):
    PIPELINE = "pipeline"
    PARALLEL = "parallel"
    HYBRID = "hybrid"


class TaskState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    id: str
    description: str
    payload: dict
    assigned_to: str | None = None
    state: TaskState = TaskState.PENDING
    result: Any = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None


class TaskResult:
    def __init__(self, task_id: str, tool: str, output: Any, error: str | None = None):
        self.task_id = task_id
        self.tool = tool
        self.output = output
        self.error = error
        self.timestamp = datetime.now(timezone.utc)


class Orchestrator:
    def __init__(
        self,
        hub: RedisHub,
        tool_manager: ToolManager,
        strategy: str = "pipeline",
        max_parallel: int = 3,
        timeout: int = 300,
        aggregation: str = "first",
    ):
        self.hub = hub
        self.tool_manager = tool_manager
        self.strategy = Strategy(strategy)
        self.max_parallel = max_parallel
        self.timeout = timeout
        self.aggregation = aggregation

        self._tasks: dict[str, Task] = {}
        self._results: dict[str, list[TaskResult]] = {}
        self._task_queue: queue.Queue = queue.Queue()
        self._results_queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._running = False
        self._worker_threads: list[threading.Thread] = []

    def start(self) -> None:
        self._running = True
        self.hub.start_listening()
        self.hub.subscribe_tasks(self._handle_task)
        self.hub.subscribe_results(self._handle_result)

        for i in range(self.max_parallel):
            t = threading.Thread(target=self._worker_loop, daemon=True, name=f"worker-{i}")
            t.start()
            self._worker_threads.append(t)

        logger.info(f"Orchestrator started with {self.max_parallel} workers")

    def stop(self) -> None:
        self._running = False
        for t in self._worker_threads:
            if t.is_alive():
                t.join(timeout=5)
        self.tool_manager.stop_all()
        logger.info("Orchestrator stopped")

    def submit_task(self, description: str, payload: dict) -> str:
        task_id = str(uuid.uuid4())
        task = Task(id=task_id, description=description, payload=payload)

        with self._lock:
            self._tasks[task_id] = task
            self._results[task_id] = []

        msg = Message(
            msg_type=MessageType.TASK,
            sender="orchestrator",
            payload=payload,
            task_id=task_id,
        )
        self.hub.publish_task(msg)
        
        logger.info(f"Submitted task {task_id}: {description}")
        return task_id

    def get_task_status(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def get_result(self, task_id: str) -> Any:
        with self._lock:
            results = self._results.get(task_id, [])
        
        if not results:
            return None

        if self.aggregation == "first":
            for r in results:
                if r.error is None:
                    return r.output
            return results[0].error

        if self.aggregation == "all":
            return [r.output for r in results]

        return results[0].output if results else None

    def wait_for_result(self, task_id: str, timeout: int = 0) -> Any:
        start = time.time()
        while True:
            task = self.get_task_status(task_id)
            if task and task.state in (TaskState.COMPLETED, TaskState.FAILED):
                return self.get_result(task_id)
            if timeout > 0 and time.time() - start > timeout:
                return None
            time.sleep(0.1)

    def _handle_task(self, message: Message) -> None:
        task_id = message.task_id
        payload = message.payload

        with self._lock:
            if task_id in self._tasks:
                task = self._tasks[task_id]
            else:
                task = Task(
                    id=task_id,
                    description=payload.get("description", "unknown"),
                    payload=payload,
                )
                self._tasks[task_id] = task

        if self.strategy == Strategy.PIPELINE:
            self._assign_pipeline(task)
        elif self.strategy == Strategy.PARALLEL:
            self._assign_parallel(task)
        else:
            self._assign_hybrid(task)

    def _assign_pipeline(self, task: Task) -> None:
        tool = self._choose_tool(task.payload.get("capabilities", []))
        if tool:
            self._assign_task(task, tool)
        else:
            task.state = TaskState.FAILED
            task.error = "No available tool"

    def _assign_parallel(self, task: Task) -> None:
        tools = self.tool_manager.find_available(task.payload.get("capabilities", []))
        if not tools:
            task.state = TaskState.FAILED
            task.error = "No available tool"
            return

        for tool in tools[:self.max_parallel]:
            self._assign_task(task, tool)

    def _assign_hybrid(self, task: Task) -> None:
        tools = self.tool_manager.find_available(task.payload.get("capabilities", []))
        if tools:
            self._assign_task(task, tools[0])

    def _assign_task(self, task: Task, tool: ProcessAdapter) -> None:
        task.state = TaskState.RUNNING
        task.assigned_to = tool.config.name
        self.tool_manager.allocate(tool.config.name)

        msg = Message(
            msg_type=MessageType.TASK,
            sender="orchestrator",
            payload=task.payload,
            task_id=task.id,
            correlation_id=task.id,
        )
        
        tool.write_line(json.dumps(msg.to_dict()))
        logger.info(f"Assigned task {task.id} to {tool.config.name}")

    def _choose_tool(self, capabilities: list[str]) -> ProcessAdapter | None:
        tools = self.tool_manager.find_available(capabilities)
        return tools[0] if tools else None

    def _handle_result(self, message: Message) -> None:
        task_id = message.correlation_id
        payload = message.payload

        result = TaskResult(
            task_id=task_id,
            tool=message.sender,
            output=payload.get("output"),
            error=payload.get("error"),
        )

        with self._lock:
            if task_id not in self._tasks:
                return
            self._results.setdefault(task_id, []).append(result)
            
            task = self._tasks[task_id]

        if message.sender and task.assigned_to:
            self.tool_manager.release(task.assigned_to)

        all_results = self._results[task_id]
        if self.aggregation == "first" and result.output and not result.error:
            task.state = TaskState.COMPLETED
            task.result = result.output
            task.completed_at = datetime.now(timezone.utc)
        elif len(all_results) >= len(self.tool_manager.list_tools()):
            task.state = TaskState.COMPLETED
            task.result = self.get_result(task_id)
            task.completed_at = datetime.now(timezone.utc)

        logger.info(f"Received result from {message.sender} for task {task_id}")

    def _worker_loop(self) -> None:
        while self._running:
            try:
                task_msg = self._task_queue.get(timeout=1)
                self._execute_task(task_msg)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Worker error: {e}")

    def _execute_task(self, task_msg: dict) -> None:
        pass


def create_orchestrator(config: dict, hub: RedisHub, tool_manager: ToolManager) -> Orchestrator:
    return Orchestrator(
        hub=hub,
        tool_manager=tool_manager,
        strategy=config.get("strategy", "pipeline"),
        max_parallel=config.get("max_parallel_tasks", 3),
        timeout=config.get("task_timeout", 300),
        aggregation=config.get("result_aggregation", "first"),
    )