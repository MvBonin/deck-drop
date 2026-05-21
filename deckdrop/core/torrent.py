"""
.torrent generation and libtorrent session factory.

libtorrent is an optional dependency. All public functions raise
RuntimeError with a clear message if it's not installed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

from deckdrop.core.integrity import iter_torrent_files

_LT_MISSING = "libtorrent is not installed. Install it with: pip install libtorrent"


def _lt():
    try:
        import libtorrent as lt

        return lt
    except ImportError:
        raise RuntimeError(_LT_MISSING)


# LAN-only session settings – hardcoded, not user-configurable
_LAN_SETTINGS_CORE = {
    "enable_dht": False,
    "enable_lsd": True,  # Local Service Discovery via LAN multicast
    "enable_upnp": False,
    "enable_natpmp": False,
    "announce_to_all_trackers": False,
    "announce_to_all_tiers": False,
}
# Optional tuning; names differ across libtorrent builds (e.g. AppImage vs pip).
_LAN_SETTINGS_OPTIONAL = {
    "allow_multiple_connections_per_ip": True,
    "unchoke_slots_limit": 16,
}


def create_torrent_data(
    game_path: Path,
    on_progress: Callable[[float], None] | None = None,
) -> bytes:
    """Create a .torrent file (as bytes) from a game directory."""
    lt = _lt()
    if on_progress:
        on_progress(0.02)

    files = iter_torrent_files(game_path)
    if not files:
        raise RuntimeError(f"No shareable files in {game_path}")

    parent = game_path.parent
    fs = lt.file_storage()
    for file_path in files:
        rel = file_path.relative_to(parent).as_posix()
        fs.add_file(rel, file_path.stat().st_size)
    t = lt.create_torrent(fs)
    t.set_comment(f"DeckDrop – {game_path.name}")

    num_pieces = max(int(t.num_pieces()), 1)

    def _piece_progress(piece_index: int) -> None:
        if on_progress:
            # Hashing dominates runtime; map to 5–95 %
            frac = min(1.0, (int(piece_index) + 1) / num_pieces)
            on_progress(0.05 + 0.9 * frac)

    try:
        lt.set_piece_hashes(t, str(parent), _piece_progress)  # type: ignore[misc]
    except TypeError:
        lt.set_piece_hashes(t, str(parent))  # type: ignore[misc]
        if on_progress:
            on_progress(0.5)

    if on_progress:
        on_progress(0.98)
    return lt.bencode(t.generate())


def make_magnet(torrent_data: bytes) -> tuple[str, str]:
    """
    Parse torrent bytes and return (magnet_uri, info_hash_hex).
    No trackers are added – LAN-only via LSD.
    """
    lt = _lt()
    info = lt.torrent_info(lt.bdecode(torrent_data))
    info_hash = str(info.info_hashes().v1)
    magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={info.name()}"
    return magnet, info_hash


def lan_session(torrent_port: int) -> object:
    """Return a new libtorrent session configured for LAN-only operation."""
    lt = _lt()
    listen = f"0.0.0.0:{torrent_port}"
    settings = dict(_LAN_SETTINGS_CORE)
    settings.update(_LAN_SETTINGS_OPTIONAL)
    settings["listen_interfaces"] = listen
    try:
        return lt.session(settings)
    except (KeyError, TypeError) as exc:
        if "unknown name" not in str(exc).lower():
            raise
        log.warning("Some libtorrent session settings unsupported (%s), using core set", exc)
        core = dict(_LAN_SETTINGS_CORE)
        core["listen_interfaces"] = listen
        return lt.session(core)
