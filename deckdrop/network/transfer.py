"""
libtorrent-based download manager. LAN-only.

libtorrent is optional. All methods raise RuntimeError if not installed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import shutil
import time
from dataclasses import dataclass, field, fields
from pathlib import Path

from deckdrop.api.websocket import broadcast
from deckdrop.core.config import Config

log = logging.getLogger(__name__)


@dataclass
class DownloadStatus:
    id: str
    game_id: str
    game_name: str
    peer_id: str
    peer_name: str
    status: str  # queued | downloading | verifying | seeding | done | error | paused
    progress: float  # 0.0–1.0
    speed_bytes_sec: int
    downloaded_bytes: int
    total_bytes: int
    num_peers: int
    pieces_total: int = 0
    pieces_missing: int = 0
    bytes_remaining: int = 0
    error: str | None = None
    error_hint: str | None = None
    dest_path: str | None = None


@dataclass
class _Handle:
    download_id: str
    game_id: str
    game_name: str
    peer_id: str
    peer_name: str
    handle: object  # lt.torrent_handle
    dest_path: Path
    started_at: float = field(default_factory=time.monotonic)


@dataclass
class _PersistedRecord:
    download_id: str
    game_id: str
    game_name: str
    peer_id: str
    peer_name: str
    magnet: str
    dest_path: str
    started_at: float
    peer_address: str = ""
    user_paused: bool = False
    error: str | None = None
    progress: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    info_hash: str = ""


_MAGNET_CHECK_INTERVAL = 15.0

# Shown in GET /api/downloads only; finished/seeding use Meine Spiele + _seed_handles.
_ACTIVE_DOWNLOAD_STATUSES = frozenset({"queued", "downloading", "verifying", "paused", "error"})


def _info_hash_from_magnet(magnet: str) -> str:
    m = re.search(r"btih:([a-fA-F0-9]{40})", magnet, re.I)
    return m.group(1).lower() if m else ""


def _peer_http_url(address: str, port: int, path: str) -> str:
    host = address
    if ":" in address and not address.startswith("["):
        host = f"[{address}]"
    return f"http://{host}:{port}{path}"


def _fetch_peer_magnet(address: str, port: int, game_id: str) -> tuple[str, str]:
    import httpx

    url = _peer_http_url(address, port, f"/api/games/{game_id}/magnet")
    r = httpx.get(url, timeout=15.0)
    r.raise_for_status()
    data = r.json()
    magnet = data["magnet"]
    info_hash = (data.get("info_hash") or _info_hash_from_magnet(magnet)).lower()
    return magnet, info_hash


def _lt():
    try:
        import libtorrent as lt

        return lt
    except ImportError:
        raise RuntimeError("libtorrent is not installed. Install it with: pip install libtorrent")


_TRANSFER_ERROR_PATTERNS: list[tuple[str, str]] = [
    ("no such file", "Dateipfad nicht gefunden."),
    ("filesystem", "Dateipfad nicht gefunden."),
    ("timed out", "Zeitüberschreitung – Host erreichbar?"),
    ("timeout", "Zeitüberschreitung – Host erreichbar?"),
    ("connection refused", "Verbindung abgelehnt – Torrent-Port 7374 prüfen."),
    ("parse_magnet_uri", "Magnet-Link konnte nicht gelesen werden."),
    ("does not match c++ signature", "libtorrent-API inkompatibel – App aktualisieren."),
    ("hash", "Prüfsumme fehlgeschlagen – Datei beschädigt oder unvollständig."),
    ("piece", "Datenblock fehlerhaft – Download unvollständig."),
    ("no space", "Nicht genug Speicherplatz."),
    ("disk", "Festplattenfehler beim Schreiben."),
    ("0 peers", "Kein Peer verbunden."),
    ("no peers", "Kein Peer verbunden."),
]

_TRANSFER_ERROR_HINTS: list[tuple[str, str]] = [
    ("hash", "Erneut versuchen; Host-Dateien prüfen."),
    ("piece", "Erneut versuchen; Host-Dateien prüfen."),
    ("no space", "Speicherplatz freigeben, dann „Erneut“."),
    ("disk", "Speicherplatz und Berechtigungen prüfen."),
    ("no peers", "Host online lassen und erneut versuchen."),
    ("0 peers", "Host online lassen und erneut versuchen."),
    ("timed out", "Host erreichbar? Kurz warten und „Erneut“."),
    ("timeout", "Host erreichbar? Kurz warten und „Erneut“."),
    ("connection refused", "Firewall: Torrent-Port 7374 am Host prüfen."),
    ("no such file", "Download entfernen und neu starten."),
    ("filesystem", "Download entfernen und neu starten."),
]


def _parse_magnet_params(lt: object, magnet: str, save_path: str) -> object:
    """libtorrent 2.x: parse_magnet_uri(str) -> params; 1.x: parse_magnet_uri(str, params)."""
    try:
        params = lt.parse_magnet_uri(magnet)  # type: ignore[misc]
    except TypeError:
        params = lt.add_torrent_params()
        lt.parse_magnet_uri(magnet, params)  # type: ignore[misc]
    params.save_path = save_path
    return params


def _friendly_transfer_error(raw: str) -> str:
    lower = raw.lower()
    for needle, msg in _TRANSFER_ERROR_PATTERNS:
        if needle in lower:
            return msg
    if len(raw) > 120:
        return raw[:117] + "…"
    return raw


def _transfer_error_hint(message: str) -> str:
    lower = message.lower()
    for needle, hint in _TRANSFER_ERROR_HINTS:
        if needle in lower:
            return hint
    return "„Erneut“ versuchen oder Host-Verbindung prüfen."


# Near-complete stall: reconnect peer and surface error after this long without progress.
_STALL_NUDGE_INTERVAL = 5.0
_STALL_REANNOUNCE_INTERVAL = 30.0
_STALL_RECHECK_AFTER = 90.0
_STALL_ERROR_AFTER = 600.0
_STALL_REMAINING_MAX = 5 * 1024 * 1024  # only nudge when < 5 MiB left


def _bytes_from_status(s: object) -> tuple[int, int, int]:
    """Return (downloaded_bytes, total_bytes, bytes_remaining)."""
    total = int(s.total_wanted)
    done = int(s.total_done)
    wanted_done = int(getattr(s, "total_wanted_done", done) or done)
    downloaded = max(done, wanted_done)
    return downloaded, total, max(0, total - downloaded)


def _bytes_complete(
    downloaded_bytes: int,
    total_bytes: int,
    **_: object,
) -> bool:
    """Byte-only completion check for persisted records without a live handle."""
    return total_bytes > 0 and downloaded_bytes >= total_bytes


def _pieces_from_status(s: object) -> tuple[int, int]:
    try:
        total = int(s.num_pieces)
    except (AttributeError, TypeError, ValueError):
        return 0, 0
    if total <= 0:
        return 0, 0
    pieces = getattr(s, "pieces", None)
    if pieces is None:
        # libtorrent didn't populate pieces (status() without query_pieces flag).
        # Treat as unknown – used for UI only; completion uses _torrent_is_complete.
        return 0, 0
    try:
        if hasattr(pieces, "count"):
            have = int(pieces.count(True))
        else:
            have = sum(1 for i in range(total) if pieces[i])
    except Exception:
        have = 0
    return total, max(0, total - have)


def _map_torrent_state(lt: object, state_int: int) -> str:
    """Map libtorrent 2.x state enum (starts at 1, not 0)."""
    ts = lt.torrent_status
    mapping = {
        int(ts.checking_files): "checking",
        int(ts.downloading_metadata): "queued",
        int(ts.downloading): "downloading",
        int(ts.finished): "done",
        int(ts.seeding): "seeding",
        int(ts.allocating): "queued",
        int(ts.checking_resume_data): "checking",
    }
    return mapping.get(state_int, "downloading")


def _torrent_is_complete(lt: object, s: object) -> bool:
    """True only when libtorrent reports the torrent finished downloading."""
    state = _map_torrent_state(lt, int(s.state))
    if state not in ("done", "seeding"):
        return False
    downloaded, total, _ = _bytes_from_status(s)
    return total > 0 and downloaded >= total


def _status_flags(lt: object) -> int:
    """Status query flags for accurate byte counters and piece info."""
    th = lt.torrent_handle
    flags = 0
    for name in ("query_accurate_download_counters", "query_pieces"):
        bit = getattr(th, name, None)
        if bit is not None:
            flags |= int(bit)
    return flags


def _torrent_status(handle: object) -> object:
    """Return torrent_status with accurate counters when supported."""
    lt = _lt()
    flags = _status_flags(lt)
    if flags:
        try:
            return handle.status(flags)
        except (TypeError, ValueError):
            pass
    return handle.status()


def _progress_from_status(s: object) -> float:
    """Prefer byte ratio; libtorrent's progress can lag or jump."""
    downloaded, total, _ = _bytes_from_status(s)
    if total > 0:
        return min(1.0, downloaded / total)
    return float(s.progress)


