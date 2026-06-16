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

_CONTENT_TYPE_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
}


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


def _filename_for_content_type(content_type: str) -> str:
    key = (content_type or "").split(";", 1)[0].strip().lower()
    ext = _CONTENT_TYPE_EXT.get(key)
    if not ext:
        return DEFAULT_COVER_FILENAME
    return f"deckdrop.{ext}"


def _save_cover_bytes(game_path: Path, content: bytes, content_type: str) -> bool:
    clear_covers(game_path)
    dest = game_path / _filename_for_content_type(content_type)
    try:
        dest.write_bytes(content)
    except OSError as exc:
        log.warning("Could not write cover to %s: %s", dest, exc)
        return False
    return True


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

    if not _save_cover_bytes(game_path, r.content, "image/jpeg"):
        return False

    log.info("Saved Steam cover for app_id=%d → %s", app_id, game_path)
    return True


def download_cover_from_url(game_path: Path, url: str, *, timeout: float = 8.0) -> bool:
    """
    Fetch a cover image from an arbitrary URL (e.g. a peer host) and store it
    locally in the game folder. The file extension follows the response's
    content-type. Returns True on success.
    """
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(url)
            if r.status_code != 200 or not r.content:
                log.info("Cover not available at %s (HTTP %s)", url, r.status_code)
                return False
            content_type = (r.headers.get("content-type") or "").lower()
            if "image" not in content_type and len(r.content) < 256:
                log.info("Cover response from %s is not an image", url)
                return False
    except Exception as exc:
        log.warning("Cover download from %s failed: %s", url, exc)
        return False

    if not _save_cover_bytes(game_path, r.content, content_type):
        return False

    log.info("Saved cover from %s → %s", url, game_path)
    return True
