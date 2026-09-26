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

from deckdrop.core import content, debuglog, integrity
from deckdrop.core import game as game_mod

log = logging.getLogger(__name__)

_BUSY_STATES = ("hashing", "publishing", "updating")
# "unverified": no file hashes yet and no hash running (interrupted by a
# restart, or cancelled so an update could start). Not shareable, but not
# "modified" either – the update option stays available and the next
# baseline/scan hashes it.
_EMPTY_SUMMARY: dict[str, int] = {"changed": 0, "removed": 0, "added": 0}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class _HashCancelled(Exception):
    """Raised inside `_hash_full` when `cancel_hash()` asked it to stop."""


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
        # game_id -> (cancel requested, hashing finished) for a running _hash_full
        self._hash_runs: dict[str, tuple[threading.Event, threading.Event]] = {}
        # games whose baseline hash must not (re)start, e.g. while an update
        # is being set up after cancel_hash()
        self._hash_hold: set[str] = set()
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
                entry = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            state = entry.get("state")
            if state in ("hashing", "publishing"):
                # Nothing can still be hashing/publishing right after startup –
                # the app was stopped mid-way (e.g. the Steam Deck went to
                # sleep). Left as is, ensure_baseline/scan would skip this game
                # as "busy" forever. "updating" is different: that one is
                # resumed by the TransferManager and must survive restarts.
                entry.pop("hash_progress", None)
                if state == "hashing":
                    entry["state"] = "unverified"
                    entry["pending_hash_reason"] = "interrupted"
                else:
                    entry["state"] = "modified"
                self._save_entry(p.stem, entry)
            self._entries[p.stem] = entry

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
        if state not in ("hashing", "publishing"):
            entry.pop("hash_progress", None)
        self._entries[game_id] = entry
        self._save_entry(game_id, entry)
        self._emit(
            "game_content_state",
            {"game_id": game_id, "state": state, "summary": entry.get("summary", _EMPTY_SUMMARY)},
        )

    def request_rehash(self, game_id: str, reason: str) -> None:
        """Remember why the next full baseline hash for `game_id` happens.

        Persisted in the tracker entry, so it survives until the hash actually
        runs (e.g. a fresh download that is not in the library yet).
        """
        entry = dict(self._entries.get(game_id, {}))
        entry["pending_hash_reason"] = reason
        self._entries[game_id] = entry
        self._save_entry(game_id, entry)

    def cancel_hash(self, game_id: str, timeout: float = 30.0) -> bool:
        """Stop a running baseline hash for `game_id` (e.g. an update was started).

        Returns True once no hash is running any more. The cancelled hash does
        not write anything to the game; the entry becomes "unverified" with
        `pending_hash_reason="update_started"`, so a later scan redoes it if
        the update never finishes. Also holds off any *new* baseline hash of
        this game (a scan pass could otherwise restart it before the update
        marks the game "updating") until `release_hash_hold()`.
        """
        with self._lock:
            self._hash_hold.add(game_id)
            run = self._hash_runs.get(game_id)
        if run is None:
            return True
        cancel, done = run
        cancel.set()
        return done.wait(timeout)

    def rename_game(self, old_id: str, new_id: str) -> None:
        """Move the tracker state from `old_id` to `new_id` (legacy ID relink).

        A baseline hash still running under the old ID is stopped first:
        it would otherwise save the game with its old ID when done and undo
        the relink. The game then becomes "unverified" under the new ID
        (update option visible right away); the next scan redoes the hash.
        """
        if old_id == new_id:
            return
        hashing = self.state(old_id) == "hashing"
        self.cancel_hash(old_id)
        try:
            with self._lock:
                entry = self._entries.pop(old_id, None)
            old_path = self._state_path(old_id)
            if entry is None and not hashing:
                return
            entry = dict(entry or {})
            if hashing or entry.get("state") == "hashing":
                entry["state"] = "unverified"
                entry.pop("hash_progress", None)
                entry["pending_hash_reason"] = "relinked"
            if new_id not in self._entries:
                self._entries[new_id] = entry
                self._save_entry(new_id, entry)
            try:
                old_path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("Could not remove content state of %s: %s", old_id, exc)
        finally:
            self.release_hash_hold(old_id)

    def release_hash_hold(self, game_id: str) -> None:
        with self._lock:
            self._hash_hold.discard(game_id)

    def hash_progress(self, game_id: str) -> float | None:
        """Fraction of files hashed while state is hashing or publishing."""
        if self.state(game_id) not in ("hashing", "publishing"):
            return None
        value = self._entries.get(game_id, {}).get("hash_progress")
        if value is None:
            return None
        return float(value)

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
        if self.state(game_id) in _BUSY_STATES:
            # Never clobber an in-progress update/publish/hash (e.g. the
            # startup baseline pass over every game must not reset a game
            # mid-update back to "clean" after a restart – see Phase 5 "5.2.5").
            return

        if not g.files:
            cancel, done = threading.Event(), threading.Event()
            with self._lock:
                if game_id in self._hash_hold:
                    return
                self._hash_runs[game_id] = (cancel, done)
            try:
                self.set_state(game_id, "hashing", hash_progress=0.0)
                try:
                    self._hash_full(g, cancel)
                except _HashCancelled:
                    log.info("Baseline hashing for %s cancelled", game_id)
                    self.set_state(game_id, "unverified", pending_hash_reason="update_started")
                    return
                except Exception as exc:
                    log.error("Baseline hashing failed for %s: %s", game_id, exc)
                    self.set_state(game_id, "modified")
                    return
            finally:
                with self._lock:
                    self._hash_runs.pop(game_id, None)
                done.set()
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
            return

        if not g.sizes and self.state(game_id) != "modified":
            snap = content.take_snapshot(g.path, g.files.keys())
            g.sizes = {rel: snap[rel][0] for rel in snap if rel in g.files}
            game_mod.save(g)

        # A published manifest already exists. Do not adopt the current disk
        # as a new clean baseline — a patched game stays unshareable until
        # the user publishes an update.
        entry = self._entries.get(game_id, {})
        if entry.get("snapshot") or self.state(game_id) == "modified":
            self.scan(game_id)
            return
        self._baseline_from_manifest(g)

    def _baseline_from_manifest(self, g: game_mod.GameInfo) -> None:
        """First snapshot for a game that already has file hashes.

        Sizes that still match disk become the baseline. A size that does not
        match is a local patch: the game stays modified and is not hashed.
        """
        current = content.take_snapshot(g.path, g.files.keys())
        drifted: list[str] = []
        for rel in g.files:
            st = current.get(rel)
            manifest_size = g.sizes.get(rel)
            if st is None or (manifest_size is not None and st[0] != manifest_size):
                drifted.append(rel)
        if drifted:
            drifted_set = set(drifted)
            kept = {rel: current[rel] for rel in current if rel not in drifted_set}
            self.set_state(
                g.id,
                "modified",
                snapshot=kept,
                summary={"changed": len(drifted), "removed": 0, "added": 0},
                changed=sorted(drifted),
                removed=[],
                added=[],
            )
            transfer = self._transfer()
            if transfer is not None:
                transfer.drop_seed(g.id)
            return
        self.set_state(
            g.id,
            "clean",
            snapshot=current,
            summary=dict(_EMPTY_SUMMARY),
            changed=[],
            removed=[],
            added=[],
        )

    def _hash_reason(self, g: game_mod.GameInfo) -> str:
        pending = self._entries.get(g.id, {}).get("pending_hash_reason")
        if pending:
            return str(pending)
        if g.origin.peer_id or g.origin.peer_name:
            return "download_without_manifest"
        return "new_local_game"

    def _hash_full(self, g: game_mod.GameInfo, cancel: threading.Event | None = None) -> None:
        rels = content.iter_content_files(g.path, g.content.ignore)
        debuglog.record("blake2b", self._hash_reason(g), g.id, rels)

        def _check_cancel(_n: int) -> None:
            if cancel is not None and cancel.is_set():
                raise _HashCancelled()

        files: dict[str, str] = {}
        sizes: dict[str, int] = {}
        total = 0
        n = len(rels) or 1
        done = 0
        for rel in rels:
            _check_cancel(0)
            path = g.path / rel
            try:
                st = path.stat()
            except OSError:
                done += 1
                self._note_hash_progress(g.id, done / n)
                continue
            files[rel] = integrity.hash_file(path, _check_cancel)
            sizes[rel] = st.st_size
            total += st.st_size
            done += 1
            self._note_hash_progress(g.id, done / n)
        g.files = files
        g.sizes = sizes
        g.size_bytes = total
        if not g.content.content_hash:
            g.content.content_hash = content.compute_content_hash(files, sizes)
        game_mod.save(g)
        entry = self._entries.get(g.id)
        if entry and entry.pop("pending_hash_reason", None) is not None:
            self._save_entry(g.id, entry)

    def _note_hash_progress(self, game_id: str, progress: float) -> None:
        entry = dict(self._entries.get(game_id, {}))
        prev = float(entry.get("hash_progress") or 0.0)
        entry["hash_progress"] = progress
        self._entries[game_id] = entry
        if progress >= 1 or progress - prev >= 0.01:
            self._save_entry(game_id, entry)
        self._emit(
            "content_publish_progress",
            {"game_id": game_id, "progress": progress},
        )

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
        if result.mtime_only:
            debuglog.record("blake2b", "mtime_changed", game_id, result.mtime_only)
        for rel in result.mtime_only:
            path = g.path / rel
            try:
                new_hash = integrity.hash_file(path)
                st = path.stat()
            except OSError:
                changed.append(rel)
                continue
            if new_hash == g.files.get(rel):
                # Hash still matches the manifest: the snapshot may move forward.
                snapshot[rel] = [st.st_size, st.st_mtime_ns]
            else:
                changed.append(rel)

        # Snapshot stays at the stat from the last published hash. Moving it
        # forward for a size change made the next scan (and publish) treat the
        # patch as unchanged, so the typed version never replaced Rev. 1.
        # A snapshot already moved forward still matches disk; the published
        # size does not, so those files count as changed too.
        removed_set = set(result.removed)
        seen = set(changed)
        for rel, manifest_size in g.sizes.items():
            if rel in seen or rel in removed_set:
                continue
            path = content.safe_join(g.path, rel)
            if path is None:
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_size != manifest_size:
                changed.append(rel)
                seen.add(rel)

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
            rehashed: list[str] = []
            total = len(rels) or 1
            for i, rel in enumerate(rels):
                path = g.path / rel
                try:
                    st = path.stat()
                except OSError:
                    continue
                old_hash = g.files.get(rel)
                snap = old_snapshot.get(rel)
                manifest_size = g.sizes.get(rel)
                # Reuse the manifest hash only when this exact size and mtime
                # were already hashed. A snapshot that was advanced without a
                # new hash must not hide a patch.
                if (
                    old_hash
                    and isinstance(snap, (list, tuple))
                    and len(snap) >= 2
                    and snap[0] == st.st_size
                    and snap[1] == st.st_mtime_ns
                    and manifest_size == st.st_size
                ):
                    h = old_hash
                else:
                    h = integrity.hash_file(path)
                    rehashed.append(rel)
                files[rel] = h
                sizes[rel] = st.st_size
                self._note_hash_progress(game_id, (i + 1) / total)

            debuglog.record(
                "blake2b",
                "publish",
                game_id,
                rehashed,
                detail=f"{len(files) - len(rehashed)} Datei(en) ohne Rehash übernommen",
            )
            new_hash = content.compute_content_hash(files, sizes)
            if new_hash and new_hash == g.content.content_hash and ignore == g.content.ignore:
                # Same files: still keep a version name the user typed (e.g. 1.0.5
                # from a patch that was already in the folder). No new revision
                # and no torrent rebuild — peers see the label on the same content.
                label_updated = self._apply_label(g, version_label, note)
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
                self._reseed(game_id, g.path)
                self._emit(
                    "content_publish_complete",
                    {
                        "game_id": game_id,
                        "unchanged": True,
                        "label_updated": label_updated,
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

    def _apply_label(self, g: game_mod.GameInfo, version_label: str, note: str) -> bool:
        """Store a version name on the current revision. Returns True if saved."""
        label_changed = bool(version_label) and version_label != g.content.version_label
        note_changed = bool(note) and note != g.content.note
        if not label_changed and not note_changed:
            return False
        now = _now()
        user = getattr(self._cfg, "user_name", "")
        if label_changed:
            g.content.version_label = version_label
        if note_changed:
            g.content.note = note
        g.content.updated_by = user
        g.content.updated_at = now
        for entry in reversed(g.history):
            if entry.revision != g.content.revision:
                continue
            if label_changed:
                entry.version_label = version_label
            if note_changed:
                entry.note = note
            break
        game_mod.save(g)
        return True

    def _reseed(self, game_id: str, game_path: Path) -> None:
        transfer = self._transfer()
        if transfer is None:
            return
        cache = Path(self._cfg.torrent_cache) / f"{game_id}.torrent"
        if cache.is_file():
            transfer.seed_from_cache(game_id, game_path, cache)

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
