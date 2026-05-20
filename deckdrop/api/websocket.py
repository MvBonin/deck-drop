"""WebSocket endpoint for live updates pushed to the frontend."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

_connections: set[WebSocket] = set()


_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    host = ws.client.host if ws.client else ""
    if host not in _LOCAL_HOSTS:
        await ws.close(code=1008)
        return
    await ws.accept()
    _connections.add(ws)
    try:
        while True:
            # Keep alive – client can send pings, we ignore them
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _connections.discard(ws)


async def broadcast(event: str, data: Any) -> None:
    """Send a typed event to all connected clients."""
    if not _connections:
        return
    message = json.dumps({"event": event, "data": data})
    dead: set[WebSocket] = set()
    for ws in list(_connections):
        try:
            await ws.send_text(message)
        except Exception:
            dead.add(ws)
    _connections.difference_update(dead)
