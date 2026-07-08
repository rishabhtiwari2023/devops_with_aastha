"""
WebSocket connection manager for real-time dashboard updates.

A single `manager` singleton is imported by both routers (to expose the
/ws endpoint) and by background.py (to push alert/metric events without
this module knowing anything about FastAPI or collectors).

All connected clients receive every broadcasted payload; the React
frontend filters by `payload.type` to route messages to the right panel.
"""

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("rca.ws")


class ConnectionManager:
    def __init__(self):
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self._connections.append(ws)
        logger.debug("WS client connected  (total=%d)", len(self._connections))

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self._connections = [c for c in self._connections if c is not ws]
        logger.debug("WS client disconnected (remaining=%d)", len(self._connections))

    async def broadcast(self, payload: Any):
        """Serialize `payload` to JSON and push to every live connection."""
        data = json.dumps(payload) if isinstance(payload, dict) else str(payload)
        dead: list[WebSocket] = []

        async with self._lock:
            clients = list(self._connections)

        for ws in clients:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)

        for ws in dead:
            await self.disconnect(ws)

    def broadcast_sync(self, payload: Any):
        """
        Fire-and-forget bridge for synchronous callers (e.g. APScheduler
        callbacks running in a thread).  Schedules a coroutine on the
        already-running event loop.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self.broadcast(payload))
            )
        except RuntimeError:
            pass  # no event loop — test / startup context


# Singleton imported everywhere
manager = ConnectionManager()
