"""
libtorrent-based download manager. LAN-only.

libtorrent is optional. All methods raise RuntimeError if not installed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
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


def _lt():
    try:
        import libtorrent as lt

        return lt
    except ImportError:
        raise RuntimeError("libtorrent is not installed. Install it with: pip install libtorrent")


_LT_STATE_MAP = {
    # libtorrent torrent_status.state_t values
    0: "queued",  # checking_files
    1: "queued",  # downloading_metadata
    2: "downloading",  # downloading
    3: "done",  # finished
    4: "seeding",  # seeding
    5: "queued",  # allocating
    6: "queued",  # checking_resume_data
}


class TransferManager:
    def __init__(self, cfg: Config) -> None:
        from deckdrop.core.torrent import lan_session

        self._cfg = cfg
        self._session = lan_session(cfg.torrent_port)
        self._handles: dict[str, _Handle] = {}  # download_id → _Handle
        self._paused: dict[str, _PersistedRecord] = {}  # survived restarts, no active handle
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

    def start_download(
        self,
        game_id: str,
        game_name: str,
        magnet: str,
        peer_id: str,
        peer_name: str,
        peer_address: str,
        dest_path: Path,
    ) -> str:
        lt = _lt()
        download_id = secrets.token_hex(4)
        dest_path.mkdir(parents=True, exist_ok=True)

        params = lt.add_torrent_params()
        params.save_path = str(dest_path)
        lt.parse_magnet_uri(magnet, params)
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
        )
        self._save_state()
        log.info("Download started: %s (%s) from %s", game_name, download_id, peer_address)
        return download_id

    def cancel(self, download_id: str) -> bool:
        h = self._handles.get(download_id)
        if h:
            self._session.remove_torrent(h.handle)
            del self._handles[download_id]
        self._paused.pop(download_id, None)
        self._save_state()
        if h:
            log.info("Download cancelled: %s", download_id)
            return True
        # Also allow cancelling a paused (persisted-only) record
        return download_id in self._paused or h is not None

    def get_status(self, download_id: str) -> DownloadStatus | None:
        h = self._handles.get(download_id)
        if h:
            return self._build_status(h)
        rec = self._paused.get(download_id)
        if rec:
            return self._paused_status(rec)
        return None

    def all_statuses(self) -> list[DownloadStatus]:
        active = [self._build_status(h) for h in self._handles.values()]
        paused = [
            self._paused_status(rec)
            for did, rec in self._paused.items()
            if did not in self._handles
        ]
        return active + paused

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

    def _paused_status(self, rec: _PersistedRecord) -> DownloadStatus:
        return DownloadStatus(
            id=rec.download_id,
            game_id=rec.game_id,
            game_name=rec.game_name,
            peer_id=rec.peer_id,
            peer_name=rec.peer_name,
            status="paused",
            progress=0.0,
            speed_bytes_sec=0,
            downloaded_bytes=0,
            total_bytes=0,
            num_peers=0,
        )

    def _build_status(self, h: _Handle) -> DownloadStatus:
        _lt()  # verify installed
        try:
            s = h.handle.status()
            state_int = int(s.state)
            status_str = _LT_STATE_MAP.get(state_int, "downloading")
            return DownloadStatus(
                id=h.download_id,
                game_id=h.game_id,
                game_name=h.game_name,
                peer_id=h.peer_id,
                peer_name=h.peer_name,
                status=status_str,
                progress=s.progress,
                speed_bytes_sec=s.download_rate,
                downloaded_bytes=s.total_done,
                total_bytes=s.total_wanted,
                num_peers=s.num_peers,
            )
        except Exception as exc:
            return DownloadStatus(
                id=h.download_id,
                game_id=h.game_id,
                game_name=h.game_name,
                peer_id=h.peer_id,
                peer_name=h.peer_name,
                status="error",
                progress=0.0,
                speed_bytes_sec=0,
                downloaded_bytes=0,
                total_bytes=0,
                num_peers=0,
                error=str(exc),
            )

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
            for item in data:
                rec = _PersistedRecord(**item)
                self._paused[rec.download_id] = rec
            if self._paused:
                log.info("Loaded %d paused download(s) from disk", len(self._paused))
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

                if status.status in ("done", "seeding"):
                    await broadcast(
                        "download_complete",
                        {"id": status.id, "game_id": status.game_id, "game_name": status.game_name},
                    )
                    if self._library:
                        self._library.reload(self._cfg)
                    if not self._cfg.seed_after_download:
                        done_ids.append(status.id)

                elif status.status == "error":
                    await broadcast(
                        "download_error",
                        {"id": status.id, "game_id": status.game_id, "error": status.error},
                    )
                    done_ids.append(status.id)

            for did in done_ids:
                self._paused.pop(did, None)
                h = self._handles.get(did)
                if h:
                    self._session.remove_torrent(h.handle)
                    del self._handles[did]
                self._save_state()
