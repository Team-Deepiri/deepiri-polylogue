"""Polylogue CLI entry point."""
import argparse
import asyncio
import logging
import signal
import sys
import time
from typing import Optional

from polylogue.config import load_config, Config
from polylogue.hub import create_hub, RedisHub
from polylogue.orchestrate import Orchestrator
from polylogue.tools import create_manager_from_config, ToolManager


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


logger = logging.getLogger(__name__)


class Polylogue:
    def __init__(self, config: Config):
        self.config = config
        self.hub: Optional[RedisHub] = None
        self.tool_manager: Optional[ToolManager] = None
        self.orchestrator: Optional[Orchestrator] = None

    def start(self) -> None:
        logger.info("Starting polylogue...")

        logger.info(f"Connecting to Redis at {self.config.redis_host}:{self.config.redis_port}")
        self.hub = create_hub(self.config.redis)

        logger.info("Starting AI tools...")
        self.tool_manager = create_manager_from_config({"tools": self.config.tools})
        results = self.tool_manager.start_all()

        for name, success in results.items():
            if not success:
                logger.warning(f"Failed to start tool: {name}")

        time.sleep(1)

        for name, tool in self.tool_manager._tools.items():
            if tool.status.state.name == "RUNNING":
                self.hub.register_agent(name, tool.get_metadata())

        logger.info("Starting orchestrator...")
        self.orchestrator = Orchestrator(
            hub=self.hub,
            tool_manager=self.tool_manager,
            strategy=self.config.strategy,
            max_parallel=self.config.max_parallel_tasks,
            timeout=self.config.task_timeout,
            aggregation=self.config.result_aggregation,
        )
        self.orchestrator.start()

        logger.info("Polylogue running. Press Ctrl+C to stop.")

        try:
            while True:
                time.sleep(1)
                self._check_tools()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        logger.info("Stopping polylogue...")
        if self.orchestrator:
            self.orchestrator.stop()
        if self.tool_manager:
            self.tool_manager.stop_all()
        if self.hub:
            self.hub.disconnect()
        logger.info("Polylogue stopped")

    def _check_tools(self) -> None:
        for name, tool in self.tool_manager._tools.items():
            if tool.status.state.name == "TERMINATED":
                logger.warning(f"Tool {name} terminated, restarting...")
                tool.start()
                if tool.status.state.name == "RUNNING":
                    self.hub.register_agent(name, tool.get_metadata())

    def submit(self, description: str, payload: dict, block: bool = True, timeout: int = 60) -> Optional[dict]:
        if not self.orchestrator:
            logger.error("Orchestrator not running")
            return None

        task_id = self.orchestrator.submit_task(description, payload)
        logger.info(f"Submitted task: {task_id}")

        if block:
            result = self.orchestrator.wait_for_result(task_id, timeout=timeout)
            return {"task_id": task_id, "result": result}

        return {"task_id": task_id}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Polylogue - Multi-agent AI coding assistant orchestration",
    )
    parser.add_argument(
        "-c", "--config",
        help="Path to config file",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["start", "stop", "status", "submit"],
        help="Command to run",
    )
    parser.add_argument(
        "args",
        nargs="*",
        help="Arguments for command",
    )

    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        logger.error(str(e))
        return 1

    polylogue = Polylogue(config)

    if args.command == "start" or not args.command:
        polylogue.start()
    elif args.command == "status":
        polylogue.tool_manager = create_manager_from_config({"tools": config.tools})
        print("Tools:")
        for name, status in polylogue.tool_manager.list_status().items():
            print(f"  {name}: {status.state.value} (pid={status.pid})")
    elif args.command == "stop":
        print("Stopping polylogue components...")
        print("Done.")
    elif args.command == "submit":
        if not args.args:
            logger.error("Usage: submit <description> [json payload]")
            return 1
        description = args.args[0]
        payload = {}
        if len(args.args) > 1:
            import json
            try:
                payload = json.loads(" ".join(args.args[1:]))
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON: {e}")
                return 1
        result = polylogue.submit(description, payload)
        if result:
            print(f"Task: {result['task_id']}")
            print(f"Result: {result.get('result')}")
        else:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())