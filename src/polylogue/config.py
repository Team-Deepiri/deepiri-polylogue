"""Polylogue configuration loader."""
import os
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATHS = [
    "polylogue.yaml",
    "polylogue.yml",
    ".polylogue.yaml",
    ".polylogue.yml",
    os.path.expanduser("~/.config/polylogue.yaml"),
    "/etc/polylogue.yaml",
]


class Config:
    def __init__(self, data: dict[str, Any]):
        self._data = data
        self._tools: dict[str, dict] = {}
        
        tools = data.get("tools", {})
        for name, tool in tools.items():
            if tool.get("enabled", False):
                self._tools[name] = tool

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
    def queue(self) -> dict:
        return self._data.get("queue", {})

    @property
    def task_channel(self) -> str:
        q = self.queue
        return f"{self.redis_prefix}:{q.get('task_channel', 'tasks')}"

    @property
    def result_channel(self) -> str:
        q = self.queue
        return f"{self.redis_prefix}:{q.get('result_channel', 'results')}"

    @property
    def tools(self) -> dict[str, dict]:
        return self._tools

    @property
    def orchestration(self) -> dict:
        return self._data.get("orchestration", {})

    @property
    def strategy(self) -> str:
        return self.orchestration.get("strategy", "pipeline")

    @property
    def max_parallel_tasks(self) -> int:
        return self.orchestration.get("max_parallel_tasks", 3)

    @property
    def task_timeout(self) -> int:
        return self.orchestration.get("task_timeout", 300)

    @property
    def result_aggregation(self) -> str:
        return self.orchestration.get("result_aggregation", "first")

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
    def logging(self) -> dict:
        return self._data.get("logging", {})

    @property
    def log_level(self) -> str:
        return self.logging.get("level", "INFO")

    @property
    def log_file(self) -> str | None:
        return self.logging.get("file")


def load_config(config_path: str | None = None) -> Config:
    if config_path:
        paths = [config_path]
    else:
        paths = DEFAULT_CONFIG_PATHS

    for path in paths:
        p = Path(path)
        if p.exists():
            with open(p) as f:
                data = yaml.safe_load(f)
            return Config(data)

    raise FileNotFoundError(f"No config found in: {paths}")