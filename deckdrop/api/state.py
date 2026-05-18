"""Shared application state injected via FastAPI dependency."""

from __future__ import annotations

from deckdrop.core.config import Config
from deckdrop.core.library import Library


class AppState:
    def __init__(self, cfg: Config, library: Library) -> None:
        self.cfg = cfg
        self.library = library


_state: AppState | None = None


def init(cfg: Config, library: Library) -> None:
    global _state
    _state = AppState(cfg, library)


def get() -> AppState:
    if _state is None:
        raise RuntimeError("AppState not initialized")
    return _state
