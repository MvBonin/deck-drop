"""GET /api/debug – diagnostics for the frontend debug mode (`?debug=1`).

Read-only: why files were hashed/re-checked (deckdrop.core.debuglog), the
content tracker state per game and the active downloads.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from fastapi import APIRouter, Depends

from deckdrop import __version__
from deckdrop.api import state as app_state
from deckdrop.api.deps import local_only
from deckdrop.core import debuglog

log = logging.getLogger(__name__)

router = APIRouter(tags=["debug"], dependencies=[Depends(local_only)])

_ENTRY_KEYS = (
    "state",
    "summary",
    "changed",
    "removed",
    "added",
    "last_scan",
    "hash_progress",
    "pending_hash_reason",
)


def _norm(name: Any) -> str:
    return " ".join(str(name or "").split()).casefold()


def _network_rows(registry: Any) -> list[dict[str, Any]]:
    """One row per (game, version) offered by online peers."""
    rows: list[dict[str, Any]] = []
    try:
        network_games = registry.all_network_games()
    except Exception as exc:
        log.debug("No network games for debug view: %s", exc)
        return rows
    for ng in network_games:
        for v in ng.get("versions") or []:
            rows.append(
                {
                    "id": ng.get("id"),
                    "name": ng.get("name"),
                    "installed": bool(ng.get("installed")),
                    "update_available": bool(ng.get("update_available")),
                    "version_key": v.get("version_key"),
                    "revision": v.get("revision"),
                    "version_label": v.get("version_label"),
                    "shareable": v.get("shareable"),
                    "has_torrent": v.get("has_torrent"),
                    "size_bytes": v.get("size_bytes"),
                    "peers": [p.get("peer_name") for p in v.get("peers") or []],
                }
            )
    return rows


def _network_match(g: Any, rows: list[dict[str, Any]], registry: Any) -> dict[str, Any]:
    """How the network sees this local game – by ID, and by name only.

    Updates are matched by game ID alone; a same-named game with another ID
    is shown as a fresh download instead of an update (legacy ID bug).
    """
    same_id = [r for r in rows if r["id"] == g.id]
    name_matches = sorted(
        {r["id"] for r in rows if r["id"] != g.id and _norm(r["name"]) == _norm(g.name)}
    )
    best = None
    try:
        b = registry.best_update_for(g.id, g.content.revision)
        if b:
            best = {"revision": b.get("revision"), "version_label": b.get("version_label")}
    except Exception as exc:
        log.debug("best_update_for failed for %s: %s", g.id, exc)
    return {"same_id": len(same_id), "best_update": best, "name_matches": name_matches}


def _game_info(g: Any, tracker: Any) -> dict[str, Any]:
    entry = tracker.get_entry(g.id) if tracker is not None else {}
    info = {k: entry.get(k) for k in _ENTRY_KEYS if k in entry}
    info.setdefault("state", "clean")
    pending = info.get("pending_hash_reason")
    if pending:
        info["pending_hash_reason_text"] = debuglog.reason_text(pending)
    info["has_pending_update"] = bool(entry.get("pending_update"))
    return {
        "id": g.id,
        "name": g.name,
        "path": str(g.path),
        "origin_peer": g.origin.peer_name or None,
        "origin_peer_id": g.origin.peer_id or None,
        "revision": g.content.revision,
        "version_label": g.content.version_label,
        "content_hash": g.content.content_hash,
        "info_hash": g.torrent.info_hash,
        "file_count": len(g.files),
        "has_file_hashes": bool(g.files),
        "content": info,
    }


@router.get("/debug")
def get_debug(game_id: str | None = None) -> dict[str, Any]:
    s = app_state.get()
    tracker = None
    try:
        tracker = s.get_content_tracker()
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("No content tracker for debug view: %s", exc)

    network = _network_rows(s.peer_registry)
    games = []
    for g in s.library.all():
        if game_id and g.id != game_id:
            continue
        info = _game_info(g, tracker)
        info["network"] = _network_match(g, network, s.peer_registry)
        games.append(info)

    downloads: list[dict[str, Any]] = []
    if s.transfer is not None and hasattr(s.transfer, "all_statuses"):
        try:
            for st in s.transfer.all_statuses():
                if dataclasses.is_dataclass(st):
                    d = dataclasses.asdict(st)
                    if not game_id or d.get("game_id") == game_id:
                        downloads.append(d)
        except Exception as exc:
            log.debug("Could not list downloads for debug view: %s", exc)

    return {
        "version": __version__,
        "peer_id": s.cfg.peer_id,
        "hash_events": debuglog.events(game_id),
        "games": games,
        "network_games": network,
        "downloads": downloads,
    }
