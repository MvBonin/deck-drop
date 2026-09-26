"""
Fast-resume storage for downloads.

Without resume data libtorrent has to re-fetch the metadata from the host and
re-hash every byte already on disk after each restart – for a 160 GB game on an
SD card that takes forever and starts over on every launch. Saving libtorrent's
resume blob (including the info dict) makes a restart continue in seconds.

libtorrent is optional and its API differs between 1.x and 2.x, so every helper
that touches it takes the module as first argument (like ``_map_torrent_state``
in transfer.py) and degrades to ``None``/``0`` instead of raising.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_RESUME_SUFFIX = ".resume"
_TORRENT_SUFFIX = ".torrent"


def _slug(info_hash: str) -> str:
    """Info hash embedded in the filename – stale blobs simply aren't found."""
    return (info_hash or "unknown").lower()


class ResumeStore:
    """Reads/writes resume + metadata blobs. Never raises on I/O problems."""

    def __init__(self, base_dir: Path) -> None:
        self._dir = Path(base_dir)

    @property
    def directory(self) -> Path:
        return self._dir

    def resume_path(self, download_id: str, info_hash: str) -> Path:
        return self._dir / f"{download_id}-{_slug(info_hash)}{_RESUME_SUFFIX}"

    def metadata_path(self, download_id: str, info_hash: str) -> Path:
        return self._dir / f"{download_id}-{_slug(info_hash)}{_TORRENT_SUFFIX}"

    def save_resume(self, download_id: str, info_hash: str, blob: bytes) -> bool:
        return self._write(self.resume_path(download_id, info_hash), blob)

    def load_resume(self, download_id: str, info_hash: str) -> bytes | None:
        return self._read(self.resume_path(download_id, info_hash))

    def save_metadata(self, download_id: str, info_hash: str, blob: bytes) -> bool:
        return self._write(self.metadata_path(download_id, info_hash), blob)

    def find_metadata(self, download_id: str, info_hash: str) -> Path | None:
        path = self.metadata_path(download_id, info_hash)
        return path if path.is_file() else None

    def has_metadata(self, download_id: str, info_hash: str) -> bool:
        return self.find_metadata(download_id, info_hash) is not None

    def discard(self, download_id: str) -> None:
        """Drop every blob belonging to a download (any info hash)."""
        if not self._dir.is_dir():
            return
        for path in self._dir.glob(f"{download_id}-*"):
            try:
                path.unlink()
            except OSError as exc:
                log.warning("Could not delete %s: %s", path, exc)

    def drop_resume(self, download_id: str, info_hash: str) -> None:
        try:
            self.resume_path(download_id, info_hash).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("Could not delete resume blob for %s: %s", download_id, exc)

    # -- internal --

    def _write(self, path: Path, blob: bytes) -> bool:
        if not blob:
            return False
        tmp = path.with_name(path.name + ".tmp")
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(blob)
            os.replace(tmp, path)
            return True
        except OSError as exc:
            log.warning("Could not write %s: %s", path, exc)
            try:
                tmp.unlink()
            except OSError:
                pass
            return False

    def _read(self, path: Path) -> bytes | None:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            log.warning("Could not read %s: %s", path, exc)
            return None
        return data or None


# -- libtorrent-facing helpers (lt module passed in, never imported here) --


def _or_attrs(obj: object, names: tuple[str, ...]) -> int:
    value = 0
    for name in names:
        bit = getattr(obj, name, None)
        if bit is None:
            continue
        try:
            value |= int(bit)
        except (TypeError, ValueError):
            continue
    return value


def resume_flags(lt: object) -> int:
    """save_resume_data flags; save_info_dict keeps metadata inside the blob."""
    handle_cls = getattr(lt, "torrent_handle", None)
    if handle_cls is None:
        return 0
    return _or_attrs(handle_cls, ("save_info_dict", "flush_disk_cache"))


def alert_mask(lt: object) -> int:
    """Alert categories needed for resume data, metadata and errors."""
    category = getattr(lt, "alert_category", None)
    mask = _or_attrs(category, ("status", "storage", "error")) if category is not None else 0
    if mask:
        return mask
    alert = getattr(lt, "alert", None)
    legacy = getattr(alert, "category_t", None) if alert is not None else None
    if legacy is None:
        return 0
    return _or_attrs(
        legacy,
        ("status_notification", "storage_notification", "error_notification"),
    )


