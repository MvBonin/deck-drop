"""Entry point: CLI + server startup."""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="deckdrop",
        description="LAN game sharing for Steam Deck and Linux",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run API server only, don't open browser",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind host (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override port from config",
    )
    args = parser.parse_args()

    _run(headless=args.headless, host=args.host, port_override=args.port)


def _run(headless: bool, host: str, port_override: int | None) -> None:
    from contextlib import asynccontextmanager

    import uvicorn
    from fastapi import FastAPI

    from deckdrop.api import state as app_state
    from deckdrop.api.server import create_app
    from deckdrop.core import config as cfg_mod
    from deckdrop.core.library import Library
    from deckdrop.network.discovery import DiscoveryService
    from deckdrop.network.peer_registry import PeerRegistry

    cfg = cfg_mod.load()
    library = Library()
    library.reload(cfg)
    peer_registry = PeerRegistry()

    # TransferManager is optional (requires libtorrent)
    transfer = None
    try:
        from deckdrop.network.transfer import TransferManager

        transfer = TransferManager(cfg)
        transfer.set_library(library)
    except RuntimeError:
        import logging

        logging.getLogger(__name__).warning("libtorrent not available – transfers disabled")

    app_state.init(cfg, library, peer_registry, transfer)

    port = port_override or cfg.port

    # Ensure directories exist
    cfg.download_dir.mkdir(parents=True, exist_ok=True)
    cfg.torrent_cache.mkdir(parents=True, exist_ok=True)

    discovery = DiscoveryService()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        try:
            await discovery.start(cfg, peer_registry.upsert_sync, peer_registry.remove)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("mDNS discovery unavailable: %s", exc)
        if transfer:
            transfer.start_polling()
        yield
        # Shutdown
        await discovery.stop()
        if transfer:
            transfer.shutdown()

    app = create_app(lifespan=lifespan)

    if not headless:
        import threading
        import time
        import webbrowser

        def _open_browser() -> None:
            time.sleep(1.2)
            webbrowser.open(f"http://localhost:{port}")

        threading.Thread(target=_open_browser, daemon=True).start()

    print(f"DeckDrop running at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
