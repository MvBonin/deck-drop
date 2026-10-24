"""DeckDrop self-update: check GitHub on request, find newer versions on LAN
peers, install a new AppImage in place – only after verifying a signed
release manifest.

Trust model: a release is described by `release.json` (version + sha256 of
every file), signed with Ed25519 (`release.json.sig`). The public keys are
built into the app (`release_key.PUBLIC_KEYS`). The AppImage is only ever
replaced if the manifest signature is valid, the manifest version is newer
than the running one and the downloaded file's sha256 matches the manifest –
so the download source (GitHub or any LAN peer) doesn't need to be trusted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deckdrop import __version__
from deckdrop.core import ed25519, release_key
from deckdrop.core.i18n import t

log = logging.getLogger(__name__)

GITHUB_REPO = "MvBonin/deck-drop"
GITHUB_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
MANIFEST_NAME = "release.json"
SIGNATURE_NAME = "release.json.sig"
_APPIMAGE_RE = re.compile(r"^DeckDrop-\d+(\.\d+)*-x86_64\.AppImage$")
_USER_AGENT = f"DeckDrop/{__version__} (+https://github.com/{GITHUB_REPO})"
_CHUNK = 1024 * 1024


class UpdateError(Exception):
    """Shown to the user as is (German)."""


# -- versions --------------------------------------------------------------


def parse_version(v: str) -> tuple[int, ...]:
    m = re.match(r"^v?(\d+(?:\.\d+)*)", (v or "").strip())
    if not m:
        raise ValueError(t("update.invalid_version", version=repr(v)))
    return tuple(int(x) for x in m.group(1).split("."))


def is_newer(candidate: str, current: str = __version__) -> bool:
    try:
        a, b = parse_version(candidate), parse_version(current)
    except ValueError:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


# -- signed manifest -------------------------------------------------------


def verify_manifest(manifest_bytes: bytes, signature: bytes, keys: list[str] | None = None) -> dict:
    """Parse release.json after checking its Ed25519 signature.

    `signature` may be raw 64 bytes or hex text. Raises UpdateError.
    """
    keys = release_key.PUBLIC_KEYS if keys is None else keys
    if not keys:
        raise UpdateError(t("update.no_key"))
    sig = signature.strip()
    if len(sig) != 64:
        try:
            sig = bytes.fromhex(sig.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            raise UpdateError(t("update.bad_signature")) from None
    if not any(ed25519.verify(bytes.fromhex(k), manifest_bytes, sig) for k in keys):
        raise UpdateError(t("update.foreign_signature"))
    try:
        manifest = json.loads(manifest_bytes)
    except ValueError:
        raise UpdateError("Manifest unlesbar.") from None
    if manifest.get("app") != "deckdrop":
        raise UpdateError(t("update.foreign_manifest"))
    try:
        parse_version(str(manifest.get("version", "")))
    except ValueError:
        raise UpdateError(t("update.manifest_no_version")) from None
    if not appimage_entry(manifest):
        raise UpdateError(t("update.manifest_no_appimage"))
    return manifest


def appimage_entry(manifest: dict) -> dict | None:
    for f in manifest.get("files") or []:
        if isinstance(f, dict) and _APPIMAGE_RE.match(str(f.get("name", ""))):
            if re.fullmatch(r"[0-9a-f]{64}", str(f.get("sha256", ""))):
                return f
    return None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


# -- environment -----------------------------------------------------------


def appimage_path() -> Path | None:
    p = os.getenv("APPIMAGE")
    return Path(p) if p else None


def install_type() -> str:
    """How this process runs – from the environment only.

    Deliberately not `service.detect_install_type()`: without $APPIMAGE that
    searches the whole home directory for an AppImage (rglob), which took
    seconds on big homes and stalled every GET /api/selfupdate.
    """
    import sys

    if os.getenv("FLATPAK_ID"):
        return "flatpak"
    if os.getenv("APPIMAGE"):
        return "appimage"
    if getattr(sys, "frozen", False):
        return "windows" if sys.platform == "win32" else "appimage"
    return "pipx"


def can_self_update() -> tuple[bool, str]:
    path = appimage_path()
    if path is None or not path.is_file():
        return False, t("update.appimage_only")
    if not os.access(path.parent, os.W_OK) or not os.access(path, os.W_OK):
        return False, t("update.no_write_access", path=path)
    return True, ""


def release_dir() -> Path:
    from deckdrop.core import config as cfg_mod

    return Path(cfg_mod.CONFIG_PATH).parent / "release"


def backup_path() -> Path | None:
    path = appimage_path()
    return path.with_name(path.name + ".bak") if path else None


# -- state -----------------------------------------------------------------


@dataclass
class _State:
    github: dict | None = None  # last check result
    install: dict = field(default_factory=lambda: {"state": "idle"})
    restart_requested: bool = False
    offer_cache: dict | None = None  # (size, mtime) -> sha256 of own AppImage
    verified_offers: dict = field(default_factory=dict)  # manifest sha → manifest|None


_state = _State()
_lock = threading.Lock()


def reset_state() -> None:  # tests
    global _state
    _state = _State()


def status(peer_registry: object | None = None) -> dict[str, Any]:
    ok, reason = can_self_update()
    bak = backup_path()
    return {
        "current": __version__,
        "install_type": install_type(),
        "can_self_update": ok,
        "self_update_hint": reason,
        "github": public_github(),
        "lan": lan_offers(peer_registry) if peer_registry is not None else [],
        "install": dict(_state.install),
        "rollback_available": bool(bak and bak.is_file()),
        "signing_configured": bool(release_key.PUBLIC_KEYS),
    }


# -- GitHub ----------------------------------------------------------------


def check_github(client: Any | None = None) -> dict:
    """Ask GitHub for the latest release (only ever on user request)."""
    import httpx

    own = client is None
    client = client or httpx.Client(
        timeout=10.0, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
    )
    try:
        r = client.get(GITHUB_LATEST, headers={"Accept": "application/vnd.github+json"})
        if r.status_code == 404:
            result = {
                "ok": True,
                "latest": None,
                "newer": False,
                "error": None,
                "message": t("update.no_release"),
            }
            _state.github = result
            return result
        r.raise_for_status()
        rel = r.json()
        latest = str(rel.get("tag_name") or "").lstrip("v")
        assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets") or []}
        result: dict[str, Any] = {
            "ok": True,
            "latest": latest,
            "newer": is_newer(latest),
            "notes": rel.get("body") or "",
            "published_at": rel.get("published_at") or "",
            "html_url": rel.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases",
            "verified": False,
            "error": None,
        }
        if result["newer"]:
            try:
                if MANIFEST_NAME not in assets or SIGNATURE_NAME not in assets:
                    raise UpdateError(t("update.unsigned_release"))
                m_bytes = client.get(assets[MANIFEST_NAME]).content
                sig = client.get(assets[SIGNATURE_NAME]).content
                manifest = verify_manifest(m_bytes, sig)
                if str(manifest["version"]) != latest:
                    raise UpdateError(t("update.version_mismatch"))
                entry = appimage_entry(manifest)
                url = assets.get(entry["name"])
                if not url:
                    raise UpdateError("AppImage fehlt im Release.")
                result["verified"] = True
                result["_source"] = {
                    "kind": "github",
                    "url": url,
                    "manifest": m_bytes.decode("utf-8"),
                    "signature": sig.hex() if len(sig) == 64 else sig.decode().strip(),
                }
            except UpdateError as exc:
                result["error"] = str(exc)
        _state.github = result
        return result
    except Exception as exc:
        log.warning("Update check failed: %s", exc)
        result = {"ok": False, "error": t("update.github_unreachable", error=exc)}
        _state.github = result
        return result
    finally:
        if own:
            client.close()


def public_github() -> dict | None:
    g = _state.github
    return {k: v for k, v in g.items() if not k.startswith("_")} if g else None


# -- LAN -------------------------------------------------------------------


def local_offer() -> dict | None:
    """What this instance can hand to LAN peers: its running AppImage, if a
    stored signed manifest describes exactly this version and file."""
    path = appimage_path()
    rdir = release_dir()
    m_path, s_path = rdir / MANIFEST_NAME, rdir / SIGNATURE_NAME
    if path is None or not path.is_file() or not m_path.is_file() or not s_path.is_file():
        return None
    try:
        m_bytes, sig = m_path.read_bytes(), s_path.read_bytes()
        manifest = verify_manifest(m_bytes, sig)
    except (OSError, UpdateError):
        return None
    if str(manifest["version"]) != __version__:
        return None  # e.g. already replaced on disk, restart pending
    st = path.stat()
    key = (st.st_size, st.st_mtime_ns)
    cached = _state.offer_cache
    if not cached or cached.get("key") != key:
        cached = {"key": key, "sha256": _sha256_file(path)}
        _state.offer_cache = cached
    if cached["sha256"] != appimage_entry(manifest)["sha256"]:
        return None
    return {
        "version": __version__,
        "manifest": m_bytes.decode("utf-8"),
        "signature": sig.hex() if len(sig) == 64 else sig.decode().strip(),
        "size": st.st_size,
    }


def _verified_offer(offer: dict) -> dict | None:
    m_bytes = str(offer.get("manifest") or "").encode("utf-8")
    key = hashlib.sha256(m_bytes + str(offer.get("signature")).encode()).hexdigest()
    if key not in _state.verified_offers:
        try:
            manifest = verify_manifest(m_bytes, str(offer.get("signature") or "").encode())
        except UpdateError:
            manifest = None
        _state.verified_offers[key] = manifest
    return _state.verified_offers[key]


def lan_offers(peer_registry: object) -> list[dict]:
    """Newer, validly signed DeckDrop versions offered by online peers."""
    out = []
    for peer in getattr(peer_registry, "all", lambda: [])():
        offer = getattr(peer, "app_offer", None)
        if not isinstance(offer, dict):
            continue
        manifest = _verified_offer(offer)
        if not manifest or str(manifest["version"]) != str(offer.get("version")):
            continue
        if not is_newer(str(manifest["version"])):
            continue
        out.append(
            {
                "peer_id": peer.peer_id,
                "peer_name": peer.name,
                "version": str(manifest["version"]),
                "size": offer.get("size"),
            }
        )
    out.sort(key=lambda o: parse_version(o["version"]), reverse=True)
    return out


# -- install ---------------------------------------------------------------


def _set_install(**kw: Any) -> None:
    with _lock:
        _state.install = {**_state.install, **kw}
    try:  # live progress over the WebSocket (same loop torrent_prep uses)
        from deckdrop.core import torrent_prep

        torrent_prep._emit("selfupdate_progress", dict(_state.install))
    except Exception:
        pass


def start_install(source: dict, *, background: bool = True) -> None:
    """source: {"kind": "github"|"peer", "url", "manifest", "signature", "label"}"""
    ok, reason = can_self_update()
    if not ok:
        raise UpdateError(reason)
    if _state.install.get("state") in ("downloading", "verifying"):
        raise UpdateError(t("update.already_running"))
    manifest = verify_manifest(
        source["manifest"].encode("utf-8"), str(source["signature"]).encode()
    )
    if not is_newer(str(manifest["version"])):
        raise UpdateError(t("update.not_newer"))
    _set_install(
        state="downloading",
        progress=0.0,
        error=None,
        version=str(manifest["version"]),
        source=source.get("label", source["kind"]),
    )
    worker = threading.Thread(
        target=_install_worker, args=(source, manifest), daemon=True, name="selfupdate-install"
    )
    if background:
        worker.start()
    else:
        worker.run()


def _install_worker(source: dict, manifest: dict) -> None:
    import httpx

    path = appimage_path()
    assert path is not None
    entry = appimage_entry(manifest)
    part = path.with_name(".DeckDrop-update.part")
    try:
        h = hashlib.sha256()
        done = 0
        total = int(entry.get("size") or 0)
        with (
            httpx.Client(
                timeout=60.0, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
            ) as client,
            client.stream("GET", source["url"]) as r,
            part.open("wb") as fh,
        ):
            r.raise_for_status()
            total = total or int(r.headers.get("content-length") or 0)
            last = 0.0
            for chunk in r.iter_bytes(_CHUNK):
                fh.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if total and done > total:
                    raise UpdateError(t("update.too_large"))
                if total and time.monotonic() - last > 0.3:
                    last = time.monotonic()
                    _set_install(progress=done / total)
        _set_install(state="verifying", progress=1.0)
        if h.hexdigest() != entry["sha256"]:
            raise UpdateError(t("update.checksum_mismatch"))
        if total and done != total:
            raise UpdateError(t("update.incomplete"))
        os.chmod(part, 0o755)
        bak = backup_path()
        os.replace(path, bak)
        try:
            os.replace(part, path)
        except OSError:
            os.replace(bak, path)  # put the old one back
            raise
        rdir = release_dir()
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / MANIFEST_NAME).write_text(source["manifest"], encoding="utf-8")
        (rdir / SIGNATURE_NAME).write_text(str(source["signature"]), encoding="utf-8")
        _set_install(state="ready", error=None)
        log.info("DeckDrop %s installed at %s (backup %s)", manifest["version"], path, bak)
    except Exception as exc:
        log.warning("Self-update failed: %s", exc)
        part.unlink(missing_ok=True)
        _set_install(
            state="error",
            error=str(exc) if isinstance(exc, UpdateError) else f"Update fehlgeschlagen: {exc}",
        )


def rollback() -> None:
    path, bak = appimage_path(), backup_path()
    if path is None or bak is None or not bak.is_file():
        raise UpdateError("Keine vorherige Version vorhanden.")
    tmp = path.with_name(".DeckDrop-rollback.tmp")
    os.replace(path, tmp)
    os.replace(bak, path)
    os.replace(tmp, bak)
    # The stored manifest describes the version we just moved away from.
    for name in (MANIFEST_NAME, SIGNATURE_NAME):
        (release_dir() / name).unlink(missing_ok=True)
    _set_install(state="ready", error=None, version="vorherige")


# -- restart ---------------------------------------------------------------


def request_restart() -> None:
    _state.restart_requested = True


def restart_requested() -> bool:
    return _state.restart_requested


def exec_restart(argv: list[str]) -> None:
    """Replace this process with the (new) AppImage – same PID, so systemd
    and Steam keep tracking it. Called by main() after the server stopped."""
    path = appimage_path()
    env = {**os.environ, "DECKDROP_RESTARTED": "1"}
    if path is not None and path.is_file():
        from deckdrop.core.hostenv import host_env

        # The new AppImage brings its own libraries – don't pass ours on.
        os.execve(str(path), [str(path), *argv[1:]], host_env({"DECKDROP_RESTARTED": "1"}))
    import sys

    os.execve(sys.executable, [sys.executable, "-m", "deckdrop.main", *argv[1:]], env)
