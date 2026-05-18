"""
.torrent generation and libtorrent session factory.

libtorrent is an optional dependency. All public functions raise
RuntimeError with a clear message if it's not installed.
"""

from __future__ import annotations

from pathlib import Path

_LT_MISSING = "libtorrent is not installed. Install it with: pip install libtorrent"


def _lt():
    try:
        import libtorrent as lt

        return lt
    except ImportError:
        raise RuntimeError(_LT_MISSING)


# LAN-only session settings – hardcoded, not user-configurable
_LAN_SETTINGS = {
    "enable_dht": False,
    "enable_lsd": True,  # Local Service Discovery via LAN multicast
    "enable_upnp": False,
    "enable_natpmp": False,
    "announce_to_all_trackers": False,
    "announce_to_all_tiers": False,
}


def create_torrent_data(game_path: Path) -> bytes:
    """Create a .torrent file (as bytes) from a game directory."""
    lt = _lt()
    fs = lt.file_storage()
    lt.add_files(fs, str(game_path))
    t = lt.create_torrent(fs)
    t.set_comment(f"DeckDrop – {game_path.name}")
    lt.set_piece_hashes(t, str(game_path.parent))
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
    settings = dict(_LAN_SETTINGS)
    settings["listen_interfaces"] = f"0.0.0.0:{torrent_port}"
    return lt.session(settings)