class TransferManager:
    def __init__(self, cfg: Config) -> None:
        from deckdrop.core.torrent import lan_session

        self._cfg = cfg
        self._session = lan_session(cfg.torrent_port)
        self._handles: dict[str, _Handle] = {}  # download_id → _Handle
        self._seed_handles: dict[str, object] = {}  # game_id → lt.torrent_handle
        self._paused: dict[str, _PersistedRecord] = {}  # metadata for all downloads
        self._user_paused: set[str] = set()  # download_ids paused by user
        self._library = None  # injected after init via set_library()
        self._poll_task: asyncio.Task | None = None
        self._completed_ids: set[str] = set()
        self._last_downloaded: dict[str, int] = {}
        self._last_progress_at: dict[str, float] = {}
        self._last_nudge_at: dict[str, float] = {}
        self._last_reannounce_at: dict[str, float] = {}
        self._recheck_done: set[str] = set()
        self._last_magnet_check_at: dict[str, float] = {}
        self._pending_download_dests: set[Path] = set()
        if cfg.max_upload_speed or cfg.max_download_speed:
            self.apply_rate_limits()
        self._load_state()

    @staticmethod
    def _rate_limit_bytes(kb_per_sec: int) -> int:
        """UI stores KB/s; libtorrent expects bytes/s (0 = unlimited)."""
        return kb_per_sec * 1024 if kb_per_sec > 0 else 0

    def set_library(self, library: object) -> None:
        self._library = library

    def apply_rate_limits(self) -> None:
        """Apply upload/download speed limits from config to the libtorrent session."""
        _lt()  # verify installed
        up = self._rate_limit_bytes(self._cfg.max_upload_speed)
        down = self._rate_limit_bytes(self._cfg.max_download_speed)
        self._session.apply_settings(
            {
                "upload_rate_limit": up,
                "download_rate_limit": down,
            }
        )
        log.debug("Rate limits set: up=%s down=%s bytes/s", up, down)

    def start_polling(self) -> None:
        """Start the async background loop. Call after the event loop is running."""
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop())

    def restore_active_downloads(self) -> int:
        """Re-attach incomplete downloads after restart (not user-paused)."""
        restored = 0
        for rec in list(self._paused.values()):
            if rec.user_paused or rec.download_id in self._user_paused:
                continue
            if rec.error:
                continue
            if rec.download_id in self._handles:
                continue
            if rec.total_bytes > 0 and rec.downloaded_bytes >= rec.total_bytes:
                continue
            if self._reattach_download(rec):
                restored += 1
        if restored:
            log.info("Restored %d download(s) after restart", restored)
        return restored

    def update_peer_address(self, peer_id: str, address: str) -> None:
        """Keep torrent peer connections in sync when mDNS reports a new IP."""
        if not address:
            return
        for rec in self._paused.values():
            if rec.peer_id == peer_id and rec.peer_address != address:
                rec.peer_address = address
        for h in self._handles.values():
            if h.peer_id != peer_id:
                continue
            try:
                h.handle.connect_peer((address, self._cfg.torrent_port))
            except Exception as exc:
                log.warning("connect_peer after address change failed: %s", exc)
        self._save_state()

    def drop_seed(self, game_id: str) -> None:
        """Stop seeding a game (e.g. before torrent rebuild)."""
        handle = self._seed_handles.pop(game_id, None)
        if not handle:
            return
        try:
            self._session.remove_torrent(handle)
        except Exception as exc:
            log.warning("drop_seed failed for %s: %s", game_id, exc)
        log.info("Stopped seeding game %s", game_id)

    def _promote_download_to_seed(self, h: _Handle) -> None:
        """Keep libtorrent seeding after download completes; hide from download list."""
        existing = self._seed_handles.pop(h.game_id, None)
        if existing is not None and existing is not h.handle:
            try:
                self._session.remove_torrent(existing)
            except Exception:
                pass
        self._seed_handles[h.game_id] = h.handle
        log.info(
            "Download %s archived; %s now seeds in background",
            h.download_id,
            h.game_name,
        )

    def recheck_seed(self, game_id: str) -> None:
        """Re-verify files on disk for a seeding torrent after rebuild."""
        handle = self._seed_handles.get(game_id)
        if not handle:
            return
        try:
            handle.force_recheck()
            log.debug("force_recheck on seed for %s", game_id)
        except Exception as exc:
            log.warning("recheck_seed failed for %s: %s", game_id, exc)

    def seed_from_cache(self, game_id: str, game_path: Path, torrent_path: Path) -> None:
        """Seed a local game from a cached .torrent file (host side)."""
        if game_id in self._seed_handles:
            return
        if not torrent_path.is_file() or not game_path.is_dir():
            log.warning("Cannot seed %s: missing torrent or game path", game_id)
            return
        lt = _lt()
        ti = lt.torrent_info(str(torrent_path))
        params = lt.add_torrent_params()
        params.ti = ti
        params.save_path = str(game_path.parent)
        params.flags |= lt.torrent_flags.seed_mode
        params.flags |= lt.torrent_flags.auto_managed
        handle = self._session.add_torrent(params)
        self._seed_handles[game_id] = handle
        log.info("Seeding game %s from %s", game_id, torrent_path)

    def seed_all_shared(self, library: object, cfg: Config) -> None:
        """Start seeding every local game that has a cached .torrent file."""
        for g in library.all():  # type: ignore[union-attr]
            cache = cfg.torrent_cache / f"{g.id}.torrent"
            if cache.is_file():
                try:
                    self.seed_from_cache(g.id, g.path, cache)
                except Exception as exc:
                    log.warning("Could not seed %s: %s", g.id, exc)

    def start_download(
        self,
        game_id: str,
        game_name: str,
        magnet: str,
        peer_id: str,
        peer_name: str,
        peer_address: str,
        dest_path: Path,
        download_id: str | None = None,
    ) -> str:
        lt = _lt()
        download_id = download_id or secrets.token_hex(4)
        # Torrent paths are e.g. "GameName/file.bin" – save_path must be the parent dir.
        save_path = dest_path.parent
        save_path.mkdir(parents=True, exist_ok=True)
        params = _parse_magnet_params(lt, magnet, str(save_path))
        handle = self._session.add_torrent(params)

        # Directly connect to the peer who has the game – no waiting for LSD
        handle.connect_peer((peer_address, self._cfg.torrent_port))

        self._pending_download_dests.discard(dest_path.resolve())
        self._handles[download_id] = _Handle(
            download_id=download_id,
            game_id=game_id,
            game_name=game_name,
            peer_id=peer_id,
            peer_name=peer_name,
            handle=handle,
            dest_path=dest_path,
        )
        info_hash = _info_hash_from_magnet(magnet)
        self._paused[download_id] = _PersistedRecord(
            download_id=download_id,
            game_id=game_id,
            game_name=game_name,
            peer_id=peer_id,
            peer_name=peer_name,
            magnet=magnet,
            dest_path=str(dest_path),
            started_at=time.time(),
            peer_address=peer_address,
            info_hash=info_hash,
        )
        self._user_paused.discard(download_id)
        self._save_state()
        log.info("Download started: %s (%s) from %s", game_name, download_id, peer_address)
        return download_id

    def pause_download(self, download_id: str) -> bool:
        rec = self._paused.get(download_id)
        if not rec:
            return False
        h = self._handles.get(download_id)
        if h:
            try:
                h.handle.pause()
            except Exception as exc:
                log.warning("pause failed for %s: %s", download_id, exc)
        self._user_paused.add(download_id)
        rec.user_paused = True
        self._save_state()
        log.info("Download paused: %s", download_id)
        return True

    def resume_download(self, download_id: str) -> bool:
        rec = self._paused.get(download_id)
        if not rec:
            return False
        rec.user_paused = False
        rec.error = None
        self._user_paused.discard(download_id)

        h = self._handles.get(download_id)
        if h:
            try:
                h.handle.resume()
            except Exception as exc:
                log.warning("resume failed for %s: %s", download_id, exc)
            if rec.peer_address:
                try:
                    h.handle.connect_peer((rec.peer_address, self._cfg.torrent_port))
                except Exception as exc:
                    log.warning("connect_peer on resume failed: %s", exc)
            self._save_state()
            return True

        return self._reattach_download(rec)

    def retry_download(self, download_id: str) -> bool:
        """Resume after error; re-attaches torrent if needed."""
        rec = self._paused.get(download_id)
        if not rec:
            return False
        rec.error = None
        self._completed_ids.discard(download_id)
        self._last_downloaded.pop(download_id, None)
        self._last_progress_at.pop(download_id, None)
        self._last_nudge_at.pop(download_id, None)
        self._last_reannounce_at.pop(download_id, None)
        self._last_magnet_check_at.pop(download_id, None)
        self._recheck_done.discard(download_id)
        self._user_paused.discard(download_id)
        rec.user_paused = False

        h = self._handles.get(download_id)
        if h:
            try:
                self._session.remove_torrent(h.handle)
            except Exception:
                pass
            del self._handles[download_id]

        return self._reattach_download(rec)

    def upgrade_download(self, download_id: str, magnet: str, info_hash: str) -> bool:
        """Hot-swap magnet/info_hash for an active download; keeps files on disk."""
        rec = self._paused.get(download_id)
        if not rec or not info_hash:
            return False
        if rec.info_hash == info_hash.lower():
            return False
        if rec.user_paused or download_id in self._user_paused:
            return False

        h = self._handles.pop(download_id, None)
        if h:
            try:
                self._session.remove_torrent(h.handle)
            except Exception:
                pass

        rec.magnet = magnet
        rec.info_hash = info_hash.lower()
        rec.error = None
        self._completed_ids.discard(download_id)
        self._last_downloaded.pop(download_id, None)
        self._last_progress_at.pop(download_id, None)
        self._last_nudge_at.pop(download_id, None)
        self._last_reannounce_at.pop(download_id, None)
        self._last_magnet_check_at.pop(download_id, None)
        self._recheck_done.discard(download_id)

        if not self._reattach_download(rec):
            return False
        log.info(
            "Upgraded download %s to new info_hash %s…",
            download_id,
            rec.info_hash[:8],
        )
        return True

    def remove_download(self, download_id: str, *, delete_files: bool = False) -> bool:
        rec = self._paused.pop(download_id, None)
        h = self._handles.pop(download_id, None)
        self._user_paused.discard(download_id)
        self._completed_ids.discard(download_id)
        self._last_downloaded.pop(download_id, None)
        self._last_progress_at.pop(download_id, None)
        self._last_nudge_at.pop(download_id, None)
        self._last_reannounce_at.pop(download_id, None)
        self._last_magnet_check_at.pop(download_id, None)
        self._recheck_done.discard(download_id)
        if h:
            try:
                self._session.remove_torrent(h.handle)
            except Exception:
                pass
        if not rec and not h:
            return False

        dest: Path | None = None
        if rec:
            dest = Path(rec.dest_path)
        elif h:
            dest = h.dest_path

        self._save_state()
        if delete_files and dest and dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
            log.info("Deleted download files: %s", dest)
        log.info("Download removed: %s (delete_files=%s)", download_id, delete_files)
        return True

    def cancel(self, download_id: str) -> bool:
        return self.remove_download(download_id, delete_files=False)

    def _reattach_download(self, rec: _PersistedRecord) -> bool:
        """Re-add torrent to session (after pause without handle or retry)."""
        if not rec.info_hash and rec.magnet:
            rec.info_hash = _info_hash_from_magnet(rec.magnet)
        lt = _lt()
        dest_path = Path(rec.dest_path)
        save_path = dest_path.parent
        save_path.mkdir(parents=True, exist_ok=True)
        try:
            params = _parse_magnet_params(lt, rec.magnet, str(save_path))
            handle = self._session.add_torrent(params)
            if rec.peer_address:
                handle.connect_peer((rec.peer_address, self._cfg.torrent_port))
            self._handles[rec.download_id] = _Handle(
                download_id=rec.download_id,
                game_id=rec.game_id,
                game_name=rec.game_name,
                peer_id=rec.peer_id,
                peer_name=rec.peer_name,
                handle=handle,
                dest_path=dest_path,
            )
            self._save_state()
            log.info("Download re-attached: %s", rec.download_id)
            return True
        except Exception as exc:
            rec.error = _friendly_transfer_error(str(exc))
            self._save_state()
            log.warning("Could not re-attach download %s: %s", rec.download_id, exc)
            return False

    def get_status(self, download_id: str) -> DownloadStatus | None:
        h = self._handles.get(download_id)
        if h:
            return self._build_status(h)
        rec = self._paused.get(download_id)
        if rec:
            return self._paused_status(rec)
        return None

    def reserve_download_dest(self, dest_path: Path) -> None:
        """Mark destination while magnet is fetched — keep out of Meine Spiele."""
        self._pending_download_dests.add(dest_path.resolve())

    def release_download_dest(self, dest_path: Path) -> None:
        self._pending_download_dests.discard(dest_path.resolve())

    def incomplete_download_dest_paths(self) -> frozenset[Path]:
        """Dest folders for downloads not yet finished — hide from Meine Spiele."""
        paths: set[Path] = set(self._pending_download_dests)
        for h in self._handles.values():
            if h.download_id not in self._completed_ids:
                paths.add(h.dest_path.resolve())
        for rec in self._paused.values():
            if rec.download_id in self._completed_ids:
                continue
            if not _bytes_complete(rec.downloaded_bytes, rec.total_bytes):
                paths.add(Path(rec.dest_path).resolve())
        return frozenset(paths)

    def all_statuses(self) -> list[DownloadStatus]:
        seen: set[str] = set()
        result: list[DownloadStatus] = []
        for h in self._handles.values():
            result.append(self._build_status(h))
            seen.add(h.download_id)
        for did, rec in self._paused.items():
            if did not in seen:
                result.append(self._paused_status(rec))
        return [s for s in result if s.status in _ACTIVE_DOWNLOAD_STATUSES]

    def _register_downloaded_game(self, h: _Handle) -> None:
        """Write deckdrop.toml with origin peer so My Games shows the source."""
        from deckdrop.core import game as game_mod
        from deckdrop.core import integrity

        dest = h.dest_path
        if not dest.is_dir():
            return
        try:
            info = game_mod.load_from_path(dest)
            if info:
                if not info.origin.peer_name:
                    info.origin.peer_id = h.peer_id
                    info.origin.peer_name = h.peer_name
                    game_mod.save(info)
            else:
                info = game_mod.create_new(dest, h.game_name, added_by=self._cfg.user_name)
                info.origin.peer_id = h.peer_id
                info.origin.peer_name = h.peer_name
                info.size_bytes = integrity.dir_size(dest)
                game_mod.save(info)
            log.info("Registered download at %s (from %s)", dest, h.peer_name)
        except Exception as exc:
            log.warning("Could not register downloaded game at %s: %s", dest, exc)

    def shutdown(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
        for h in self._handles.values():
            try:
                h.handle.pause()
            except Exception:
                pass
        log.info("TransferManager shut down")

    # -- Internal --

    def _sync_rec_from_status(self, rec: _PersistedRecord, status: DownloadStatus) -> None:
        rec.progress = status.progress
        rec.downloaded_bytes = status.downloaded_bytes
        rec.total_bytes = status.total_bytes
        if status.error:
            rec.error = status.error

    def _paused_status(self, rec: _PersistedRecord) -> DownloadStatus:
        if rec.error:
            status = "error"
        elif rec.user_paused or rec.download_id in self._user_paused:
            status = "paused"
        elif _bytes_complete(rec.downloaded_bytes, rec.total_bytes):
            status = "done"
        else:
            status = "queued"
        err = rec.error
        return DownloadStatus(
            id=rec.download_id,
            game_id=rec.game_id,
            game_name=rec.game_name,
            peer_id=rec.peer_id,
            peer_name=rec.peer_name,
            status=status,
            progress=rec.progress,
            speed_bytes_sec=0,
            downloaded_bytes=rec.downloaded_bytes,
            total_bytes=rec.total_bytes,
            num_peers=0,
            bytes_remaining=max(0, rec.total_bytes - rec.downloaded_bytes),
            error=err,
            error_hint=_transfer_error_hint(err) if err else None,
            dest_path=rec.dest_path,
        )

    def _build_status(self, h: _Handle) -> DownloadStatus:
        _lt()  # verify installed
        try:
            s = _torrent_status(h.handle)
            lt = _lt()
            state_int = int(s.state)
            status_str = _map_torrent_state(lt, state_int)
            progress = _progress_from_status(s)
            downloaded_bytes, total_bytes, bytes_remaining = _bytes_from_status(s)
            pieces_total, pieces_missing = _pieces_from_status(s)
            lt_complete = _torrent_is_complete(lt, s)

            if status_str == "checking":
                status_str = "verifying" if lt_complete else "queued"

            lt_error = str(getattr(s, "error", "") or "").strip()
            if not lt_error:
                try:
                    if int(getattr(s, "errc", 0) or 0) != 0:
                        lt_error = str(s.errc)
                except (AttributeError, TypeError, ValueError):
                    pass

            rec = self._paused.get(h.download_id)
            err: str | None = None
            hint: str | None = None

            if h.download_id in self._user_paused or (rec and rec.user_paused):
                status_str = "paused"
            elif lt_error:
                status_str = "error"
                err = _friendly_transfer_error(lt_error)
                hint = _transfer_error_hint(err)
                if rec:
                    rec.error = err
            elif rec and rec.error and status_str not in ("done", "seeding"):
                status_str = "error"
                err = rec.error
                hint = _transfer_error_hint(err)
            elif lt_complete and status_str in ("done", "seeding"):
                status_str = "done"
            elif status_str in ("done", "seeding") and not lt_complete:
                status_str = "downloading"

            out = DownloadStatus(
                id=h.download_id,
                game_id=h.game_id,
                game_name=h.game_name,
                peer_id=h.peer_id,
                peer_name=h.peer_name,
                status=status_str,
                progress=progress,
                speed_bytes_sec=0 if status_str == "paused" else int(s.download_rate),
                downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes,
                num_peers=int(s.num_peers),
                pieces_total=pieces_total,
                pieces_missing=pieces_missing,
                bytes_remaining=bytes_remaining,
                error=err,
                error_hint=hint,
                dest_path=str(h.dest_path),
            )
            if rec:
                self._sync_rec_from_status(rec, out)
            return out
        except Exception as exc:
            err = _friendly_transfer_error(str(exc))
            hint = _transfer_error_hint(err)
            rec = self._paused.get(h.download_id)
            if rec:
                rec.error = err
            out = DownloadStatus(
                id=h.download_id,
                game_id=h.game_id,
                game_name=h.game_name,
                peer_id=h.peer_id,
                peer_name=h.peer_name,
                status="error",
                progress=rec.progress if rec else 0.0,
                speed_bytes_sec=0,
                downloaded_bytes=rec.downloaded_bytes if rec else 0,
                total_bytes=rec.total_bytes if rec else 0,
                num_peers=0,
                bytes_remaining=max(
                    0,
                    (rec.total_bytes if rec else 0) - (rec.downloaded_bytes if rec else 0),
                ),
                error=err,
                error_hint=hint,
                dest_path=str(h.dest_path),
            )
            return out

    def _save_state(self) -> None:
        path = self._cfg.downloads_state_path
        records = list(self._paused.values())
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps([r.__dict__ for r in records], indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("Could not save download state: %s", exc)

    def _load_state(self) -> None:
        path = self._cfg.downloads_state_path
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            field_names = {f.name for f in fields(_PersistedRecord)}
            for item in data:
                filtered = {k: v for k, v in item.items() if k in field_names}
                rec = _PersistedRecord(**filtered)
                self._paused[rec.download_id] = rec
                if rec.user_paused:
                    self._user_paused.add(rec.download_id)
            if self._paused:
                log.info("Loaded %d download record(s) from disk", len(self._paused))
        except Exception as exc:
            log.warning("Could not load download state: %s", exc)

    def _progress_payload(self, status: DownloadStatus) -> dict:
        return {
            "id": status.id,
            "game_id": status.game_id,
            "progress": status.progress,
            "speed_bytes_sec": status.speed_bytes_sec,
            "downloaded_bytes": status.downloaded_bytes,
            "total_bytes": status.total_bytes,
            "num_peers": status.num_peers,
            "status": status.status,
            "pieces_total": status.pieces_total,
            "pieces_missing": status.pieces_missing,
            "bytes_remaining": status.bytes_remaining,
            "error": status.error,
            "error_hint": status.error_hint,
        }

    def _track_progress(self, download_id: str, downloaded_bytes: int) -> None:
        now = time.monotonic()
        prev = self._last_downloaded.get(download_id)
        if prev is None:
            self._last_progress_at[download_id] = now
            self._last_downloaded[download_id] = downloaded_bytes
        elif downloaded_bytes > prev:
            self._last_progress_at[download_id] = now
            self._last_downloaded[download_id] = downloaded_bytes

    def _nudge_stalled_download(self, h: _Handle, status: DownloadStatus) -> None:
        """Reconnect to host when near-complete; error if stuck too long."""
        if status.status not in ("downloading", "queued", "verifying"):
            return
        remaining = status.bytes_remaining
        if remaining <= 0 or remaining > _STALL_REMAINING_MAX:
            return

        did = h.download_id
        now = time.monotonic()
        self._track_progress(did, status.downloaded_bytes)

        last_at = self._last_progress_at.get(did, now)
        stall_limit = 180.0 if remaining < 1024 * 1024 else _STALL_ERROR_AFTER
        if now - last_at >= stall_limit:
            rec = self._paused.get(did)
            if rec and not rec.error:
                if status.num_peers > 0 and status.pieces_missing > 0:
                    rec.error = (
                        "Host liefert letzte Daten nicht – auf dem Host Torrent neu "
                        "erstellen (Metadaten geändert?). „Erneut“ versuchen."
                    )
                else:
                    rec.error = (
                        "Download hängt bei den letzten Daten – "
                        "Host erreichbar und online? „Erneut“ versuchen."
                    )
                self._save_state()
            return

        rec = self._paused.get(did)
        if rec and rec.peer_address:
            if now - self._last_nudge_at.get(did, 0) >= _STALL_NUDGE_INTERVAL:
                self._last_nudge_at[did] = now
                try:
                    h.handle.connect_peer((rec.peer_address, self._cfg.torrent_port))
                    log.debug(
                        "Re-connect peer for %s (%s remaining)",
                        did,
                        remaining,
                    )
                except Exception as exc:
                    log.warning("Stall nudge connect_peer failed for %s: %s", did, exc)

            if now - self._last_reannounce_at.get(did, 0) >= _STALL_REANNOUNCE_INTERVAL:
                self._last_reannounce_at[did] = now
                try:
                    h.handle.force_reannounce()
                    log.debug("force_reannounce for %s", did)
                except Exception as exc:
                    log.warning("force_reannounce failed for %s: %s", did, exc)

        if now - last_at >= _STALL_RECHECK_AFTER and did not in self._recheck_done:
            self._recheck_done.add(did)
            try:
                h.handle.force_recheck()
                log.info("force_recheck for stalled download %s", did)
            except Exception as exc:
                log.warning("force_recheck failed for %s: %s", did, exc)

    async def _maybe_upgrade_from_peer(self, h: _Handle) -> bool:
        """If host published a new info_hash, hot-swap torrent without deleting files."""
        from deckdrop.api import state as app_state

        rec = self._paused.get(h.download_id)
        if not rec or rec.user_paused or h.download_id in self._user_paused:
            return False

        now = time.monotonic()
        if now - self._last_magnet_check_at.get(h.download_id, 0) < _MAGNET_CHECK_INTERVAL:
            return False
        self._last_magnet_check_at[h.download_id] = now

        try:
            s = app_state.get()
        except RuntimeError:
            return False

        peer = s.peer_registry.get(h.peer_id)
        if not peer or not peer.online:
            return False

        remote_game = next((g for g in peer.games if g.get("id") == h.game_id), None)
        if not remote_game or not remote_game.get("has_torrent"):
            return False

        remote_hash = (remote_game.get("info_hash") or "").lower()
        if not remote_hash or remote_hash == rec.info_hash:
            return False

        try:
            magnet, info_hash = await asyncio.to_thread(
                _fetch_peer_magnet,
                peer.address,
                peer.port,
                h.game_id,
            )
        except Exception as exc:
            log.warning(
                "Could not fetch magnet for upgrade %s: %s",
                h.download_id,
                exc,
            )
            return False

        if not self.upgrade_download(h.download_id, magnet, info_hash):
            return False

        await broadcast(
            "download_torrent_upgraded",
            {
                "id": h.download_id,
                "game_id": h.game_id,
                "game_name": h.game_name,
            },
        )
        return True

    async def _finalize_download(self, h: _Handle, status: DownloadStatus) -> None:
        if status.id in self._completed_ids:
            return
        self._completed_ids.add(status.id)
        self._register_downloaded_game(h)
        await broadcast(
            "download_complete",
            {
                "id": status.id,
                "game_id": status.game_id,
                "game_name": status.game_name,
                "peer_name": h.peer_name,
                "downloaded_bytes": status.downloaded_bytes,
                "total_bytes": status.total_bytes,
            },
        )
        if self._library:
            self._library.reload(
                self._cfg,
                exclude_paths=self.incomplete_download_dest_paths(),
            )

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            done_ids: list[str] = []

            for h in list(self._handles.values()):
                if await self._maybe_upgrade_from_peer(h):
                    h = self._handles.get(h.download_id)
                    if not h:
                        continue
                status = self._build_status(h)

                lt = _lt()
                is_complete = _torrent_is_complete(lt, _torrent_status(h.handle))

                if is_complete and status.status != "error":
                    await self._finalize_download(h, status)
                    if self._cfg.seed_after_download:
                        self._promote_download_to_seed(h)
                    done_ids.append(status.id)
                    continue

                self._nudge_stalled_download(h, status)
                await broadcast("download_progress", self._progress_payload(status))

                if status.status == "error":
                    rec = self._paused.get(h.download_id)
                    if rec:
                        rec.error = status.error or rec.error
                        self._save_state()
                    try:
                        h.handle.pause()
                    except Exception:
                        pass
                    await broadcast(
                        "download_error",
                        {
                            "id": status.id,
                            "game_id": status.game_id,
                            "error": status.error or "Unbekannter Übertragungsfehler.",
                            "error_hint": status.error_hint,
                            "status": "error",
                            "progress": status.progress,
                            "downloaded_bytes": status.downloaded_bytes,
                            "total_bytes": status.total_bytes,
                            "pieces_total": status.pieces_total,
                            "pieces_missing": status.pieces_missing,
                            "bytes_remaining": status.bytes_remaining,
                        },
                    )

            for did in done_ids:
                self._paused.pop(did, None)
                h = self._handles.pop(did, None)
                self._last_downloaded.pop(did, None)
                self._last_progress_at.pop(did, None)
                self._last_nudge_at.pop(did, None)
                self._last_reannounce_at.pop(did, None)
                self._last_magnet_check_at.pop(did, None)
                self._recheck_done.discard(did)
                if h:
                    promoted = (
                        self._cfg.seed_after_download
                        and self._seed_handles.get(h.game_id) is h.handle
                    )
                    if not promoted:
                        try:
                            self._session.remove_torrent(h.handle)
                        except Exception:
                            pass
                self._save_state()
