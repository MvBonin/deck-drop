"""Shared application state injected via FastAPI dependency."""

from __future__ import annotations

from deckdrop.core.config import Config
from deckdrop.core.library import Library
from deckdrop.network.peer_registry import PeerRegistry


class AppState:
    def __init__(
        self,
        cfg: Config,
        library: Library,
        peer_registry: PeerRegistry,
        transfer: object | None = None,  # TransferManager | None (optional dep)
    ) -> None:
        self.cfg = cfg
        self.library = library
        self.peer_registry = peer_registry
        self.transfer = transfer


_state: AppState | None = None


def init(
    cfg: Config,
    library: Library,
    peer_registry: PeerRegistry | None = None,
    transfer: object | None = None,
) -> None:
    global _state
    _state = AppState(
        cfg=cfg,
        library=library,
        peer_registry=peer_registry or PeerRegistry(),
        transfer=transfer,
    )


def get() -> AppState:
    if _state is None:
        raise RuntimeError("AppState not initialized")
    return _state
