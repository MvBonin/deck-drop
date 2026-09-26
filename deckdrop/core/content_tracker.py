"""
Per-game content tracker: detects local changes to shared game folders and
enforces "verändert → nicht teilbar, bis veröffentlicht" (see
docs/plans/game-updates.md Phase 2/3).

State is persisted outside the game folder (Config.content_state_dir), one
JSON file per game id. Follows the thread + `_emit` pattern already used by
`deckdrop.core.torrent_prep` (bind_loop from the uvicorn lifespan, daemon
threads for slow I/O, WebSocket events for progress).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deckdrop.core import content, integrity
from deckdrop.core import game as game_mod

log = logging.getLogger(__name__)

_BUSY_STATES = ("hashing", "publishing", "updating")
_EMPTY_SUMMARY: dict[str, int] = {"changed": 0, "removed": 0, "added": 0}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ContentTracker:
    def __init__(self, cfg: object, library: object) -> None:
        self._cfg = cfg
        self._library = library
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._scanning = False
        self._publishing: set[str] = set()
        self._periodic_task: asyncio.Task | None = None
        self._entries: dict[str, dict[str, Any]] = {}
        self._load_all()

    # -- lifecycle --

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def start_periodic(self) -> None:
        """Start the asyncio background scan loop (call once, from the lifespan)."""
        if self._periodic_task is not None and not self._periodic_task.done():
            return
        self._periodic_task = asyncio.create_task(self._periodic_loop())

    async def _periodic_loop(self) -> None:
        while True:
            interval = getattr(self._cfg, "content_scan_interval", 300) or 300
            await asyncio.sleep(interval)
            try:
                self.scan_all_async()
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("Periodic content scan failed to start: %s", exc)

    # -- persistence --

    def _state_path(self, game_id: str) -> Path:
        return Path(self._cfg.content_state_dir) / f"{game_id}.json"

    def _load_all(self) -> None:
        d = Path(self._cfg.content_state_dir)
        if not d.is_dir():
            return
        for p in d.glob("*.json"):
            try:
                self._entries[p.stem] = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue

    def _save_entry(self, game_id: str, entry: dict[str, Any]) -> None:
        path = self._state_path(game_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(entry), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.warning("Could not persist content state for %s: %s", game_id, exc)

    # -- read accessors --

    def state(self, game_id: str) -> str:
        return self._entries.get(game_id, {}).get("state", "clean")

    def summary(self, game_id: str) -> dict[str, int]:
        return dict(self._entries.get(game_id, {}).get("summary") or _EMPTY_SUMMARY)

    def is_shareable(self, game_id: str) -> bool:
        return self.state(game_id) == "clean"

    def get_entry(self, game_id: str) -> dict[str, Any]:
        return dict(self._entries.get(game_id, {}))

    def change_lists(self, game_id: str) -> dict[str, list[str]]:
        e = self._entries.get(game_id, {})
        return {
            "changed": list(e.get("changed") or []),
            "removed": list(e.get("removed") or []),
            "added": list(e.get("added") or []),
        }

    # -- writes --

    def set_state(self, game_id: str, state: str, **extra: Any) -> None:
        entry = dict(self._entries.get(game_id, {}))
        entry["state"] = state
        entry["last_scan"] = time.time()
        for k, v in extra.items():
            entry[k] = v
        self._entries[game_id] = entry
        self._save_entry(game_id, entry)
        self._emit(
            "game_content_state",
            {"game_id": game_id, "state": state, "summary": entry.get("summary", _EMPTY_SUMMARY)},
        )

    # -- baseline --

    def ensure_baseline(self, game_id: str) -> None:
        """Fill files/sizes/content_hash for a never-hashed game, or snapshot an existing one.

        Replaces the old games.py::_hash_game_files. Does NOT call
        torrent_prep.invalidate_torrent on first fill (that would rebuild the
        torrent that was just prepared).
        """
        g = self._library.get(game_id)
        if not g:
            return

        if not g.files:
            self.set_state(game_id, "hashing")
            try:
                self._hash_full(g)
            except Exception as exc:
                log.error("Baseline hashing failed for %s: %s", game_id, exc)
                self.set_state(game_id, "modified")
                return
        elif not g.sizes:
            snap = content.take_snapshot(g.path, g.files.keys())
            g.sizes = {rel: snap[rel][0] for rel in snap if rel in g.files}
            game_mod.save(g)

        snapshot = content.take_snapshot(g.path, g.files.keys())
        self.set_state(
            game_id,
            "clean",
            snapshot=snapshot,
            summary=dict(_EMPTY_SUMMARY),
            changed=[],
            removed=[],
            added=[],
        )

    def _hash_full(self, g: game_mod.GameInfo) -> None:
        rels = content.iter_content_files(g.path, g.content.ignore)
        files: dict[str, str] = {}
        sizes: dict[str, int] = {}
        total = 0
        for rel in rels:
            path = g.path / rel
            try:
                st = path.stat()
            except OSError:
                continue
            files[rel] = integrity.hash_file(path)
            sizes[rel] = st.st_size
            total += st.st_size
        g.files = files
        g.sizes = sizes
        g.size_bytes = total
        if not g.content.content_hash:
            g.content.content_hash = content.compute_content_hash(files, sizes)
        game_mod.save(g)

    # -- scanning --

    def scan(self, game_id: str) -> str:
        """Synchronous scan (stat only, plus a hash for mtime-only changes)."""
        g = self._library.get(game_id)
        if not g:
            return "clean"

        cur_state = self.state(game_id)
        if cur_state in _BUSY_STATES:
            return cur_state

        if not g.files:
            self.ensure_baseline(game_id)
            return self.state(game_id)

        entry = self._entries.get(game_id, {})
        snapshot = dict(entry.get("snapshot") or {})
        result = content.compare_snapshot(g.path, g.files, snapshot, g.content.ignore)

        changed = list(result.changed)
        for rel in result.mtime_only:
            path = g.path / rel
            try:
                new_hash = integrity.hash_file(path)
                st = path.stat()
            except OSError:
                changed.append(rel)
                continue
            if new_hash == g.files.get(rel):
                snapshot[rel] = [st.st_size, st.st_mtime_ns]
            else:
                changed.append(rel)

        for rel in result.changed:
            path = g.path / rel
            try:
                st = path.stat()
                snapshot[rel] = [st.st_size, st.st_mtime_ns]
            except OSError:
                pass

        changed = sorted(set(changed))
        removed = result.removed
        added = result.added

        new_state = "modified" if (changed or removed) else "clean"
        summary = {"changed": len(changed), "removed": len(removed), "added": len(added)}
        self.set_state(
            game_id,
            new_state,
            snapshot=snapshot,
            summary=summary,
            changed=changed,
            removed=removed,
            added=added,
        )

        if new_state == "modified" and cur_state != "modified":
            transfer = self._transfer()
            if transfer is not None:
                transfer.drop_seed(game_id)
        elif new_state == "clean" and cur_state == "modified":
            transfer = self._transfer()
            if transfer is not None:
                cache = Path(self._cfg.torrent_cache) / f"{game_id}.torrent"
                if cache.is_file():
                    transfer.seed_from_cache(game_id, g.path, cache)

        return new_state

    def scan_all_async(self) -> None:
        with self._lock:
            if self._scanning:
                return
            self._scanning = True
        threading.Thread(target=self._scan_all_worker, daemon=True, name="content-scan-all").start()

    def _scan_all_worker(self) -> None:
        try:
            for g in list(self._library.all()):
                try:
                    if g.id not in self._entries:
                        self.ensure_baseline(g.id)
                    else:
                        self.scan(g.id)
                except Exception as exc:
                    log.warning("Content scan failed for %s: %s", g.id, exc)
        finally:
            with self._lock:
                self._scanning = False

    # -- publish (Phase 3) --

    def publish(self, game_id: str, version_label: str, note: str, exclude: list[str]) -> None:
        with self._lock:
            if game_id in self._publishing:
                return
            self._publishing.add(game_id)
        threading.Thread(
            target=self._publish_worker,
            args=(game_id, version_label, note, list(exclude)),
            daemon=True,
            name=f"content-publish-{game_id}",
        ).start()

    def _publish_worker(
        self, game_id: str, version_label: str, note: str, exclude: list[str]
    ) -> None:
        try:
            g = self._library.get(game_id)
            if not g:
                return
            self.set_state(game_id, "publishing")
            transfer = self._transfer()
            if transfer is not None:
                transfer.drop_seed(game_id)

            ignore = list(dict.fromkeys([*g.content.ignore, *exclude]))
            old_snapshot = self._entries.get(game_id, {}).get("snapshot") or {}
            rels = content.iter_content_files(g.path, ignore)

            files: dict[str, str] = {}
            sizes: dict[str, int] = {}
            total = len(rels) or 1
            for i, rel in enumerate(rels):
                path = g.path / rel
                try:
                    st = path.stat()
                except OSError:
                    continue
                old_hash = g.files.get(rel)
                snap = old_snapshot.get(rel)
                if old_hash and snap and snap[0] == st.st_size and snap[1] == st.st_mtime_ns:
                    h = old_hash
                else:
                    h = integrity.hash_file(path)
                files[rel] = h
                sizes[rel] = st.st_size
                self._emit(
                    "content_publish_progress",
                    {"game_id": game_id, "progress": (i + 1) / total},
                )

            new_hash = content.compute_content_hash(files, sizes)
            if new_hash and new_hash == g.content.content_hash and ignore == g.content.ignore:
                snapshot = content.take_snapshot(g.path, files.keys())
                self.set_state(
                    game_id,
                    "clean",
                    snapshot=snapshot,
                    summary=dict(_EMPTY_SUMMARY),
                    changed=[],
                    removed=[],
                    added=[],
                )
                self._emit(
                    "content_publish_complete",
                    {
                        "game_id": game_id,
                        "unchanged": True,
                        "revision": g.content.revision,
                        "version_label": g.content.version_label,
                    },
                )
                return

            prev_revision = g.content.revision
            max_hist_rev = max((h.revision for h in g.history), default=prev_revision)
            new_revision = max(prev_revision, max_hist_rev) + 1
            now = _now()
            user = getattr(self._cfg, "user_name", "")

            g.content.ignore = ignore
            g.content.revision = new_revision
            g.content.version_label = version_label
            g.content.note = note
            g.content.updated_by = user
            g.content.updated_at = now
            g.content.content_hash = new_hash
            g.history.append(
                game_mod.HistoryEntry(
                    revision=new_revision,
                    version_label=version_label,
                    note=note,
                    by=user,
                    at=now,
                    content_hash=new_hash,
                )
            )
            g.files = files
            g.sizes = sizes
            g.size_bytes = sum(sizes.values())
            game_mod.save(g)

            snapshot = content.take_snapshot(g.path, files.keys())
            self.set_state(
                game_id,
                "clean",
                snapshot=snapshot,
                summary=dict(_EMPTY_SUMMARY),
                changed=[],
                removed=[],
                added=[],
            )

            from deckdrop.core import torrent_prep

            torrent_prep.invalidate_torrent(game_id)

            self._emit(
                "content_publish_complete",
                {
                    "game_id": game_id,
                    "unchanged": False,
                    "revision": new_revision,
                    "version_label": version_label,
                },
            )
        except Exception as exc:
            log.error("Publish failed for %s: %s", game_id, exc)
            self.set_state(game_id, "modified")
            self._emit("content_publish_error", {"game_id": game_id, "error": str(exc)})
        finally:
            with self._lock:
                self._publishing.discard(game_id)

    # -- helpers --

    def _transfer(self) -> object | None:
        try:
            from deckdrop.api import state as app_state

            return app_state.get().transfer
        except RuntimeError:
            return None

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None:
            return
        from deckdrop.api.websocket import broadcast

        try:
            asyncio.run_coroutine_threadsafe(broadcast(event, data), loop)
        except Exception as exc:
            log.debug("Could not emit %s: %s", event, exc)
