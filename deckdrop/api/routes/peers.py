"""
/api/peers – list known peers + their games.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from deckdrop.api import state as app_state
from deckdrop.api.deps import local_only
from deckdrop.api.routes.games import CommentOut

router = APIRouter(tags=["peers"], dependencies=[Depends(local_only)])

_PEER_COMMENT_TIMEOUT = 3.0


def _peer_http_url(address: str, port: int, path: str) -> str:
    host = address
    if ":" in address and not address.startswith("["):
        host = f"[{address}]"
    return f"http://{host}:{port}{path}"


class PeerOut(BaseModel):
    peer_id: str
    name: str
    address: str
    port: int
    online: bool
    game_count: int


@router.get("/peers", response_model=list[PeerOut])
def list_peers() -> list[PeerOut]:
    registry = app_state.get().peer_registry
    return [
        PeerOut(
            peer_id=p.peer_id,
            name=p.name,
            address=p.address,
            port=p.port,
            online=p.online,
            game_count=len(p.games),
        )
        for p in registry.all()
    ]


@router.get("/peers/{peer_id}/games")
def get_peer_games(peer_id: str) -> list[dict]:
    registry = app_state.get().peer_registry
    entry = registry.get(peer_id)
    if not entry:
        raise HTTPException(404, "Peer not found")
    return entry.games


@router.get("/network/games")
def all_network_games() -> list[dict]:
    """All games from all online peers (for the Network view in the UI)."""
    return app_state.get().peer_registry.all_network_games()


@router.get(
    "/peers/{peer_id}/games/{game_id}/comments",
    response_model=list[CommentOut],
)
def get_peer_game_comments(peer_id: str, game_id: str) -> list[CommentOut]:
    """Proxy comments from a remote peer (read-only for Network view)."""
    registry = app_state.get().peer_registry
    entry = registry.get(peer_id)
    if not entry or not entry.online:
        raise HTTPException(404, "Peer nicht gefunden")
    if not any(g.get("id") == game_id for g in entry.games):
        raise HTTPException(404, "Spiel beim Peer nicht gefunden")

    url = _peer_http_url(entry.address, entry.port, f"/api/games/{game_id}/comments")
    try:
        r = httpx.get(url, timeout=_PEER_COMMENT_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(
            502,
            f"Kommentare vom Peer nicht abrufbar (HTTP {exc.response.status_code}): {detail}",
        ) from exc
    except Exception as exc:
        raise HTTPException(502, f"Kommentare vom Peer nicht abrufbar: {exc}") from exc
