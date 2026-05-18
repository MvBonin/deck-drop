"""FastAPI application factory."""

from __future__ import annotations

import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from deckdrop.api.routes import downloads, games, peers, settings, status
from deckdrop.api.websocket import router as ws_router


def _find_frontend() -> Path | None:
    if getattr(sys, "frozen", False):
        p = Path(sys._MEIPASS) / "frontend"  # type: ignore[attr-defined]
        if p.exists():
            return p
    try:
        from importlib.resources import files

        p = Path(str(files("deckdrop"))) / "frontend"
        if p.exists():
            return p
    except Exception:
        pass
    p = Path(__file__).parent.parent.parent / "frontend"
    if p.exists():
        return p
    return None


@asynccontextmanager
async def _default_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


def create_app(lifespan: Any = None) -> FastAPI:
    app = FastAPI(
        title="DeckDrop",
        version="2.0.0",
        docs_url="/api/docs",
        lifespan=lifespan or _default_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # LAN-only, no auth needed
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(games.router, prefix="/api")
    app.include_router(peers.router, prefix="/api")
    app.include_router(downloads.router, prefix="/api")
    app.include_router(settings.router, prefix="/api")
    app.include_router(status.router, prefix="/api")
    app.include_router(ws_router)

    frontend_dir = _find_frontend()
    if frontend_dir:
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

    return app
