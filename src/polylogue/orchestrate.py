"""PolyBridge orchestration engine - master/slave parallel execution with context isolation."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from collections import defaultdict
from typing import Any, Callable

from polylogue.dag import DAGScheduler
from polylogue.hub import Message, MessageType, RedisHub, ContextIsolator
from polylogue.journal_bridge import JournalBridge
from polylogue.models import (
    AgentCapability,
    AgentNode,
    AgentRole,
    AgentState,
    MessageType as MT,
    Strategy,
    Task,
    TaskState,
    Topology,
    utcnow,
)
from polylogue.retry import RetryExecutor
from polylogue.tools import ProcessAdapter, ToolManager, ToolRecord

logger = logging.getLogger(__name__)


class TaskResult:
    def __init__(self, task_id: str, agent: str, output: Any, error: str | None = None):
        self.task_id = task_id
        self.agent = agent
        self.output = output
        self.error = error
        self.timestamp = utcnow().isoformat()


class ContextManager:
    """Manages context isolation between agents in the bridge."""

    def __init__(self, isolator: ContextIsolator):
        self._iso = isolator

    def set_agent_context(self, agent_id: str, key: str, value: Any, shared: bool = False) -> None:
        scope = "global" if shared else None
        self._iso.set(agent_id, key, value, shared_scope=scope)

    def get_agent_context(self, agent_id: str, key: str, shared: bool = False) -> Any | None:
        scope = "global" if shared else None
        return self._iso.get(agent_id, key, shared_scope=scope)

    def push_event(self, agent_id: str, event: dict) -> None:
        self._iso.push_event(agent_id, event)

    def tail_events(self, agent_id: str, count: int = 50) -> list[dict]:
        return self._iso.tail_events(agent_id, count)

    def sync_to_agent(self, source: str, target: str, keys: list[str]) -> None:
        for key in keys:
            val = self._iso.get(source, key)
            if val is not None:
                self._iso.set(target, key, val)

    def isolate_task(self, task: Task, agents: list[str]) -> dict[str, dict]:
        snapshots = {}
        for agent_id in agents:
            snapshots[agent_id] = {
                k: self._iso.get(agent_id, k) for k in self._iso.keys(agent_id)
            }
        task.context_snapshot = snapshots
        return snapshots

    def restore_context(self, task: Task, agent_id: str) -> None:
        snapshot = task.context_snapshot
        if not snapshot:
            return
        for key, val in snapshot.items():
            if val is not None:
                self._iso.set(agent_id, key, val)


class PipelineExecutor:
    """Sequential task execution - one agent at a time."""

    def __init__(self, hub: RedisHub, tool_manager: ToolManager, ctx: ContextManager, timeout: float = 300.0):
        self.hub = hub
        self.tm = tool_manager
        self.ctx = ctx
        self.timeout = timeout

    def execute(self, task: Task, agents: list[tuple[str, ProcessAdapter, ToolRecord]]) -> Task:
        if not agents:
            task.state = TaskState.FAILED
            task.errors["_system"] = "No available agents"
            return task
        task.state = TaskState.DISPATCHED
        task.started_at = utcnow().isoformat()
        name, adapter, rec = agents[0]
        task.assigned_to = [name]
        self.tm.allocate(name)
        try:
            result = self._run_on_agent(task, name, adapter)
            if result.error:
                task.state = TaskState.FAILED
                task.errors[name] = result.error
            else:
                task.state = TaskState.COMPLETED
                task.results[name] = result.output
        finally:
            self.tm.release(name)
        task.completed_at = utcnow().isoformat()
        return task

    def _run_on_agent(self, task: Task, name: str, adapter: ProcessAdapter) -> TaskResult:
        msg = Message(
            msg_type=MT.TASK,
            sender="master",
            payload={"task_id": task.id, "description": task.description, "payload": task.payload},
            task_id=task.id,
            target=name,
        )
        adapter.write_line(msg.to_json())
        deadline = time.time() + self.timeout
        collected: list[str] = []
        def capture(line: str) -> None:
            if task.id in line:
                collected.append(line)
        old_handler = adapter._output_handler
        adapter.set_output_handler(capture)
        try:
            while time.time() < deadline:
                if adapter.poll():
                    break
                if self.hub.is_completed(task.id):
                    break
                if task.state in (TaskState.CANCELLED, TaskState.FAILED):
                    break
                time.sleep(0.1)
            for line in collected:
                try:
                    data = json.loads(line)
                    if data.get("task_id") == task.id and data.get("type") == "result":
                        return TaskResult(task.id, name, data.get("payload", {}).get("output"), data.get("payload", {}).get("error"))
                except (json.JSONDecodeError, KeyError):
                    pass
            raw = self.hub.get_results(task.id)
            if name in raw:
                return TaskResult(task.id, name, raw[name], None)
        finally:
            adapter.set_output_handler(old_handler)
        return TaskResult(task.id, name, None, "Timeout or no result")


class ParallelExecutor:
    """True fan-out parallel execution - dispatch to multiple agents simultaneously."""

    def __init__(self, hub: RedisHub, tool_manager: ToolManager, ctx: ContextManager, max_parallel: int = 5, timeout: float = 300.0):
        self.hub = hub
        self.tm = tool_manager
        self.ctx = ctx
        self.max_parallel = max_parallel
        self.timeout = timeout
        self._lock = threading.Lock()

    def execute(self, task: Task, agents: list[tuple[str, ProcessAdapter, ToolRecord]]) -> Task:
        if not agents:
            task.state = TaskState.FAILED
            task.errors["_system"] = "No available agents"
            return task
        task.state = TaskState.DISPATCHED
        task.started_at = utcnow().isoformat()
        selected = agents[:self.max_parallel]
        barrier = threading.Barrier(len(selected) + 1, timeout=self.timeout)
        results: dict[str, TaskResult] = {}
        threads: list[threading.Thread] = []
        def run(name: str, adapter: ProcessAdapter) -> None:
            try:
                self.tm.allocate(name)
                result = self._exec_single(task, name, adapter)
                with self._lock:
                    results[name] = result
                    task.assigned_to.append(name)
                    if result.error:
                        task.errors[name] = result.error
                    else:
                        task.results[name] = result.output
            except Exception as e:
                with self._lock:
                    results[name] = TaskResult(task.id, name, None, str(e))
            finally:
                self.tm.release(name)
                try:
                    barrier.wait()
                except Exception:
                    pass
        for name, adapter, _ in selected:
            t = threading.Thread(target=run, args=(name, adapter), daemon=True)
            t.start()
            threads.append(t)
        try:
            barrier.wait()
        except Exception:
            pass
        for t in threads:
            t.join(timeout=5)
        failures = sum(1 for r in results.values() if r.error)
        successes = sum(1 for r in results.values() if r.output is not None and not r.error)
        if successes > 0:
            task.state = TaskState.COMPLETED
        elif failures == len(selected):
            task.state = TaskState.FAILED
        else:
            task.state = TaskState.COMPLETED
        task.completed_at = utcnow().isoformat()
        logger.info(f"Task {task.id[:8]}: {successes} success, {failures} fail across {len(selected)} agents")
        return task

    def _exec_single(self, task: Task, name: str, adapter: ProcessAdapter) -> TaskResult:
        msg = Message(
            msg_type=MT.TASK,
            sender="master",
            payload={"task_id": task.id, "description": task.description, "payload": task.payload},
            task_id=task.id,
            target=name,
        )
        adapter.write_line(msg.to_json())
        deadline = time.time() + self.timeout
        collected: list[str] = []
        def capture(line: str) -> None:
            if task.id in line:
                collected.append(line)
        old = adapter._output_handler
        adapter.set_output_handler(capture)
        try:
            while time.time() < deadline:
                if adapter.poll():
                    break
                if self.hub.is_completed(task.id):
                    break
                if task.state in (TaskState.CANCELLED, TaskState.FAILED):
                    break
                time.sleep(0.1)
            for line in collected:
                try:
                    data = json.loads(line)
                    if data.get("task_id") == task.id and data.get("type") == "result":
                        return TaskResult(task.id, name, data.get("payload", {}).get("output"), data.get("payload", {}).get("error"))
                except (json.JSONDecodeError, KeyError):
                    pass
            raw = self.hub.get_results(task.id)
            if name in raw:
                return TaskResult(task.id, name, raw[name], None)
        finally:
            adapter.set_output_handler(old)
        return TaskResult(task.id, name, None, "Timeout")


class MasterOrchestrator:
    """The master node - coordinates all agents, dispatches tasks, monitors health, manages context."""

    def __init__(
        self,
        hub: RedisHub,
        tool_manager: ToolManager,
        node_id: str,
        max_parallel: int = 5,
        task_timeout: float = 300.0,
        result_aggregation: str = "first",
    ):
        self.hub = hub
        self.tm = tool_manager
        self.node_id = node_id
        self.max_parallel = max_parallel
        self.task_timeout = task_timeout
        self.aggregation = result_aggregation

        self.ctx = ContextManager(hub.context)
        self.topology = Topology(
            master=AgentNode(
                id=node_id,
                name="polylogue-master",
                role=AgentRole.MASTER,
                state=AgentState.ONLINE,
                capabilities={c for c in AgentCapability},
                priority=100,
                slots=max_parallel * 2,
                label="PolyBridge Master",
            )
        )

        self._tasks: dict[str, Task] = {}
        self._results: dict[str, list[TaskResult]] = defaultdict(list)
        self._pending: queue.Queue[Task] = queue.Queue()
        self._lock = threading.Lock()
        self._running = False
        self._workers: list[threading.Thread] = []
        self._heartbeat_timer: threading.Thread | None = None
        self._agent_states: dict[str, AgentNode] = {}
        self._state_listeners: list[Callable[[str, str], None]] = []

        self._pipeline_exec = PipelineExecutor(hub, tool_manager, self.ctx, task_timeout)
        self._parallel_exec = ParallelExecutor(hub, tool_manager, self.ctx, max_parallel, task_timeout)

        self.dag_scheduler = DAGScheduler(executor=self._run_task, max_parallel=max_parallel)
        self.retry_executor = RetryExecutor()
        self.journal: JournalBridge | None = None

    def set_journal_bridge(self, jb: JournalBridge) -> None:
        self.journal = jb

    def on_state_change(self, callback: Callable[[str, str], None]) -> None:
        self._state_listeners.append(callback)

    def start(self) -> None:
        self._running = True
        self.hub.start_listening()
        self.hub.subscribe_tasks(self._handle_incoming_task)
        self.hub.subscribe_results(self._handle_incoming_result)
        self.hub.subscribe_type(MessageType.HEARTBEAT, self._handle_heartbeat)
        self.hub.subscribe_type(MessageType.REGISTER, self._handle_register)
        self.hub.subscribe_type(MessageType.STATUS, self._handle_status)
        self.hub.subscribe_type(MessageType.ALERT, self._handle_alert)

        self.hub.register_agent(self.topology.master)
        self.tm.on_state_change(self._tool_state_changed)

        for i in range(self.max_parallel):
            t = threading.Thread(target=self._worker_loop, daemon=True, name=f"orchestrator-worker-{i}")
            t.start()
            self._workers.append(t)

        self._heartbeat_timer = threading.Thread(target=self._heartbeat_sender, daemon=True, name="orchestrator-hb")
        self._heartbeat_timer.start()

        self._sync_agents_to_topology()
        logger.info(f"Master orchestrator {self.node_id[:8]} started with {self.max_parallel} workers")

    def stop(self) -> None:
        self._running = False
        for w in self._workers:
            if w.is_alive():
                w.join(timeout=3)
        if self._heartbeat_timer and self._heartbeat_timer.is_alive():
            self._heartbeat_timer.join(timeout=3)
        self.hub.deregister_agent(self.node_id)
        logger.info("Master orchestrator stopped")

    def submit_task(self, description: str, payload: dict, strategy: Strategy = Strategy.PARALLEL, capabilities: list[str] | None = None, priority: int = 5, timeout: float | None = None) -> str:
        task = Task(
            description=description,
            payload=payload,
            strategy=strategy,
            required_capabilities=[AgentCapability(c) for c in (capabilities or [])],
            priority=priority,
            timeout_seconds=timeout or self.task_timeout,
        )
        with self._lock:
            self._tasks[task.id] = task
        self._pending.put(task)
        msg = Message(
            msg_type=MT.TASK,
            sender=self.node_id,
            payload={"task_id": task.id, "description": description, "strategy": strategy.value},
            task_id=task.id,
        )
        self.hub.publish_task(msg)
        logger.info(f"Task {task.id[:8]} submitted: {description[:60]} (strategy={strategy.value})")
        return task.id

    def get_task(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def wait_for_task(self, task_id: str, timeout: float = 60.0) -> Task | None:
        start = time.time()
        while time.time() - start < timeout:
            task = self.get_task(task_id)
            if task and task.is_done:
                return task
            time.sleep(0.1)
        return self.get_task(task_id)

    def get_task_result(self, task_id: str) -> Any:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            results = task.results
            errors = task.errors
        if not results:
            return None if not errors else {"error": errors}
        if self.aggregation == "first":
            for agent, out in results.items():
                if out is not None:
                    return out
            return {"error": errors}
        if self.aggregation == "all":
            return {"results": results, "errors": errors}
        return results

    def _handle_incoming_task(self, msg: Message) -> None:
        task_id = msg.task_id
        with self._lock:
            if task_id not in self._tasks:
                self._tasks[task_id] = Task(
                    id=task_id,
                    description=msg.payload.get("description", "remote task"),
                    payload=msg.payload.get("payload", msg.payload),
                )
                self._pending.put(self._tasks[task_id])

    def _handle_incoming_result(self, msg: Message) -> None:
        task_id = msg.correlation_id
        payload = msg.payload or {}
        result = TaskResult(task_id, msg.sender, payload.get("output"), payload.get("error"))
        with self._lock:
            self._results[task_id].append(result)
            task = self._tasks.get(task_id)
            if task:
                if result.error:
                    task.errors[msg.sender] = result.error
                else:
                    task.results[msg.sender] = result.output
                self._check_task_completion(task)
        self.hub.store_result(task_id, msg.sender, result.output or result.error)

    def _check_task_completion(self, task: Task) -> None:
        if task.is_done:
            return
        if self.aggregation == "first":
            for out in task.results.values():
                if out is not None:
                    self._finalize_task(task, TaskState.COMPLETED)
                    return
        agent_count = self._count_assigned(task)
        result_count = len(task.results) + len(task.errors)
        if result_count >= agent_count and agent_count > 0:
            has_any_success = len(task.results) > 0
            self._finalize_task(task, TaskState.COMPLETED if has_any_success else TaskState.FAILED)

    def _count_assigned(self, task: Task) -> int:
        agents = self.tm.find_available(
            [c.value for c in task.required_capabilities]
        )
        return min(len(agents), self.max_parallel)

    def _finalize_task(self, task: Task, state: TaskState) -> None:
        task.state = state
        task.completed_at = utcnow().isoformat()
        self.hub.ack_task(task.id)
        logger.info(f"Task {task.id[:8]} finalized: {state.value}")

    def _handle_heartbeat(self, msg: Message) -> None:
        pass

    def _handle_register(self, msg: Message) -> None:
        pass

    def _handle_status(self, msg: Message) -> None:
        pass

    def _handle_alert(self, msg: Message) -> None:
        event = msg.payload.get("event")
        agent_id = msg.payload.get("agent_id")
        if event == "agent_stale" and agent_id:
            logger.warning(f"Agent {agent_id[:8]} is stale, removing from topology")
            with self._lock:
                self._agent_states.pop(agent_id, None)

    def _tool_state_changed(self, name: str, old: AgentState, new: AgentState) -> None:
        logger.info(f"Tool {name} state: {old.value} -> {new.value}")
        for cb in self._state_listeners:
            cb(name, new.value)

    def _heartbeat_sender(self) -> None:
        while self._running:
            time.sleep(5)
            try:
                master = self.topology.master
                self.hub.send_heartbeat(
                    agent_id=master.id,
                    role=master.role,
                    state=AgentState.ONLINE,
                    pid=master.pid,
                    slots_used=master.slots_used,
                    slots_total=master.slots,
                    load=0.0,
                )
            except Exception as e:
                logger.debug(f"Heartbeat error: {e}")

    def submit_dag(self, task_descriptions: list[tuple[str, dict, list[str]]]) -> list[str]:
        ids = []
        for desc, payload, deps in task_descriptions:
            task = Task(description=desc, payload=payload)
            with self._lock:
                self._tasks[task.id] = task
            ids.append(self.dag_scheduler.submit(task, deps))
        return ids

    def wait_dag(self, timeout: float = 300.0) -> dict[str, str]:
        states = self.dag_scheduler.wait_all(timeout=timeout)
        return {tid: s.value for tid, s in states.items()}

    def _run_task(self, task: Task) -> bool:
        caps = [c.value for c in task.required_capabilities] if task.required_capabilities else None
        agents = self.tm.find_available(caps)
        if not agents:
            task.state = TaskState.FAILED
            task.errors["_system"] = "No available agents"
            task.completed_at = utcnow().isoformat()
            if self.journal:
                self.journal.write_task_failed(task.id, task.description, task.errors, task.elapsed)
            return False
        try:
            self._dispatch_task(task)
            if task.state == TaskState.COMPLETED:
                if self.journal:
                    self.journal.write_task_completed(task.id, task.description, task.results, task.elapsed)
                return True
            if self.journal:
                self.journal.write_task_failed(task.id, task.description, task.errors, task.elapsed)
            return False
        except Exception as e:
            task.state = TaskState.FAILED
            task.errors["_system"] = str(e)
            task.completed_at = utcnow().isoformat()
            if self.journal:
                self.journal.write_task_failed(task.id, task.description, task.errors, task.elapsed)
            return False

    def _sync_agents_to_topology(self) -> None:
        for name in self.tm.list_tools():
            rec = self.tm.get_record(name)
            if not rec:
                continue
            adapter = rec.adapter
            node = AgentNode(
                id=adapter.agent_id,
                name=name,
                role=AgentRole.SLAVE,
                state=adapter.state,
                capabilities={AgentCapability(c) for c in rec.capabilities},
                priority=rec.priority,
                slots=adapter.slots,
                pid=adapter.pid,
                label=rec.label,
                parent_id=self.node_id,
            )
            self.topology.slaves[node.id] = node
            self._agent_states[node.id] = node
            self.hub.register_agent(node)

    def _worker_loop(self) -> None:
        while self._running:
            try:
                task = self._pending.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self._dispatch_task(task)
            except Exception as e:
                logger.error(f"Dispatch error for task {task.id[:8]}: {e}")
                task.state = TaskState.FAILED
                task.errors["_system"] = str(e)
                task.completed_at = utcnow().isoformat()

    def _dispatch_task(self, task: Task) -> None:
        logger.info(f"Dispatching task {task.id[:8]}: {task.description[:50]} (strategy={task.strategy.value})")
        caps = [c.value for c in task.required_capabilities] if task.required_capabilities else None
        agents = self.tm.find_available(caps)
        if not agents:
            task.state = TaskState.FAILED
            task.errors["_system"] = f"No available agents for capabilities: {caps}"
            task.completed_at = utcnow().isoformat()
            logger.warning(f"Task {task.id[:8]}: no agents available")
            return
        logger.info(f"Task {task.id[:8]}: found {len(agents)} agents for dispatch")
        if task.strategy == Strategy.PIPELINE:
            self._pipeline_exec.execute(task, agents)
        elif task.strategy in (Strategy.PARALLEL, Strategy.ALL):
            self._parallel_exec.execute(task, agents)
        elif task.strategy == Strategy.HYBRID:
            self._pipeline_exec.execute(task, agents[:1])
        elif task.strategy == Strategy.LEADER_ELECT:
            self._pipeline_exec.execute(task, agents[:1])
        elif task.strategy == Strategy.CONSENSUS:
            self._parallel_exec.execute(task, agents)
        else:
            self._parallel_exec.execute(task, agents)


class DirectorAgent:
    """A director can break tasks into sub-tasks and assign them to specific slaves."""

    def __init__(self, agent_id: str, master: MasterOrchestrator):
        self.agent_id = agent_id
        self.master = master

    def delegate(self, subtask: Task, target_agent: str) -> str:
        msg = Message(
            msg_type=MT.TASK,
            sender=self.agent_id,
            payload=subtask.payload,
            task_id=subtask.id,
            target=target_agent,
            context_scope=subtask.id,
        )
        self.master.hub.publish_direct(target_agent, msg)
        return subtask.id

    def broadcast(self, task: Task) -> list[str]:
        ids = []
        msg = Message(
            msg_type=MT.BROADCAST,
            sender=self.agent_id,
            payload=task.payload,
            task_id=task.id,
        )
        self.master.hub.publish_broadcast(msg)
        ids.append(task.id)
        return ids

    def sync_context(self, source: str, targets: list[str], keys: list[str]) -> None:
        for t in targets:
            self.master.ctx.sync_to_agent(source, t, keys)


def create_orchestrator(config: dict, hub: RedisHub, tool_manager: ToolManager) -> MasterOrchestrator:
    return MasterOrchestrator(
        hub=hub,
        tool_manager=tool_manager,
        node_id=config.get("node_id", f"master:{uuid.uuid4().hex[:12]}"),
        max_parallel=config.get("max_parallel", 5),
        task_timeout=config.get("task_timeout", 300.0),
        result_aggregation=config.get("result_aggregation", "first"),
    )
