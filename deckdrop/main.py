"""Entry point: CLI + server startup."""

from __future__ import annotations

import argparse


def _should_schedule_prepare(game: object, cfg: object, tracker: object) -> bool:
    """True when startup should build a torrent for this game.

    Modified games stay unshareable until the user publishes an update.
    Preparing a torrent for them would hash and share the patched files.
    """
    if getattr(game, "origin", None) and (
        game.origin.peer_id or game.origin.peer_name  # type: ignore[attr-defined]
    ):
        return False
    if tracker.state(game.id) == "modified":  # type: ignore[attr-defined]
        return False
    torrent = getattr(game, "torrent", None)
    if torrent is not None and torrent.magnet:
        return False
    from deckdrop.core import torrent_prep

    return not torrent_prep.has_cached_torrent(cfg, game.id)  # type: ignore[attr-defined]


def _baseline_all_then_scan(library: object, content_tracker: object) -> None:
    """Baseline every game, then kick off a scan pass – run in a background thread.

    Baseline hashing (full blake2b of every file for a game never hashed
    before) can take a long time on a large library / slow SD card. Running
    it synchronously in the uvicorn lifespan would delay the server accepting
    any request until it's done. Each game stays in state "hashing" (not
    shareable) via ContentTracker.ensure_baseline until its own pass
    completes, so nothing is served half-hashed in the meantime.
    """
    for g in library.all():  # type: ignore[attr-defined]
        content_tracker.ensure_baseline(g.id)  # type: ignore[attr-defined]
    content_tracker.scan_all_async()  # type: ignore[attr-defined]


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
    parser.add_argument(
        "--install",
        action="store_true",
        help="Copy this AppImage to ~/Applications/DeckDrop.AppImage, add menu entries "
        "(normal + Gaming Mode) and exit",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove the background service and the menu entries, then exit",
    )
    parser.add_argument(
        "--service",
        choices=("on", "off", "start", "stop", "status"),
        help="Background service (systemd user unit): on = run at every boot, off = remove",
    )
    args = parser.parse_args()

    if args.install or args.uninstall or args.service:
        raise SystemExit(_manage(args))

    _run(
        headless=args.headless or args.kiosk,
        kiosk=args.kiosk,
        host=args.host,
        port_override=args.port,
    )


def _manage(args: argparse.Namespace) -> int:
    """Non-interactive setup commands (used by the Decky plugin and by hand)."""
    import json
    import os

    from deckdrop.core import config as cfg_mod
    from deckdrop.core import install as install_mod
    from deckdrop.core import service as svc

    cfg = cfg_mod.load()
    rc = 0
    if args.install:
        try:
            target = install_mod.install()
        except (ValueError, OSError) as exc:
            print(f"Install failed: {exc}")
            return 1
        cfg.appimage_path = str(target)
        cfg_mod.save(cfg)
        os.environ["APPIMAGE"] = str(target)
        svc.refresh_appimage_path(str(target))
        print(f"Installed: {target}")
    if args.uninstall:
        svc.disable()
        install_mod.uninstall()
        cfg.autostart = False
        cfg_mod.save(cfg)
        print("Service and menu entries removed. The AppImage file was kept.")
    if args.service == "on":
        appimage = ""
        installed = install_mod.install_path()
        if installed.is_file():
            appimage = str(installed)
        try:
            svc.enable(svc.detect_install_type(appimage or cfg.appimage_path), appimage)
        except ValueError as exc:
            print(f"Service setup failed: {exc}")
            return 1
        cfg.autostart = True
        cfg_mod.save(cfg)
    elif args.service == "off":
        svc.disable()
        cfg.autostart = False
        cfg_mod.save(cfg)
    elif args.service == "start":
        rc = 0 if svc.start() else 1
    elif args.service == "stop":
        rc = 0 if svc.stop() else 1
    if args.service or args.install:
        info = {
            "service_installed": svc.is_installed(),
            "service_enabled": svc.is_enabled(),
            "service_active": svc.is_active(),
            **install_mod.status().to_dict(),
        }
        # Last line of output: machine-readable state (read by the Decky plugin)
        print(json.dumps(info))
    return rc


