"""Real-time WebSocket chat bridge between LLM surfaces."""

from .client import bridge_status, connect_loop, send_message
from .server import BridgeServer

__all__ = ["BridgeServer", "bridge_status", "connect_loop", "send_message"]