def encode_resume_alert(lt: object, alert: object) -> bytes | None:
    """Serialize a save_resume_data_alert (2.x params, 1.x resume_data)."""
    write_buf = getattr(lt, "write_resume_data_buf", None)
    params = getattr(alert, "params", None)
    if write_buf is not None and params is not None:
        try:
            return bytes(write_buf(params))
        except Exception as exc:  # pragma: no cover - exotic builds
            log.debug("write_resume_data_buf failed: %s", exc)
    raw = getattr(alert, "resume_data", None)
    bencode = getattr(lt, "bencode", None)
    if raw is not None and bencode is not None:
        try:
            return bytes(bencode(raw))
        except Exception as exc:  # pragma: no cover - exotic builds
            log.debug("bencode(resume_data) failed: %s", exc)
    return None


def clear_startup_flags(lt: object, params: object) -> None:
    """Drop flags that would bring a restored torrent back paused or as a seed."""
    flags_enum = getattr(lt, "torrent_flags", None)
    if flags_enum is None:
        return
    unwanted = _or_attrs(flags_enum, ("paused", "stop_when_ready", "upload_mode", "seed_mode"))
    if not unwanted:
        return
    try:
        params.flags = int(params.flags) & ~unwanted
    except (AttributeError, TypeError, ValueError) as exc:
        log.debug("Could not clear startup flags: %s", exc)


def params_info_hash(params: object) -> str:
    """Best-effort info hash of add_torrent_params ('' when unavailable)."""
    hashes = getattr(params, "info_hashes", None)
    v1 = getattr(hashes, "v1", None) if hashes is not None else None
    for candidate in (v1, getattr(params, "info_hash", None)):
        if candidate is None:
            continue
        try:
            text = str(candidate).lower().strip()
        except Exception:  # pragma: no cover - exotic builds
            continue
        if text and set(text) != {"0"}:
            return text
    return ""


def decode_resume_params(lt: object, blob: bytes, save_path: str) -> object | None:
    """Turn a stored resume blob into add_torrent_params."""
    read = getattr(lt, "read_resume_data", None)
    if read is None:
        return None
    try:
        params = read(blob)
    except Exception as exc:
        log.warning("Resume data unreadable, falling back: %s", exc)
        return None
    try:
        params.save_path = save_path
    except (AttributeError, TypeError) as exc:  # pragma: no cover - exotic builds
        log.warning("Could not set save_path on resume params: %s", exc)
        return None
    clear_startup_flags(lt, params)
    return params


def params_from_torrent_file(
    lt: object,
    path: Path,
    save_path: str,
    expect_info_hash: str = "",
    folder_name: str | None = None,
) -> object | None:
    """Build add_torrent_params from a cached .torrent (metadata, no bitmap).

    ``folder_name`` renames the torrent's top-level folder to the receiver's
    destination folder name (see core.torrent.retarget_root) – the host's
    folder name and the local dest folder can differ.
    """
    try:
        info = lt.torrent_info(str(path))  # type: ignore[attr-defined]
        if folder_name:
            from deckdrop.core.torrent import retarget_root

            retarget_root(lt, info, folder_name)
        params = lt.add_torrent_params()  # type: ignore[attr-defined]
        params.ti = info
        params.save_path = save_path
    except Exception as exc:
        log.warning("Cached torrent %s unusable: %s", path, exc)
        return None
    if expect_info_hash:
        actual = params_info_hash(params) or _info_hash_from_torrent_info(info)
        if actual and actual != expect_info_hash.lower():
            log.info("Cached torrent %s has a different info hash – ignored", path)
            return None
    clear_startup_flags(lt, params)
    return params


def _info_hash_from_torrent_info(info: object) -> str:
    getter = getattr(info, "info_hashes", None)
    try:
        if getter is not None:
            return str(getter().v1).lower()
        return str(info.info_hash()).lower()  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - exotic builds
        return ""


def torrent_bytes_from_handle(lt: object, handle: object) -> bytes | None:
    """Bencoded .torrent for a handle whose metadata has arrived."""
    info = None
    for name in ("torrent_file", "get_torrent_info"):
        getter = getattr(handle, name, None)
        if getter is None:
            continue
        try:
            info = getter()
        except Exception:  # pragma: no cover - exotic builds
            info = None
        if info is not None:
            break
    if info is None:
        return None
    try:
        return bytes(lt.bencode(lt.create_torrent(info).generate()))  # type: ignore[attr-defined]
    except Exception as exc:
        log.debug("Could not serialize torrent metadata: %s", exc)
        return None
