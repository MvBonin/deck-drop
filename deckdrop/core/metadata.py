"""Online game metadata lookup (Steam store) for "Add game" / "Edit game".

Only the game *name* the user typed (search) or the chosen Steam app id
(details) leave the device – the same requests the cover lookup already
makes. Nothing is sent automatically: the UI asks for it.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import asdict, dataclass, field

import httpx

log = logging.getLogger(__name__)

STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
STEAM_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
STEAM_CAPSULE_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/capsule_231x87.jpg"
STEAM_LIBRARY_COVER_URL = (
    "https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/library_600x900.jpg"
)

_MAX_RESULTS = 12
_MAX_DESCRIPTION = 600
_TAG_RE = re.compile(r"<[^>]+>")


class MetadataError(RuntimeError):
    """The metadata source could not be reached or answered garbage."""


@dataclass
class SearchResult:
    steam_app_id: int
    name: str
    thumb_url: str
    cover_url: str
    platforms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GameDetails:
    steam_app_id: int
    name: str
    description: str = ""
    developers: list[str] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)
    release_date: str = ""
    platforms: list[str] = field(default_factory=list)
    # "linux" | "windows" | "any" – what DeckDrop's platform field should say
    suggested_platform: str = "any"
    cover_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _platform_list(raw: object) -> list[str]:
    if not isinstance(raw, dict):
        return []
    return [p for p in ("windows", "linux", "mac") if raw.get(p)]


def suggest_platform(platforms: list[str]) -> str:
    """Steam platforms → DeckDrop platform.

    A game that also ships for Linux is most likely shared as the native
    build; Windows-only is a Proton game. Everything else stays "any".
    """
    if "linux" in platforms:
        return "linux"
    if platforms == ["windows"]:
        return "windows"
    return "any"


def _plain_text(value: object) -> str:
    text = html.unescape(_TAG_RE.sub(" ", str(value or "")))
    text = " ".join(text.split())
    if len(text) > _MAX_DESCRIPTION:
        text = text[: _MAX_DESCRIPTION - 1].rstrip() + "…"
    return text


def parse_search(payload: object) -> list[SearchResult]:
    """Steam storesearch JSON → results (apps only, deduplicated)."""
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    out: list[SearchResult] = []
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type", "app") != "app":
            continue
        try:
            app_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        name = str(item.get("name") or "").strip()
        if app_id <= 0 or not name or app_id in seen:
            continue
        seen.add(app_id)
        out.append(
            SearchResult(
                steam_app_id=app_id,
                name=name,
                thumb_url=str(item.get("tiny_image") or STEAM_CAPSULE_URL.format(app_id=app_id)),
                cover_url=STEAM_LIBRARY_COVER_URL.format(app_id=app_id),
                platforms=_platform_list(item.get("platforms")),
            )
        )
        if len(out) >= _MAX_RESULTS:
            break
    return out


def parse_details(app_id: int, payload: object) -> GameDetails | None:
    """Steam appdetails JSON → details, None if Steam doesn't know the app."""
    entry = payload.get(str(app_id)) if isinstance(payload, dict) else None
    if not isinstance(entry, dict) or not entry.get("success"):
        return None
    data = entry.get("data")
    if not isinstance(data, dict):
        return None
    platforms = _platform_list(data.get("platforms"))
    release = data.get("release_date")
    release_date = str(release.get("date") or "") if isinstance(release, dict) else ""
    return GameDetails(
        steam_app_id=app_id,
        name=str(data.get("name") or "").strip(),
        description=_plain_text(data.get("short_description")),
        developers=[str(d) for d in data.get("developers") or [] if d],
        publishers=[str(p) for p in data.get("publishers") or [] if p],
        release_date=release_date,
        platforms=platforms,
        suggested_platform=suggest_platform(platforms),
        cover_url=STEAM_LIBRARY_COVER_URL.format(app_id=app_id),
    )


async def search(query: str, *, lang: str = "english", timeout: float = 8.0) -> list[SearchResult]:
    query = query.strip()
    if not query:
        return []
    params = {"term": query, "l": lang, "cc": "US"}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(STEAM_SEARCH_URL, params=params)
            r.raise_for_status()
            payload = r.json()
    except Exception as exc:
        log.warning("Steam metadata search failed for %r: %s", query, exc)
        raise MetadataError(str(exc)) from exc
    return parse_search(payload)


async def details(
    app_id: int, *, lang: str = "english", timeout: float = 8.0
) -> GameDetails | None:
    if app_id <= 0:
        return None
    params = {"appids": str(app_id), "l": lang}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(STEAM_DETAILS_URL, params=params)
            r.raise_for_status()
            payload = r.json()
    except Exception as exc:
        log.warning("Steam details lookup failed for app_id=%d: %s", app_id, exc)
        raise MetadataError(str(exc)) from exc
    return parse_details(app_id, payload)


def steam_language(ui_language: str) -> str:
    """DeckDrop UI language code → Steam store `l` parameter."""
    return {"de": "german", "en": "english"}.get((ui_language or "").lower(), "english")
