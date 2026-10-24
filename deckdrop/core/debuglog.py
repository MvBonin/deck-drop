"""In-memory record of *why* DeckDrop hashes or re-checks files.

Every place that reads game files to hash or verify them calls `record()` with
a machine-readable reason. The last events are kept in a ring buffer (not
persisted) and exposed via GET /api/debug for the frontend debug mode
(`?debug=1`). Each event is also logged at INFO level.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from collections.abc import Iterable
from typing import Any

from deckdrop.core.i18n import t

log = logging.getLogger(__name__)

MAX_EVENTS = 300
MAX_FILES_PER_EVENT = 50

# Human-readable texts for `kind` (what reads the files) and `reason` (why)
# live in core.i18n as debug.kind.<kind> / debug.reason.<reason>.


def kind_text(kind: str) -> str:
    key = f"debug.kind.{kind}"
    text = t(key)
    return kind if text == key else text


_lock = threading.Lock()
_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
_seq = 0
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Enable live `debug_hash_event` WebSocket broadcasts (called from the lifespan)."""
    global _loop
    _loop = loop


def reason_text(reason: str) -> str:
    key = f"debug.reason.{reason}"
    text = t(key)
    return reason if text == key else text


def record(
    kind: str,
    reason: str,
    game_id: str = "",
    files: Iterable[str] = (),
    detail: str = "",
) -> dict[str, Any]:
    """Store one hash/recheck event. Never raises."""
    global _seq
    file_list = list(files)
    with _lock:
        _seq += 1
        event = {
            "id": _seq,
            "ts": time.time(),
            "kind": kind,
            "kind_text": kind_text(kind),
            "reason": reason,
            "reason_text": reason_text(reason),
            "game_id": game_id,
            "file_count": len(file_list),
            "files": file_list[:MAX_FILES_PER_EVENT],
            "detail": detail,
        }
        _events.append(event)
    log.info(
        "[hash] %s/%s game=%s files=%d%s",
        kind,
        reason,
        game_id or "-",
        len(file_list),
        f" ({detail})" if detail else "",
    )
    _broadcast(event)
    return event


def events(game_id: str | None = None) -> list[dict[str, Any]]:
    """Newest first, optionally filtered by game id."""
    with _lock:
        items = list(_events)
    if game_id:
        items = [e for e in items if e["game_id"] == game_id]
    items.reverse()
    return [
        {**e, "kind_text": kind_text(e["kind"]), "reason_text": reason_text(e["reason"])}
        for e in items
    ]


def clear() -> None:
    with _lock:
        _events.clear()


def _broadcast(event: dict[str, Any]) -> None:
    loop = _loop
    if loop is None or loop.is_closed():
        return
    try:
        from deckdrop.api.websocket import broadcast

        asyncio.run_coroutine_threadsafe(broadcast("debug_hash_event", event), loop)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("Could not emit debug_hash_event: %s", exc)
