"""GET /api/status"""

from __future__ import annotations

from fastapi import APIRouter

from deckdrop import __version__
from deckdrop.api import state as app_state

router = APIRouter(tags=["status"])


@router.get("/status")
def get_status() -> dict:
    cfg = app_state.get().cfg
    return {
        "version": __version__,
        "name": cfg.user_name,
        "peer_id": cfg.peer_id,
        "onboarding_complete": cfg.onboarding_complete,
    }
