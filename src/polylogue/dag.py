"""Task DAG scheduler — executes tasks with dependency graphs."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from polylogue.models import Task, TaskState, utcnow

logger = logging.getLogger(__name__)


@dataclass
class DAGNode:
    task: Task
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    weight: float = 1.0
    retry_count: int = 0
    max_retries: int = 3

    @property
    def ready(self) -> bool:
        return self.task.state == TaskState.PENDING

    @property
    def blocked(self) -> bool:
        return self.task.state == TaskState.PENDING and not self.ready

    @property
    def done(self) -> bool:
        return self.task.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED)


@dataclass
class DAGGraph:
    nodes: dict[str, DAGNode] = field(default_factory=dict)

    def add_task(self, task: Task, depends_on: list[str] | None = None) -> str:
        node = DAGNode(task=task, dependencies=depends_on or [])
        self.nodes[task.id] = node
        for dep_id in node.dependencies:
            if dep_id in self.nodes:
                self.nodes[dep_id].dependents.append(task.id)
        return task.id

    def get_ready(self) -> list[DAGNode]:
        return [n for n in self.nodes.values() if n.ready and not n.blocked]

    def is_complete(self) -> bool:
        return all(n.done for n in self.nodes.values())

    def remaining(self) -> int:
        return sum(1 for n in self.nodes.values() if not n.done)

    def mark_done(self, task_id: str, state: TaskState) -> None:
        node = self.nodes.get(task_id)
        if node:
            node.task.state = state
            node.task.completed_at = utcnow().isoformat()

    def topological_sort(self) -> list[str]:
        visited = set()
        result = []
        def dfs(tid: str) -> None:
            if tid in visited:
                return
            visited.add(tid)
            node = self.nodes.get(tid)
            if node:
                for dep in node.dependencies:
                    dfs(dep)
                result.append(tid)
        for tid in list(self.nodes.keys()):
            dfs(tid)
        return result

    def critical_path(self) -> list[str]:
        sorted_tasks = self.topological_sort()
        dist: dict[str, float] = {}
        for tid in sorted_tasks:
            node = self.nodes.get(tid)
            if not node:
                continue
            dist[tid] = dist.get(tid, 0) + node.weight
            for dep_id in node.dependents:
                dist[dep_id] = max(dist.get(dep_id, 0), dist[tid])
        if not dist:
            return []
        max_dist = max(dist.values())
        critical = [tid for tid, d in dist.items() if d == max_dist]
        return critical


class DAGScheduler:
    """Executes tasks respecting dependency order via a DAG."""

    def __init__(self, executor: Any, max_parallel: int = 5):
        self.executor = executor
        self.max_parallel = max_parallel
        self._graph = DAGGraph()
        self._lock = threading.Lock()
        self._running = False
        self._scheduler_thread: threading.Thread | None = None
        self._completed: dict[str, TaskState] = {}

    def start(self) -> None:
        self._running = True
        self._scheduler_thread = threading.Thread(target=self._schedule_loop, daemon=True, name="dag-scheduler")
        self._scheduler_thread.start()
        logger.info("DAG scheduler started")

    def stop(self) -> None:
        self._running = False
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            self._scheduler_thread.join(timeout=5)
        logger.info("DAG scheduler stopped")

    def submit(self, task: Task, depends_on: list[str] | None = None) -> str:
        with self._lock:
            self._graph.add_task(task, depends_on)
        logger.info(f"DAG: submitted {task.id[:8]} (deps={depends_on})")
        return task.id

    def submit_graph(self, tasks: list[tuple[Task, list[str]]]) -> list[str]:
        ids = []
        with self._lock:
            for task, deps in tasks:
                self._graph.add_task(task, deps)
                ids.append(task.id)
        logger.info(f"DAG: submitted graph with {len(tasks)} tasks")
        return ids

    def wait_all(self, timeout: float = 300.0) -> dict[str, TaskState]:
        start = time.time()
        while time.time() - start < timeout:
            with self._lock:
                if self._graph.is_complete():
                    return {tid: n.task.state for tid, n in self._graph.nodes.items()}
            time.sleep(0.2)
        with self._lock:
            return {tid: n.task.state for tid, n in self._graph.nodes.items()}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total": len(self._graph.nodes),
                "completed": sum(1 for n in self._graph.nodes.values() if n.done),
                "pending": sum(1 for n in self._graph.nodes.values() if n.ready and not n.blocked),
                "blocked": sum(1 for n in self._graph.nodes.values() if n.blocked),
                "running": sum(1 for n in self._graph.nodes.values() if n.task.state == TaskState.RUNNING),
                "critical_path": self._graph.critical_path(),
            }

    def _schedule_loop(self) -> None:
        while self._running:
            ready = self._get_ready_tasks()
            dispatched = 0
            for node in ready:
                if dispatched >= self.max_parallel:
                    break
                node.task.state = TaskState.RUNNING
                node.task.started_at = utcnow().isoformat()
                t = threading.Thread(
                    target=self._execute_task,
                    args=(node,),
                    daemon=True,
                )
                t.start()
                dispatched += 1
            if dispatched == 0:
                time.sleep(0.5)

    def _get_ready_tasks(self) -> list[DAGNode]:
        with self._lock:
            ready = self._graph.get_ready()
            running = sum(1 for n in self._graph.nodes.values() if n.task.state == TaskState.RUNNING)
            available = self.max_parallel - running
            return ready[:available]

    def _execute_task(self, node: DAGNode) -> None:
        try:
            result = self.executor(node.task)
            state = TaskState.COMPLETED if result else TaskState.FAILED
            with self._lock:
                self._graph.mark_done(node.task.id, state)
            logger.info(f"DAG: task {node.task.id[:8]} -> {state.value}")
        except Exception as e:
            logger.error(f"DAG: task {node.task.id[:8]} error: {e}")
            with self._lock:
                if node.retry_count < node.max_retries:
                    node.retry_count += 1
                    node.task.state = TaskState.PENDING
                    logger.info(f"DAG: retrying {node.task.id[:8]} ({node.retry_count}/{node.max_retries})")
                else:
                    self._graph.mark_done(node.task.id, TaskState.FAILED)
