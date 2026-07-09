"""
WebSocket endpoint for real-time dashboard updates.

Clients connect to ws://<host>/ws and receive JSON frames pushed by the
background orchestrator whenever a new alert or metric snapshot is ready.

Frame shapes
------------
Alert push (from RCA engine)::

    { "type": "alert", "rca": {...}, "alert": {...} }

Heartbeat (every 30 s to keep the connection alive through proxies)::

    { "type": "ping", "timestamp": "2026-07-08T..." }

Clients may send any text frame — it is currently echoed back as a
keepalive ACK but otherwise ignored.
"""

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.utils.websocket_manager import manager

logger = logging.getLogger("rca.ws_router")

router = APIRouter(tags=["websocket"])

_HEARTBEAT_INTERVAL = 30  # seconds


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    heartbeat_task = asyncio.create_task(_heartbeat(ws))
    try:
        while True:
            # Keep receiving so the connection stays open; ignore client messages.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("WebSocket closed with: %s", exc)
    finally:
        heartbeat_task.cancel()
        await manager.disconnect(ws)


async def _heartbeat(ws: WebSocket):
    """Send a periodic ping frame to keep NAT/proxy connections alive."""
    import json
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL)
        try:
            await ws.send_text(
                json.dumps(
                    {
                        "type": "ping",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
            )
        except Exception:
            break
