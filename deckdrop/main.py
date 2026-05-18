"""Entry point: CLI + server startup."""

from __future__ import annotations

import argparse
import sys


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
    import uvicorn

    from deckdrop.core import config as cfg_mod
    from deckdrop.core.library import Library
    from deckdrop.api import state as app_state
    from deckdrop.api.server import create_app

    cfg = cfg_mod.load()
    library = Library()
    library.reload(cfg)

    app_state.init(cfg, library)

    port = port_override or cfg.port

    # Ensure directories exist
    cfg.download_dir.mkdir(parents=True, exist_ok=True)
    cfg.torrent_cache.mkdir(parents=True, exist_ok=True)

    if not headless:
        import threading
        import webbrowser
        import time

        def _open_browser() -> None:
            time.sleep(1.2)
            webbrowser.open(f"http://localhost:{port}")

        threading.Thread(target=_open_browser, daemon=True).start()

    app = create_app()

    print(f"DeckDrop running at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
