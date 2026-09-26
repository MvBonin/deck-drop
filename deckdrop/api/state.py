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
        content: object | None = None,  # ContentTracker | None (optional dep)
    ) -> None:
        self.cfg = cfg
        self.library = library
        self.peer_registry = peer_registry
        self.transfer = transfer
        self.content = content

    def get_content_tracker(self) -> object:
        """Return the ContentTracker, lazily creating a default one if unset.

        Keeps app_state.init() backward compatible for callers/tests that
        don't pass a tracker explicitly.
        """
        if self.content is None:
            from deckdrop.core.content_tracker import ContentTracker

            self.content = ContentTracker(self.cfg, self.library)
        return self.content


_state: AppState | None = None


def init(
    cfg: Config,
    library: Library,
    peer_registry: PeerRegistry | None = None,
    transfer: object | None = None,
    content: object | None = None,
) -> None:
    global _state
    _state = AppState(
        cfg=cfg,
        library=library,
        peer_registry=peer_registry or PeerRegistry(),
        transfer=transfer,
        content=content,
    )


def get() -> AppState:
    if _state is None:
        raise RuntimeError("AppState not initialized")
    return _state
