"""
/api/peers – list known peers discovered via mDNS.
Actual discovery logic lives in network/discovery.py (Phase 2).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["peers"])


class PeerOut(BaseModel):
    peer_id: str
    name: str
    address: str
    port: int
    online: bool


# Filled in Phase 2 when network/peer_registry.py is wired up
_peer_registry: list[PeerOut] = []


@router.get("/peers", response_model=list[PeerOut])
def list_peers() -> list[PeerOut]:
    return _peer_registry
