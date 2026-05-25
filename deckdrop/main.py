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
        "--kiosk",
        action="store_true",
        help="Headless server + fullscreen Chromium app window (Steam Deck Gaming Mode)",
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

    _run(
        headless=args.headless or args.kiosk,
        kiosk=args.kiosk,
        host=args.host,
        port_override=args.port,
    )


def _run(headless: bool, host: str, port_override: int | None, *, kiosk: bool = False) -> None:
    import atexit
    import os
    from contextlib import asynccontextmanager

    import uvicorn
    from fastapi import FastAPI

    from deckdrop.api import state as app_state
    from deckdrop.api.server import create_app
    from deckdrop.core import config as cfg_mod
    from deckdrop.core.library import Library
    from deckdrop.network.discovery import DiscoveryService
    from deckdrop.network.peer_registry import PeerRegistry
    from deckdrop.single_instance import remove_pid_file, stop_other_instances, write_pid_file

    cfg = cfg_mod.load()
    port = port_override or cfg.port

    # Persist AppImage path so the service manager can use it later
    appimage_env = os.getenv("APPIMAGE")
    if appimage_env and cfg.appimage_path != appimage_env:
        cfg.appimage_path = appimage_env
        cfg_mod.save(cfg)

    stopped = stop_other_instances(port, config_path=cfg_mod.CONFIG_PATH)
    if stopped:
        pids = ", ".join(str(p) for p in stopped)
        print(f"Vorherige DeckDrop-Instanz beendet (PID {pids})")

    write_pid_file(cfg_mod.CONFIG_PATH)
    atexit.register(remove_pid_file, cfg_mod.CONFIG_PATH)
    library = Library()
    peer_registry = PeerRegistry()
    peer_registry.set_library(library)

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

    exclude = transfer.incomplete_download_dest_paths() if transfer is not None else frozenset()
    library.reload(cfg, exclude_paths=exclude)

    # Ensure directories exist
    cfg.download_dir.mkdir(parents=True, exist_ok=True)
    cfg.torrent_cache.mkdir(parents=True, exist_ok=True)

    discovery = DiscoveryService()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio

        loop = asyncio.get_running_loop()
        peer_registry.bind_loop(loop)
        from deckdrop.core import torrent_prep

        torrent_prep.bind_loop(loop)
        # Startup
        try:
            await discovery.start(cfg, peer_registry.upsert_sync, peer_registry.remove)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("mDNS discovery unavailable: %s", exc)
        peer_registry.start_refresh_loop()
        if transfer:
            transfer.start_polling()
            transfer.seed_all_shared(library, cfg)
            restored = transfer.restore_active_downloads()
            if restored:
                import logging

                logging.getLogger(__name__).info(
                    "Unterbrochene Downloads fortgesetzt: %d", restored
                )

        migrated = torrent_prep.migrate_stale_caches(library, cfg, transfer)
        if migrated:
            import logging

            logging.getLogger(__name__).info(
                "Veraltete Torrent-Caches migriert: %d Spiel(e)", migrated
            )

        restored = torrent_prep.restore_all_cached(library, cfg, transfer)
        if restored:
            import logging

            logging.getLogger(__name__).info("Torrent cache restored for %d game(s)", restored)
        for g in library.all():
            if g.origin.peer_id or g.origin.peer_name:
                continue
            if not g.torrent.magnet and not torrent_prep.has_cached_torrent(cfg, g.id):
                torrent_prep.schedule_prepare(g.id)
        yield
        # Shutdown
        peer_registry.stop_refresh_loop()
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
    elif kiosk:
        import shutil
        import subprocess
        import threading
        import time

        url = f"http://127.0.0.1:{port}"

        def _open_kiosk() -> None:
            time.sleep(1.2)
            for candidate in ("chromium", "google-chrome-stable", "google-chrome", "brave-browser"):
                if shutil.which(candidate):
                    subprocess.Popen(
                        [
                            candidate,
                            f"--app={url}",
                            "--window-size=1280,800",
                            "--disable-features=TranslateUI",
                        ],
                        start_new_session=True,
                    )
                    return
            print(f"No Chromium found. Open {url} manually.")

        threading.Thread(target=_open_kiosk, daemon=True).start()

    print(f"DeckDrop running at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
