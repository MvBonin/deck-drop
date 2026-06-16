"""Local game cover files (excluded from torrents)."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

COVER_FILENAMES = ("deckdrop.png", "deckdrop.jpg", "deckdrop.jpeg", "deckdrop.webp")
DEFAULT_COVER_FILENAME = "deckdrop.jpg"

STEAM_LIBRARY_COVER_URL = (
    "https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/library_600x900.jpg"
)


def clear_covers(game_path: Path) -> None:
    """Remove existing DeckDrop cover files in the game folder."""
    for name in COVER_FILENAMES:
        p = game_path / name
        try:
            p.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Could not remove cover %s: %s", p, exc)


def has_local_cover(game_path: Path) -> bool:
    return any((game_path / name).is_file() for name in COVER_FILENAMES)


def download_steam_cover(game_path: Path, app_id: int, *, timeout: float = 12.0) -> bool:
    """
    Fetch Steam library art and save as deckdrop.jpg in the game folder.

    Returns True on success. Does not touch torrent state (covers are metadata).
    """
    if app_id <= 0:
        return False

    url = STEAM_LIBRARY_COVER_URL.format(app_id=app_id)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(url)
            if r.status_code != 200 or not r.content:
                log.info("Steam cover not available for app_id=%d (HTTP %s)", app_id, r.status_code)
                return False
            content_type = (r.headers.get("content-type") or "").lower()
            if "image" not in content_type and len(r.content) < 256:
                log.info("Steam cover response for app_id=%d is not an image", app_id)
                return False
    except Exception as exc:
        log.warning("Steam cover download failed for app_id=%d: %s", app_id, exc)
        return False

    clear_covers(game_path)
    dest = game_path / DEFAULT_COVER_FILENAME
    try:
        dest.write_bytes(r.content)
    except OSError as exc:
        log.warning("Could not write cover to %s: %s", dest, exc)
        return False

    log.info("Saved Steam cover for app_id=%d → %s", app_id, dest)
    return True
