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

log = logging.getLogger(__name__)

MAX_EVENTS = 300
MAX_FILES_PER_EVENT = 50

# kind: what reads the files
KIND_TEXT: dict[str, str] = {
    "blake2b": "DeckDrop hasht Dateien (blake2b)",
    "piece_check": "Lokale Pieces prüfen (SHA1)",
    "recheck": "libtorrent prüft Dateien neu",
    "manifest_check": "Manifest ↔ Torrent abgleichen (ohne Lesen)",
    "torrent_build": "Torrent erstellen (liest alle Dateien)",
    "relink": "Spiel-ID an den Host angeglichen (Legacy-Download)",
}

# reason: why it happens
REASON_TEXT: dict[str, str] = {
    "new_local_game": "Neues lokales Spiel – noch keine Datei-Hashes",
    "download_without_manifest": (
        "Download ohne Manifest (alter Peer oder Manifest-Abruf fehlgeschlagen)"
    ),
    "manifest_mismatch": "Manifest passt nicht zum Torrent – Hashes werden lokal neu berechnet",
    "manifest_ok": "Manifest passt zum Torrent – kein Rehash nötig",
    "mtime_changed": "Änderungszeit geändert, Größe gleich – Inhalt wird geprüft",
    "publish": "Update veröffentlichen – geänderte Dateien werden gehasht",
    "update_reuse_local": (
        "Update: vorhandene Bytes geänderter/verschobener Dateien wiederverwenden"
    ),
    "update_without_baseline": (
        "Update ohne alte Datei-Hashes – vorhandene Dateien werden einmal pieceweise geprüft"
    ),
    "update_started": "Nachgeholt: Hash war für ein Update abgebrochen worden",
    "interrupted": "Nachgeholt: Hash wurde durch Beenden/Neustart unterbrochen",
    "relinked": "Legacy-ID korrigiert – Prüfung läuft unter der neuen ID weiter",
    "stall": "Download hängt – einmalige Neuprüfung",
    "torrent_rebuilt": "Torrent neu erstellt – Seed prüft Dateien neu",
    "torrent_missing": "Kein Torrent vorhanden – wird aus den Dateien erstellt",
    "torrent_forced": "Torrent wird neu erstellt (Inhalt oder Metadaten geändert)",
    "unknown": "Unbekannt",
}

_lock = threading.Lock()
_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
_seq = 0
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Enable live `debug_hash_event` WebSocket broadcasts (called from the lifespan)."""
    global _loop
    _loop = loop


def reason_text(reason: str) -> str:
    return REASON_TEXT.get(reason, reason)


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
            "kind_text": KIND_TEXT.get(kind, kind),
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
    return items


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
