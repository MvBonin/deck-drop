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

    games = [_game_info(g, tracker) for g in s.library.all() if not game_id or g.id == game_id]

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
        "downloads": downloads,
    }
