"""Retry policies — exponential backoff for task execution."""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class BackoffStrategy(Enum):
    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    EXPONENTIAL_JITTER = "exponential_jitter"
    DECORRELATED_JITTER = "decorrelated_jitter"


@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL_JITTER
    retryable_errors: tuple[type[Exception], ...] = (Exception,)
    jitter_factor: float = 0.25

    def delay(self, attempt: int) -> float:
        n = attempt + 1
        if self.strategy == BackoffStrategy.FIXED:
            d = self.base_delay
        elif self.strategy == BackoffStrategy.LINEAR:
            d = self.base_delay * n
        elif self.strategy == BackoffStrategy.EXPONENTIAL:
            d = self.base_delay * (2 ** (n - 1))
        elif self.strategy == BackoffStrategy.EXPONENTIAL_JITTER:
            d = self.base_delay * (2 ** (n - 1))
            d = d * (1 - self.jitter_factor + 2 * self.jitter_factor * random.random())
        elif self.strategy == BackoffStrategy.DECORRELATED_JITTER:
            if n == 1:
                d = self.base_delay
            else:
                d = min(self.base_delay * (2 ** (n - 1)), random.uniform(self.base_delay, d * 3))
        else:
            d = self.base_delay
        return min(d, self.max_delay)


RetryHandler = Callable[[], Any]


def retry_sync(
    handler: RetryHandler,
    policy: RetryPolicy | None = None,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> Any:
    p = policy or RetryPolicy()
    last_error: Exception | None = None
    for attempt in range(p.max_retries + 1):
        try:
            return handler()
        except p.retryable_errors as e:
            last_error = e
            if attempt < p.max_retries:
                d = p.delay(attempt)
                if on_retry:
                    on_retry(attempt + 1, e)
                logger.debug(f"Retry {attempt + 1}/{p.max_retries} after {d:.1f}s: {e}")
                time.sleep(d)
            else:
                raise
    raise last_error  # type: ignore[misc]


class RetryExecutor:
    """Wraps task execution with configurable retry policies."""

    def __init__(self, default_policy: RetryPolicy | None = None):
        self.default_policy = default_policy or RetryPolicy()
        self._policies: dict[str, RetryPolicy] = {}

    def set_policy(self, task_type: str, policy: RetryPolicy) -> None:
        self._policies[task_type] = policy

    def execute(self, task_id: str, task_type: str, handler: RetryHandler) -> Any:
        policy = self._policies.get(task_type, self.default_policy)
        return retry_sync(handler, policy)


def default_policy() -> RetryPolicy:
    return RetryPolicy(
        max_retries=3,
        base_delay=1.0,
        max_delay=60.0,
        strategy=BackoffStrategy.EXPONENTIAL_JITTER,
        jitter_factor=0.25,
    )
