"""
/api/peers – list known peers + their games.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from deckdrop.api import state as app_state
from deckdrop.api.deps import local_only

router = APIRouter(tags=["peers"], dependencies=[Depends(local_only)])


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
