"""PolyBridge CLI entry point — full system integration."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from typing import Any

from polylogue.config import load_config, BridgeConfig
from polylogue.election import LeaderElector
from polylogue.hub import create_hub, RedisHub
from polylogue.journal_bridge import JournalBridge
from polylogue.models import AgentRole, Strategy, new_id
from polylogue.monitor import ResourceMonitor
from polylogue.native_bridge import PolyBridgeProcess
from polylogue.orchestrate import MasterOrchestrator
from polylogue.tools import create_manager_from_config, ToolManager
from polylogue.transport import WireServer, create_tls_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class PolyBridge:
    """The master bridge — connects agents, orchestrates tasks, maintains topology."""

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.node_id = config.node_id or f"master:{new_id()[:12]}"
        self.hub: RedisHub | None = None
        self.tool_manager: ToolManager | None = None
        self.orchestrator: MasterOrchestrator | None = None
        self.journal: JournalBridge | None = None
        self.native_bridge: PolyBridgeProcess | None = None
        self.api_server: Any = None
        self.elector: LeaderElector | None = None
        self.monitor: ResourceMonitor | None = None
        self.wire_server: WireServer | None = None

    def start(self) -> None:
        logger.info(f"PolyBridge master {self.node_id[:8]} starting...")

        self.hub = create_hub({
            "host": self.config.redis_host,
            "port": self.config.redis_port,
            "db": self.config.redis_db,
            "password": self.config.redis_password,
            "prefix": self.config.redis_prefix,
            "heartbeat_interval": self.config.heartbeat_interval,
            "stale_timeout": self.config.stale_timeout,
        })

        if self.config.election.get("enabled", False):
            from polylogue.election import LeaderElector
            self.elector = LeaderElector(
                redis_conn=self.hub._redis,
                node_id=self.node_id,
                prefix=self.config.redis_prefix,
                lease_seconds=self.config.election.get("lease_seconds", 15.0),
                heartbeat_interval=self.config.election.get("heartbeat_interval", 5.0),
            )
            self.elector.start()
            if not self.elector.is_leader:
                logger.info("Not elected leader, running in standby mode")
                self._standby_loop()
                return
            logger.info("Elected as leader, continuing startup")

        logger.info("Initializing journal bridge...")
        self.journal = JournalBridge()
        journal_cfg = self.config.journal
        if journal_cfg.get("enabled", True):
            root = journal_cfg.get("root", "")
            if root:
                self.journal.set_root(root)
            else:
                self.journal.detect_root()
            self.journal.start()

        logger.info("Launching agent processes...")
        self.tool_manager = create_manager_from_config({"tools": self.config.tools})
        self.tool_manager.supervisor.max_restarts = self.config.max_restarts
        self.tool_manager.supervisor.restart_window = self.config.restart_window
        self.tool_manager.supervisor.health_interval = self.config.health_interval
        results = self.tool_manager.start_all()
        for name, ok in results.items():
            logger.info(f"  {name}: {'started' if ok else 'failed'}")
            if not ok and self.journal:
                adapter_state = self.tool_manager.supervisor.get(name)
                if adapter_state:
                    self.journal.write_agent_state(name, name, adapter_state.state)

        resource_cfg = self.config.resource_monitor
        if resource_cfg.get("enabled", True):
            self.monitor = ResourceMonitor(interval=resource_cfg.get("interval", 10.0))
            self.monitor.start()

        bridge_cfg = self.config.native_bridge
        if bridge_cfg.get("enabled", True):
            self.native_bridge = PolyBridgeProcess(
                bind_host=bridge_cfg.get("host", "127.0.0.1"),
                port=bridge_cfg.get("port", 7847),
                auto_build=bridge_cfg.get("auto_build", True),
            )
            self.native_bridge.start()

        tls_config = create_tls_config(self.config.transport)
        if self.config.wire_server.get("enabled", False):
            self.wire_server = WireServer(
                host=self.config.wire_server.get("host", "127.0.0.1"),
                port=self.config.wire_server.get("port", 7848),
                tls_config=tls_config if tls_config.enabled else None,
            )
            self.wire_server.start()

        logger.info("Initializing orchestrator...")
        self.orchestrator = MasterOrchestrator(
            hub=self.hub,
            tool_manager=self.tool_manager,
            node_id=self.node_id,
            max_parallel=self.config.max_parallel,
            task_timeout=self.config.task_timeout,
            result_aggregation=self.config.result_aggregation,
        )
        self.orchestrator.on_state_change(self._handle_agent_state_change)
        if self.journal:
            self.orchestrator.set_journal_bridge(self.journal)
        self.orchestrator.start()

        api_cfg = self.config.api_server
        if api_cfg.get("enabled", False):
            try:
                from polylogue.ws_api import create_api_server
                self.api_server = create_api_server({"api": api_cfg}, self.hub)
                if self.api_server:
                    self.api_server.start()
            except Exception as e:
                logger.warning(f"API server failed: {e}")

        if self.monitor and self.tool_manager:
            for name in self.tool_manager.list_tools():
                adapter = self.tool_manager.get_adapter(name)
                if adapter:
                    self.monitor.track(name, adapter.pid)

        self._register_signal_handlers()
        logger.info("PolyBridge fully operational. Press Ctrl+C to stop.")
        try:
            self._main_loop()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def _main_loop(self) -> None:
        while True:
            time.sleep(5)
            if self.monitor:
                snap = self.monitor.summary()
                logger.debug(f"Resources: {snap.get('total_rss_mb')}MB RSS across {snap.get('agent_count')} agents")
            if self.native_bridge and not self.native_bridge.is_running:
                logger.warning("Native bridge down, restarting...")
                self.native_bridge.start()

    def _standby_loop(self) -> None:
        try:
            while self.elector and not self.elector.is_leader:
                time.sleep(5)
        except KeyboardInterrupt:
            pass
        finally:
            if self.elector:
                self.elector.stop()

    def stop(self) -> None:
        logger.info("Shutting down PolyBridge...")
        if self.api_server:
            try:
                self.api_server.stop()
            except Exception:
                pass
        if self.orchestrator:
            self.orchestrator.stop()
        if self.native_bridge:
            self.native_bridge.stop()
        if self.wire_server:
            self.wire_server.stop()
        if self.monitor:
            self.monitor.stop()
        if self.journal:
            self.journal.stop()
        if self.tool_manager:
            self.tool_manager.stop_all(timeout=self.config.task_timeout)
        if self.elector:
            self.elector.stop()
        if self.hub:
            self.hub.disconnect()
        logger.info("PolyBridge stopped")

    def submit(self, description: str, payload: dict, strategy: str = "parallel", block: bool = True, timeout: float = 60.0) -> dict:
        if not self.orchestrator:
            return {"error": "Orchestrator not running"}
        strategy_enum = Strategy(strategy)
        task_id = self.orchestrator.submit_task(description, payload, strategy=strategy_enum, timeout=timeout)
        result = {"task_id": task_id}
        if block:
            task = self.orchestrator.wait_for_task(task_id, timeout=timeout)
            if task:
                result["state"] = task.state.value
                result["result"] = self.orchestrator.get_task_result(task_id)
            else:
                result["state"] = "timeout"
        return result

    def submit_dag(self, tasks: list[tuple[str, dict, list[str]]]) -> dict:
        if not self.orchestrator:
            return {"error": "Orchestrator not running"}
        ids = self.orchestrator.submit_dag(tasks)
        return {"task_ids": ids}

    def status(self) -> dict:
        status = {"node_id": self.node_id, "running": self.orchestrator is not None}
        if self.hub:
            agents = self.hub.list_agents()
            status["agents"] = {
                aid: {"name": a.name, "role": a.role.value, "state": a.state.value, "slots": a.slots, "slots_used": a.slots_used}
                for aid, a in agents.items()
            }
        if self.tool_manager:
            status["tools"] = self.tool_manager.status_all()
        if self.elector:
            status["leader"] = self.elector.get_status()
        if self.monitor:
            status["resources"] = self.monitor.summary()
        if self.journal:
            status["journal"] = {"root": str(self.journal._root) if self.journal._root else None, "active": self.journal.active}
        if self.native_bridge:
            status["native_bridge"] = self.native_bridge.get_stats()
        if self.hub:
            queues = self.hub.list_durable_queues()
            if queues:
                status["queues"] = {q: self.hub.durable_queue_len(q) for q in queues}
        return status

    def _handle_agent_state_change(self, name: str, state: str) -> None:
        logger.info(f"Agent state change: {name} -> {state}")

    def _register_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
        signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))


def _setup_bridge_for_submit(config: BridgeConfig) -> PolyBridge:
    bridge = PolyBridge(config)
    bridge.hub = create_hub({
        "host": config.redis_host, "port": config.redis_port,
        "db": config.redis_db, "password": config.redis_password,
        "prefix": config.redis_prefix,
    })
    bridge.tool_manager = create_manager_from_config({"tools": config.tools})
    bridge.orchestrator = MasterOrchestrator(
        hub=bridge.hub, tool_manager=bridge.tool_manager,
        node_id=bridge.node_id, max_parallel=config.max_parallel,
        task_timeout=config.task_timeout, result_aggregation=config.result_aggregation,
    )
    bridge.orchestrator.start()
    return bridge


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PolyBridge - Multi-agent orchestration bridge with master/slave topology",
    )
    parser.add_argument("-c", "--config", help="Path to config file")
    parser.add_argument(
        "command", nargs="?", choices=[
            "start", "stop", "status", "submit", "submit-dag",
            "agents", "topology", "monitor", "queues", "dag-status",
            "election", "journal",
        ],
        help="Command to run",
    )
    parser.add_argument("args", nargs="*", help="Arguments for command")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        logger.error(str(e))
        return 1

    bridge = PolyBridge(config)

    if args.command == "start" or not args.command:
        bridge.start()
    elif args.command == "status":
        st = bridge.status()
        print(f"Node ID: {st['node_id']}")
        print(f"Running: {st['running']}")
        if "leader" in st:
            ld = st["leader"]
            print(f"Leader: {ld['is_leader']} (term={ld['term']}, node={ld['node_id'][:12]})")
        if "agents" in st:
            print(f"\nAgents ({len(st['agents'])}):")
            for aid, info in st["agents"].items():
                print(f"  {aid[:12]} -> {info['name']} ({info['role']}) [{info['state']}] slots={info['slots_used']}/{info['slots']}")
        if "tools" in st:
            print(f"\nTools ({len(st['tools'])}):")
            for name, s in st["tools"].items():
                print(f"  {name}: {s['state']} (pid={s['pid']}, slots={s['slots_used']}/{s['slots']})")
        if "resources" in st:
            r = st["resources"]
            print(f"\nResources: {r['total_rss_mb']}MB RSS / {r['total_vms_mb']}MB VMS ({r['agent_count']} agents)")
        if "native_bridge" in st:
            nb = st["native_bridge"]
            print(f"\nNative bridge: {'running' if nb['running'] else 'stopped'} (pid={nb['pid']}, {nb['bind_host']}:{nb['port']})")
        if "journal" in st:
            print(f"\nJournal: root={st['journal']['root']}, active={st['journal']['active']}")
        if "queues" in st:
            print(f"\nQueues: {st['queues']}")
    elif args.command == "agents":
        bridge.hub = create_hub({
            "host": config.redis_host, "port": config.redis_port,
            "db": config.redis_db, "password": config.redis_password,
            "prefix": config.redis_prefix,
        })
        agents = bridge.hub.list_agents()
        for aid, a in agents.items():
            print(f"{aid[:16]}: {a.name} ({a.role.value}) - {a.state.value}")
        bridge.hub.disconnect()
    elif args.command == "topology":
        bridge.hub = create_hub({
            "host": config.redis_host, "port": config.redis_port,
            "db": config.redis_db, "password": config.redis_password,
            "prefix": config.redis_prefix,
        })
        agents = bridge.hub.list_agents()
        for aid, a in agents.items():
            indent = "  " if a.role != AgentRole.MASTER else ""
            print(f"{indent}{a.name} [{a.role.value}]")
            print(f"{indent}  ID: {aid[:16]}")
            print(f"{indent}  State: {a.state.value}")
            print(f"{indent}  Slots: {a.slots_used}/{a.slots}")
            print(f"{indent}  Caps: {', '.join(c.value for c in a.capabilities)}")
            if a.parent_id:
                print(f"{indent}  Parent: {a.parent_id[:16]}")
        bridge.hub.disconnect()
    elif args.command == "submit":
        if not args.args:
            logger.error("Usage: submit <description> [json payload] [--strategy pipeline|parallel|hybrid|consensus|all]")
            return 1
        description = args.args[0]
        payload = {}
        strategy = "parallel"
        remaining = list(args.args[1:])
        for i, arg in enumerate(remaining):
            if arg == "--strategy" and i + 1 < len(remaining):
                strategy = remaining[i + 1]
                remaining = remaining[:i] + remaining[i+2:]
                break
        if remaining:
            try:
                payload = json.loads(" ".join(remaining))
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON: {e}")
                return 1
        b = _setup_bridge_for_submit(config)
        result = b.submit(description, payload, strategy=strategy, block=True, timeout=config.task_timeout)
        print(json.dumps(result, indent=2, default=str))
        b.stop()
    elif args.command == "submit-dag":
        if not args.args:
            logger.error("Usage: submit-dag <json task graph>")
            return 1
        try:
            tasks = json.loads(" ".join(args.args))
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON: {e}")
            return 1
        b = _setup_bridge_for_submit(config)
        result = b.submit_dag(tasks)
        print(json.dumps(result, indent=2))
        b.stop()
    elif args.command == "dag-status":
        if not args.args:
            logger.error("Usage: dag-status <task_id>")
            return 1
        b = _setup_bridge_for_submit(config)
        task = b.orchestrator.get_task(args.args[0]) if b.orchestrator else None
        if task:
            print(f"Task: {task.id[:16]}")
            print(f"Description: {task.description}")
            print(f"State: {task.state.value}")
            print(f"Results: {json.dumps(task.results, default=str)[:500]}")
            print(f"Errors: {json.dumps(task.errors, default=str)[:500]}")
        else:
            print(f"Task {args.args[0][:16]} not found")
        b.stop()
    elif args.command == "monitor":
        b = _setup_bridge_for_submit(config)
        if b.monitor:
            print(json.dumps(b.monitor.summary(), indent=2))
        else:
            print("No resource monitor configured")
        b.stop()
    elif args.command == "queues":
        b = _setup_bridge_for_submit(config)
        if b.hub:
            queues = b.hub.list_durable_queues()
            for q in queues:
                length = b.hub.durable_queue_len(q)
                print(f"{q}: {length} items")
        b.stop()
    elif args.command == "election":
        bridge.hub = create_hub({
            "host": config.redis_host, "port": config.redis_port,
            "db": config.redis_db, "password": config.redis_password,
            "prefix": config.redis_prefix,
        })
        elector = LeaderElector(
            redis_conn=bridge.hub._redis,
            node_id=bridge.node_id,
            prefix=config.redis_prefix,
        )
        status = elector.get_status()
        print(json.dumps(status, indent=2))
        bridge.hub.disconnect()
    elif args.command == "journal":
        jb = JournalBridge()
        journal_cfg = config.journal
        root = journal_cfg.get("root", "")
        if root:
            jb.set_root(root)
        else:
            jb.detect_root()
        if jb._path and jb._path.exists():
            print(f"Journal: {jb._path}")
            print(f"Size: {jb._path.stat().st_size} bytes")
            with open(jb._path) as f:
                lines = f.readlines()
                print(f"Events: {len(lines)}")
                for line in lines[-20:]:
                    try:
                        ev = json.loads(line)
                        print(f"  {ev.get('ts')[:19]} {ev.get('type'):12} {ev.get('text', '')[:80]}")
                    except json.JSONDecodeError:
                        print(f"  (invalid) {line[:80]}")
        else:
            print("No journal found")
    elif args.command == "stop":
        print("PolyBridge stop requested. Run 'polylogue start' to restart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