def _server_status(port: int) -> dict | None:
    """/api/status of a DeckDrop server already listening on `port`, else None."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
            data = json.loads(r.read())
    except Exception:
        return None
    return data if isinstance(data, dict) and data.get("peer_id") else None


def _wait_for_server(port: int, timeout: float) -> dict | None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = _server_status(port)
        if info is not None:
            return info
        time.sleep(0.5)
    return None


def _attach_to_running(port: int, *, kiosk: bool) -> bool:
    """Open the UI of an already running DeckDrop instead of replacing it.

    This is what keeps the background service alive when DeckDrop is started
    from the menu or from Steam (Gaming Mode): previously the new process
    killed the service and Steam then took the server down with the "game".
    Returns False if nothing is running – the caller starts a server.
    """
    from deckdrop import __version__
    from deckdrop.core import service as svc

    info = _server_status(port)
    if info is None:
        return False
    if info.get("version") != __version__:
        if not (svc.is_installed() and svc.is_active()):
            return False  # a manually started older/newer instance: replace it
        # The service runs another version (e.g. this is a newer AppImage):
        # restart it so it picks up the unit's current AppImage path.
        svc.restart()
        if _wait_for_server(port, 30.0) is None:
            return False
    url = f"http://127.0.0.1:{port}"
    print(f"DeckDrop is already running – opening {url}")
    if kiosk:
        from deckdrop.core import kiosk as kiosk_mod

        proc = kiosk_mod.open_window(url)
        if proc is None:
            print(f"No Chromium found. Open {url} manually.")
            return True
        # Steam tracks this process as the "game": stay until the window closes.
        proc.wait()
    else:
        from deckdrop.core.hostenv import open_url

        open_url(url)
    return True


def _run(headless: bool, host: str, port_override: int | None, *, kiosk: bool = False) -> None:
    import atexit
    import os
    import sys
    import threading
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

    # Keep an installed service pointing at the currently running AppImage so a
    # newer AppImage replaces the old (stale) path. No-op for the headless
    # service itself (same path) or non-AppImage installs. An AppImage that
    # was installed to ~/Applications stays the service's target: starting a
    # downloaded copy from elsewhere must not repoint the service at a file
    # that may be deleted later.
    if appimage_env:
        try:
            from deckdrop.core import install as install_mod
            from deckdrop.core import service as svc_mod

            installed = install_mod.install_path()
            if (not installed.is_file() or appimage_env == str(installed)) and (
                svc_mod.refresh_appimage_path(appimage_env)
            ):
                import logging

                logging.getLogger(__name__).info(
                    "Service-Unit auf aktuelle AppImage aktualisiert: %s", appimage_env
                )
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("Service-Unit-Refresh fehlgeschlagen: %s", exc)

    from deckdrop.core import service as svc_mod

    if not svc_mod.running_as_service() and (not headless or kiosk):
        # UI launches (menu, Steam shortcut) reuse a running server.
        if os.environ.get("DECKDROP_RESTARTED") != "1" and _attach_to_running(port, kiosk=kiosk):
            return

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

    from deckdrop.core.content_tracker import ContentTracker

    content_tracker = ContentTracker(cfg, library)

    app_state.init(cfg, library, peer_registry, transfer, content_tracker)

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
        from deckdrop.core import debuglog, torrent_prep

        torrent_prep.bind_loop(loop)
        content_tracker.bind_loop(loop)
        debuglog.bind_loop(loop)
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

            logging.getLogger(__name__).info("Migrated stale torrent caches: %d game(s)", migrated)

        restored = torrent_prep.restore_all_cached(library, cfg, transfer)
        if restored:
            import logging

            logging.getLogger(__name__).info("Torrent cache restored for %d game(s)", restored)
        for g in library.all():
            if _should_schedule_prepare(g, cfg, content_tracker):
                torrent_prep.schedule_prepare(g.id)

        threading.Thread(
            target=_baseline_all_then_scan,
            args=(library, content_tracker),
            daemon=True,
            name="content-baseline-startup",
        ).start()
        content_tracker.start_periodic()
        yield
        # Shutdown – transfers first: writing fast-resume data must happen
        # before the single-instance grace period runs out.
        peer_registry.stop_refresh_loop()
        if transfer:
            transfer.shutdown()
        await discovery.stop()

    app = create_app(lifespan=lifespan)

    # After a self-update restart the old browser/kiosk window is still open
    # and reloads itself – don't open a second one.
    restarted = os.environ.pop("DECKDROP_RESTARTED", "") == "1"

    if restarted:
        pass
    elif not headless:
        import threading
        import time

        from deckdrop.core.hostenv import open_url

        def _open_browser() -> None:
            time.sleep(1.2)
            open_url(f"http://localhost:{port}")

        threading.Thread(target=_open_browser, daemon=True).start()
    elif kiosk:
        import signal
        import threading
        import time

        from deckdrop.core import kiosk as kiosk_mod

        url = f"http://127.0.0.1:{port}"

        def _open_kiosk() -> None:
            time.sleep(1.2)
            proc = kiosk_mod.open_window(url)
            if proc is None:
                print(f"No Chromium found. Open {url} manually.")
                return
            proc.wait()
            # Window closed: in Gaming Mode Steam waits for this process to
            # end before it considers the "game" closed. Background sharing
            # is what the service is for.
            print("Kiosk window closed – stopping DeckDrop")
            os.kill(os.getpid(), signal.SIGINT)

        threading.Thread(target=_open_kiosk, daemon=True).start()

    print(f"DeckDrop running at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")

    from deckdrop.core import selfupdate

    if selfupdate.restart_requested():
        remove_pid_file(cfg_mod.CONFIG_PATH)
        print("DeckDrop is restarting…")
        selfupdate.exec_restart(sys.argv)


if __name__ == "__main__":
    main()
