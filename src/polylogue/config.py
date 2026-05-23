"""PolyBridge configuration loader - agent topology, Redis, supervision policies."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATHS = [
    "polylogue.yaml",
    "polylogue.yml",
    ".polylogue.yaml",
    ".polylogue.yml",
    os.path.expanduser("~/.config/polylogue.yaml"),
    "/etc/polylogue.yaml",
]


class BridgeConfig:
    def __init__(self, data: dict[str, Any]):
        self._data = data
        self._enabled_tools: dict[str, dict] = {}
        for name, tool in data.get("tools", {}).items():
            if tool.get("enabled", False):
                self._enabled_tools[name] = tool

    @property
    def redis(self) -> dict:
        return self._data.get("redis", {})

    @property
    def redis_host(self) -> str:
        return self.redis.get("host", "127.0.0.1")

    @property
    def redis_port(self) -> int:
        return self.redis.get("port", 6379)

    @property
    def redis_db(self) -> int:
        return self.redis.get("db", 0)

    @property
    def redis_password(self) -> str | None:
        return self.redis.get("password")

    @property
    def redis_prefix(self) -> str:
        return self.redis.get("prefix", "polylogue")

    @property
    def heartbeat_interval(self) -> float:
        return float(self.redis.get("heartbeat_interval", 5.0))

    @property
    def stale_timeout(self) -> float:
        return float(self.redis.get("stale_timeout", 15.0))

    @property
    def queue(self) -> dict:
        return self._data.get("queue", {})

    @property
    def task_channel(self) -> str:
        return f"{self.redis_prefix}:{self.queue.get('task_channel', 'tasks')}"

    @property
    def result_channel(self) -> str:
        return f"{self.redis_prefix}:{self.queue.get('result_channel', 'results')}"

    @property
    def tools(self) -> dict[str, dict]:
        return self._enabled_tools

    @property
    def agents(self) -> dict[str, dict]:
        return self._data.get("agents", {})

    @property
    def topology(self) -> dict:
        return self._data.get("topology", {})

    @property
    def orchestration(self) -> dict:
        return self._data.get("orchestration", {})

    @property
    def strategy(self) -> str:
        return self.orchestration.get("strategy", "parallel")

    @property
    def max_parallel(self) -> int:
        return self.orchestration.get("max_parallel", 5)

    @property
    def max_parallel_tasks(self) -> int:
        return self.max_parallel

    @property
    def task_timeout(self) -> float:
        return float(self.orchestration.get("task_timeout", 300.0))

    @property
    def result_aggregation(self) -> str:
        return self.orchestration.get("result_aggregation", "first")

    @property
    def consensus(self) -> dict:
        return self.orchestration.get("consensus", {})

    @property
    def supervision(self) -> dict:
        return self._data.get("supervision", {})

    @property
    def max_restarts(self) -> int:
        return self.supervision.get("max_restarts", 5)

    @property
    def restart_window(self) -> float:
        return float(self.supervision.get("restart_window", 60.0))

    @property
    def health_interval(self) -> float:
        return float(self.supervision.get("health_interval", 15.0))

    @property
    def bridge(self) -> dict:
        return self._data.get("bridge", {})

    @property
    def bridge_enabled(self) -> bool:
        return self.bridge.get("enabled", False)

    @property
    def bridge_host(self) -> str:
        return self.bridge.get("host", "127.0.0.1")

    @property
    def bridge_port(self) -> int:
        return self.bridge.get("port", 7847)

    @property
    def logging_cfg(self) -> dict:
        return self._data.get("logging", {})

    @property
    def log_level(self) -> str:
        return self.logging_cfg.get("level", "INFO")

    @property
    def log_file(self) -> str | None:
        return self.logging_cfg.get("file")

    @property
    def node_id(self) -> str:
        return self._data.get("node_id", "")


def load_config(config_path: str | None = None) -> BridgeConfig:
    if config_path:
        paths = [config_path]
    else:
        paths = DEFAULT_CONFIG_PATHS
    for path in paths:
        p = Path(path)
        if p.exists():
            with open(p) as f:
                data = yaml.safe_load(f)
            return BridgeConfig(data)
    raise FileNotFoundError(f"No config found in: {paths}")
