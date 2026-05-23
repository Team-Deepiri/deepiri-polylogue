"""PolyBridge CLI entry point."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time


from polylogue.config import load_config, BridgeConfig
from polylogue.hub import create_hub, RedisHub
from polylogue.models import AgentRole, Strategy, new_id
from polylogue.orchestrate import MasterOrchestrator
from polylogue.tools import create_manager_from_config, ToolManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class PolyBridge:
    """The master bridge - connects agents, orchestrates tasks, maintains topology."""

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.node_id = config.node_id or f"master:{new_id()[:12]}"
        self.hub: RedisHub | None = None
        self.tool_manager: ToolManager | None = None
        self.orchestrator: MasterOrchestrator | None = None

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

        logger.info("Launching agent processes...")
        self.tool_manager = create_manager_from_config({"tools": self.config.tools})
        self.tool_manager.supervisor.max_restarts = self.config.max_restarts
        self.tool_manager.supervisor.restart_window = self.config.restart_window
        self.tool_manager.supervisor.health_interval = self.config.health_interval
        results = self.tool_manager.start_all()
        for name, ok in results.items():
            if ok:
                logger.info(f"  {name}: started")
            else:
                logger.warning(f"  {name}: failed to start")

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
        self.orchestrator.start()

        logger.info("PolyBridge running. Press Ctrl+C to stop.")
        self._register_signal_handlers()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        logger.info("Shutting down PolyBridge...")
        if self.orchestrator:
            self.orchestrator.stop()
        if self.tool_manager:
            self.tool_manager.stop_all(timeout=self.config.task_timeout)
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
        return status

    def _handle_agent_state_change(self, name: str, state: str) -> None:
        logger.info(f"Agent state change: {name} -> {state}")

    def _register_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
        signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PolyBridge - Multi-agent orchestration bridge with master/slave topology",
    )
    parser.add_argument("-c", "--config", help="Path to config file")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["start", "stop", "status", "submit", "agents", "topology"],
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
        if "agents" in st:
            print(f"\nRegistered agents ({len(st['agents'])}):")
            for aid, info in st["agents"].items():
                print(f"  {aid[:12]} -> {info['name']} ({info['role']}) [{info['state']}] slots={info['slots_used']}/{info['slots']}")
        if "tools" in st:
            print(f"\nManaged tools ({len(st['tools'])}):")
            for name, s in st["tools"].items():
                print(f"  {name}: {s['state']} (pid={s['pid']}, slots={s['slots_used']}/{s['slots']})")
    elif args.command == "agents":
        if bridge.hub:
            bridge.hub.connect()
            agents = bridge.hub.list_agents()
            for aid, a in agents.items():
                print(f"{aid[:16]}: {a.name} ({a.role.value}) - {a.state.value}")
    elif args.command == "topology":
        if bridge.hub:
            bridge.hub.connect()
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
    elif args.command == "submit":
        if not args.args:
            logger.error("Usage: submit <description> [json payload] [--strategy pipeline|parallel|hybrid]")
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
        bridge.hub = create_hub({
            "host": config.redis_host,
            "port": config.redis_port,
            "db": config.redis_db,
            "password": config.redis_password,
            "prefix": config.redis_prefix,
        })
        bridge.tool_manager = create_manager_from_config({"tools": config.tools})
        bridge.orchestrator = MasterOrchestrator(
            hub=bridge.hub,
            tool_manager=bridge.tool_manager,
            node_id=bridge.node_id,
            max_parallel=config.max_parallel,
            task_timeout=config.task_timeout,
            result_aggregation=config.result_aggregation,
        )
        bridge.orchestrator.start()
        result = bridge.submit(description, payload, strategy=strategy, block=True, timeout=config.task_timeout)
        print(json.dumps(result, indent=2, default=str))
        bridge.stop()
    elif args.command == "stop":
        print("PolyBridge stop requested. Run 'polylogue start' to restart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
