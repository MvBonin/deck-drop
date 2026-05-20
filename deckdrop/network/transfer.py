"""
libtorrent-based download manager. LAN-only.

libtorrent is optional. All methods raise RuntimeError if not installed.
"""

from __future__ import annotations

import asyncio
import json
import logging
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
    status: str  # queued | downloading | seeding | done | error | paused
    progress: float  # 0.0–1.0
    speed_bytes_sec: int
    downloaded_bytes: int
    total_bytes: int
    num_peers: int
    error: str | None = None
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


def _map_torrent_state(lt: object, state_int: int) -> str:
    """Map libtorrent 2.x state enum (starts at 1, not 0)."""
    ts = lt.torrent_status
    mapping = {
        int(ts.checking_files): "queued",
        int(ts.downloading_metadata): "queued",
        int(ts.downloading): "downloading",
        int(ts.finished): "done",
        int(ts.seeding): "seeding",
        int(ts.allocating): "queued",
        int(ts.checking_resume_data): "queued",
    }
    return mapping.get(state_int, "downloading")


def _progress_from_status(s: object) -> float:
    """Prefer byte ratio; libtorrent's progress can lag or jump."""
    total = int(s.total_wanted)
    done = int(s.total_done)
    if total > 0:
        return min(1.0, done / total)
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
        if cfg.max_upload_speed or cfg.max_download_speed:
            self.apply_rate_limits()
        self._load_state()

    def set_library(self, library: object) -> None:
        self._library = library

    def apply_rate_limits(self) -> None:
        """Apply upload/download speed limits from config to the libtorrent session."""
        _lt()  # verify installed
        self._session.apply_settings(
            {
                "upload_rate_limit": self._cfg.max_upload_speed,
                "download_rate_limit": self._cfg.max_download_speed,
            }
        )
        log.debug(
            "Rate limits set: up=%s down=%s bytes/s",
            self._cfg.max_upload_speed,
            self._cfg.max_download_speed,
        )

    def start_polling(self) -> None:
        """Start the async background loop. Call after the event loop is running."""
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop())

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

        self._handles[download_id] = _Handle(
            download_id=download_id,
            game_id=game_id,
            game_name=game_name,
            peer_id=peer_id,
            peer_name=peer_name,
            handle=handle,
            dest_path=dest_path,
        )
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

    def remove_download(self, download_id: str, *, delete_files: bool = False) -> bool:
        rec = self._paused.pop(download_id, None)
        h = self._handles.pop(download_id, None)
        self._user_paused.discard(download_id)
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

    def all_statuses(self) -> list[DownloadStatus]:
        seen: set[str] = set()
        result: list[DownloadStatus] = []
        for h in self._handles.values():
            result.append(self._build_status(h))
            seen.add(h.download_id)
        for did, rec in self._paused.items():
            if did not in seen:
                result.append(self._paused_status(rec))
        return result

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
        else:
            status = "paused"
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
            error=rec.error,
            dest_path=rec.dest_path,
        )

    def _build_status(self, h: _Handle) -> DownloadStatus:
        _lt()  # verify installed
        try:
            s = h.handle.status()
            lt = _lt()
            state_int = int(s.state)
            status_str = _map_torrent_state(lt, state_int)
            progress = _progress_from_status(s)
            total_bytes = int(s.total_wanted)
            downloaded_bytes = int(s.total_done)
            # Only treat as complete when libtorrent says finished/seeding
            if status_str not in ("done", "seeding") and progress >= 0.999:
                status_str = "downloading"

            rec = self._paused.get(h.download_id)
            if h.download_id in self._user_paused or (rec and rec.user_paused):
                status_str = "paused"
            elif rec and rec.error:
                status_str = "error"

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
                error=rec.error if rec else None,
                dest_path=str(h.dest_path),
            )
            if rec:
                self._sync_rec_from_status(rec, out)
            return out
        except Exception as exc:
            err = _friendly_transfer_error(str(exc))
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
                error=err,
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

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            done_ids: list[str] = []

            for h in list(self._handles.values()):
                status = self._build_status(h)
                await broadcast(
                    "download_progress",
                    {
                        "id": status.id,
                        "game_id": status.game_id,
                        "progress": status.progress,
                        "speed_bytes_sec": status.speed_bytes_sec,
                        "downloaded_bytes": status.downloaded_bytes,
                        "total_bytes": status.total_bytes,
                        "num_peers": status.num_peers,
                        "status": status.status,
                    },
                )

                if status.status in ("done", "seeding") and (
                    status.total_bytes == 0 or status.downloaded_bytes >= status.total_bytes
                ):
                    self._register_downloaded_game(h)
                    await broadcast(
                        "download_complete",
                        {
                            "id": status.id,
                            "game_id": status.game_id,
                            "game_name": status.game_name,
                            "peer_name": h.peer_name,
                        },
                    )
                    if self._library:
                        self._library.reload(self._cfg)
                    if not self._cfg.seed_after_download:
                        done_ids.append(status.id)

                elif status.status == "error":
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
                            "status": "error",
                            "progress": status.progress,
                            "downloaded_bytes": status.downloaded_bytes,
                            "total_bytes": status.total_bytes,
                        },
                    )

            for did in done_ids:
                self._paused.pop(did, None)
                h = self._handles.get(did)
                if h:
                    self._session.remove_torrent(h.handle)
                    del self._handles[did]
                self._save_state()
