"""Real-time WebSocket chat bridge between LLM surfaces."""

from .client import bridge_status, connect_loop, send_message
from .listener import listen_loop, queue_message, state_paths
from .resolve import resolve_bridge_context, resolve_send_target
from .server import BridgeServer

__all__ = [
    "BridgeServer",
    "bridge_status",
    "connect_loop",
    "listen_loop",
    "queue_message",
    "resolve_bridge_context",
    "resolve_send_target",
    "send_message",
    "state_paths",
]
