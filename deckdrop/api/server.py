"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from deckdrop.api.routes import downloads, games, peers, settings, status
from deckdrop.api.websocket import router as ws_router


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

    # Serve frontend static files
    frontend_dir = Path(__file__).parent.parent.parent / "frontend"
    if frontend_dir.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

    return app
