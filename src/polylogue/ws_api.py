"""WebSocket & HTTP API — allows external agents to connect without Redis directly."""

from __future__ import annotations

import asyncio
import json
import logging
import threading

from polylogue.hub import Message, MessageType, RedisHub
from polylogue.models import AgentNode, AgentRole, AgentState, new_id, utcnow

logger = logging.getLogger(__name__)

try:
    from aiohttp import web  # type: ignore
    from aiohttp.web import Application, AppRunner, TCPSite, Request, WebSocketResponse, WSMsgType  # type: ignore
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False
    web = None
    Application = None
    AppRunner = None
    TCPSite = None
    Request = None
    WebSocketResponse = None
    WSMsgType = None


class AgentAPIServer:
    """HTTP/WebSocket API server for external agents to connect, register, and communicate."""

    def __init__(
        self,
        hub: RedisHub,
        host: str = "127.0.0.1",
        port: int = 9876,
        tls_cert: str | None = None,
        tls_key: str | None = None,
    ):
        if not HAS_AIOHTTP:
            raise RuntimeError("aiohttp required: pip install aiohttp")
        self.hub = hub
        self.host = host
        self.port = port
        self.tls_cert = tls_cert
        self.tls_key = tls_key
        self._app: Application = web.Application()
        self._runner: AppRunner | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._ws_clients: dict[str, WebSocketResponse] = {}
        self._setup_routes()

    def _setup_routes(self) -> None:
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_get("/api/agents", self._handle_list_agents)
        self._app.router.add_get("/api/agent/{agent_id}", self._handle_get_agent)
        self._app.router.add_post("/api/task", self._handle_submit_task)
        self._app.router.add_get("/api/task/{task_id}", self._handle_get_task)
        self._app.router.add_get("/ws", self._handle_websocket)

    async def _handle_health(self, request: Request) -> web.Response:
        return web.json_response({"status": "ok", "timestamp": utcnow().isoformat()})

    async def _handle_list_agents(self, request: Request) -> web.Response:
        agents = self.hub.list_agents()
        data = {
            aid: {
                "name": a.name,
                "role": a.role.value,
                "state": a.state.value,
                "capabilities": [c.value for c in a.capabilities],
                "slots": a.slots,
                "slots_used": a.slots_used,
                "priority": a.priority,
            }
            for aid, a in agents.items()
        }
        return web.json_response(data)

    async def _handle_get_agent(self, request: Request) -> web.Response:
        agent_id = request.match_info["agent_id"]
        agent = self.hub.get_agent(agent_id)
        if not agent:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response({
            "id": agent.id,
            "name": agent.name,
            "role": agent.role.value,
            "state": agent.state.value,
            "capabilities": [c.value for c in agent.capabilities],
            "slots": agent.slots,
            "slots_used": agent.slots_used,
            "pid": agent.pid,
            "parent_id": agent.parent_id,
            "context_scope": agent.context_scope,
        })

    async def _handle_submit_task(self, request: Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)
        description = body.get("description", "api task")
        payload = body.get("payload", {})
        strategy = body.get("strategy", "parallel")
        msg = Message(
            msg_type=MessageType.TASK,
            sender="api",
            payload={"description": description, "payload": payload, "strategy": strategy},
        )
        self.hub.publish_task(msg)
        return web.json_response({
            "task_id": msg.task_id,
            "description": description,
            "strategy": strategy,
        })

    async def _handle_get_task(self, request: Request) -> web.Response:
        task_id = request.match_info["task_id"]
        completed = self.hub.is_completed(task_id)
        results = self.hub.get_results(task_id)
        return web.json_response({
            "task_id": task_id,
            "completed": completed,
            "results": results,
        })

    async def _handle_websocket(self, request: Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        agent_id = request.query.get("agent_id", f"ws:{new_id()[:12]}")
        agent_name = request.query.get("name", agent_id[:12])
        self._ws_clients[agent_id] = ws
        agent = AgentNode(
            id=agent_id,
            name=agent_name,
            role=AgentRole.BRIDGE,
            state=AgentState.ONLINE,
            label=f"WS:{agent_name}",
        )
        self.hub.register_agent(agent)
        logger.info(f"WS agent connected: {agent_id[:16]} ({agent_name})")
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        msg_type_str = data.get("type", "task")
                        self.hub.publish("tasks", Message(
                            msg_type=MessageType(msg_type_str),
                            sender=agent_id,
                            payload=data.get("payload", data),
                        ))
                    except (json.JSONDecodeError, ValueError) as e:
                        await ws.send_json({"error": str(e)})
                elif msg.type == WSMsgType.ERROR:
                    break
        except Exception as e:
            logger.warning(f"WS agent {agent_id[:16]} error: {e}")
        finally:
            self._ws_clients.pop(agent_id, None)
            self.hub.deregister_agent(agent_id)
            logger.info(f"WS agent disconnected: {agent_id[:16]}")
        return ws

    def broadcast_to_ws(self, message: dict) -> None:
        loop = self._loop
        if loop is None:
            return
        for agent_id, ws in list(self._ws_clients.items()):
            try:
                if ws and not ws.closed:
                    asyncio.run_coroutine_threadsafe(
                        ws.send_json(message), loop
                    )
            except Exception as e:
                logger.warning(f"WS broadcast to {agent_id[:16]} failed: {e}")

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="api-server")
        self._thread.start()
        logger.info(f"Agent API server starting on {self.host}:{self.port}")

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        ssl_ctx = None
        if self.tls_cert and self.tls_key:
            import ssl
            ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_ctx.load_cert_chain(self.tls_cert, self.tls_key)
        self._runner = AppRunner(self._app)
        self._loop.run_until_complete(self._runner.setup())
        site = TCPSite(self._runner, self.host, self.port, ssl_context=ssl_ctx)
        self._loop.run_until_complete(site.start())
        logger.info(f"Agent API server listening on {'https' if ssl_ctx else 'http'}://{self.host}:{self.port}")
        try:
            self._loop.run_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self._loop.run_until_complete(self._runner.cleanup())
            self._loop.close()

    def stop(self) -> None:
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("Agent API server stopped")


def create_api_server(config: dict, hub: RedisHub) -> AgentAPIServer | None:
    if not HAS_AIOHTTP:
        logger.warning("aiohttp not installed; API server disabled (pip install aiohttp)")
        return None
    api_cfg = config.get("api", {})
    if not api_cfg.get("enabled", False):
        return None
    return AgentAPIServer(
        hub=hub,
        host=api_cfg.get("host", "127.0.0.1"),
        port=api_cfg.get("port", 9876),
        tls_cert=api_cfg.get("tls_cert"),
        tls_key=api_cfg.get("tls_key"),
    )
