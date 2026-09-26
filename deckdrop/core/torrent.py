"""
.torrent generation and libtorrent session factory.

libtorrent is an optional dependency. All public functions raise
RuntimeError with a clear message if it's not installed.

Phase 0 findings (libtorrent 2.1.1.0, pip wheel, verified 2026-09-26 in a
throwaway venv – see docs/plans/game-updates.md "Phase 0"):

- `lt.create_torrent(fs, piece_size)` builds hybrid v1+v2 torrents by default:
  `torrent_info.info_hashes().has_v2()` is True (has_v1() is also True).
  Confirmed. (The 2-arg constructor from `file_storage` is flagged
  DeprecationWarning in 2.1.1 but still works; a future libtorrent release may
  need the `torrent_creator`-based API instead.)
- Pad files are inserted between non-aligned files and flagged:
  `fs.file_flags(i) & lt.file_storage.flag_pad_file`. Confirmed.
- Every non-pad file starts on a piece boundary:
  `fs.file_offset(i) % ti.piece_length() == 0`. Confirmed (pad files
  themselves are not aligned at their end, only non-pad files start aligned).
- `ti.rename_file(i, new_path)` before `add_torrent` works (renames the
  in-memory `torrent_info`, `.pad` entries keep their own paths). Confirmed,
  though also flagged deprecated in 2.1.1 in favour of a `file_storage`-based
  rename; kept since it is still functional and 1.x-compatible.
- `handle.rename_file(i, path)` after a torrent has been added to a session
  works the same way. Confirmed.
- `ti.hash_for_piece(p)` returns the 20-byte v1 SHA1 for piece `p`;
  `ti.piece_size(p)` returns that piece's size (last piece may be shorter).
  Confirmed.
- `params.have_pieces = [bool, ...]` (on `add_torrent_params`) is accepted
  and read back unchanged. Confirmed – Phase 6 (`have_pieces` fast path) can
  go ahead.
- `ti.files()` is flagged deprecated in 2.1.1 (a newer file_storage accessor
  exists) but still returns a fully working `file_storage`; used as-is here
  since replacing it is out of scope for this phase.

If a future libtorrent build removes something above, prefer the documented
alternative in these notes and update this comment block with what changed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from deckdrop.core.integrity import iter_torrent_files

log = logging.getLogger(__name__)

_LT_MISSING = "libtorrent is not installed. Install it with: pip install libtorrent"


def _lt():
    try:
        import libtorrent as lt

        return lt
    except ImportError:
        raise RuntimeError(_LT_MISSING)


# LAN-only session settings – hardcoded, not user-configurable
_LAN_SETTINGS_CORE = {
    "enable_dht": False,
    "enable_lsd": True,  # Local Service Discovery via LAN multicast
    "enable_upnp": False,
    "enable_natpmp": False,
    "announce_to_all_trackers": False,
    "announce_to_all_tiers": False,
}
# Optional tuning; names differ across libtorrent builds (e.g. AppImage vs pip).
_LAN_SETTINGS_OPTIONAL = {
    "allow_multiple_connections_per_ip": True,
    "unchoke_slots_limit": 16,
}


def choose_piece_size(total_bytes: int) -> int:
    """Piece size rule (docs/plans/game-updates.md 3.1): smaller pieces = finer
    delta on updates, but not so small the torrent metadata gets huge."""
    gib = 1024**3
    if total_bytes < 2 * gib:
        return 1024 * 1024  # 1 MiB
    if total_bytes < 16 * gib:
        return 2 * 1024 * 1024  # 2 MiB
    return 4 * 1024 * 1024  # 4 MiB


def create_torrent_data(
    game_path: Path,
    files: list[str] | None = None,
    piece_size: int | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> bytes:
    """Create a .torrent file (as bytes) from a game directory.

    `files`, when given, are sorted POSIX relpaths (from the content manifest)
    to include instead of re-scanning the directory — used for update
    torrents so removed/local-only files never end up in the swarm. Piece
    size defaults to `choose_piece_size(total_bytes)`. File alignment (hybrid
    v1+v2, never v1_only) is mandatory for piece-level delta updates.
    """
    lt = _lt()
    if on_progress:
        on_progress(0.02)

    parent = game_path.parent
    if files is not None:
        file_paths = [game_path / rel for rel in files]
        file_paths = [p for p in file_paths if p.is_file()]
    else:
        file_paths = iter_torrent_files(game_path)
    if not file_paths:
        raise RuntimeError(f"No shareable files in {game_path}")

    total_bytes = sum(p.stat().st_size for p in file_paths)
    size = piece_size or choose_piece_size(total_bytes)

    fs = lt.file_storage()
    for file_path in file_paths:
        rel = file_path.relative_to(parent).as_posix()
        fs.add_file(rel, file_path.stat().st_size)
    t = lt.create_torrent(fs, size)
    t.set_comment(f"DeckDrop – {game_path.name}")

    num_pieces = max(int(t.num_pieces()), 1)

    def _piece_progress(piece_index: int) -> None:
        if on_progress:
            # Hashing dominates runtime; map to 5–95 %
            frac = min(1.0, (int(piece_index) + 1) / num_pieces)
            on_progress(0.05 + 0.9 * frac)

    try:
        lt.set_piece_hashes(t, str(parent), _piece_progress)  # type: ignore[misc]
    except TypeError:
        lt.set_piece_hashes(t, str(parent))  # type: ignore[misc]
        if on_progress:
            on_progress(0.5)

    if on_progress:
        on_progress(0.98)
    return lt.bencode(t.generate())


def retarget_root(lt: object, ti: object, folder_name: str) -> None:
    """Rename the torrent's top-level folder so files land in <save_path>/<folder_name>/...

    The host's torrent root is the host's own folder name, which can differ
    from the receiver's dest folder name. Renaming files does not change the
    info_hash (only the display path inside the torrent metadata), so this is
    safe to call on any torrent_info, on the host or the receiver.
    """
    fs = ti.files()
    for i in range(fs.num_files()):
        old = fs.file_path(i).replace("\\", "/")
        parts = old.split("/", 1)
        if parts[0] == folder_name:
            continue
        new_path = folder_name + ("/" + parts[1] if len(parts) > 1 else "")
        ti.rename_file(i, new_path)


class PieceCheckCancelled(Exception):
    """Raised by `build_have_pieces` when its `cancel` event is set."""


def build_have_pieces(
    lt: object,
    ti: object,
    root: Path,
    unchanged_rels: list[str] | set[str],
    changed_rels: list[str] | set[str],
    *,
    on_progress: Callable[[float], None] | None = None,
    cancel: object | None = None,  # threading.Event
) -> list[bool]:
    """Phase 6 "have_pieces" fast path (docs/plans/game-updates.md).

    Pieces of files in `unchanged_rels` are marked present without reading
    them (trusted: same hash in old/new manifest *and* the local tracker did
    not report them as changed/removed – callers must only pass files that
    meet both conditions). Pieces of files in `changed_rels` are verified by
    reading the local bytes and comparing their SHA1 against the piece hash
    from `ti`, so unchanged pieces *within* a changed file (e.g. a save-like
    append at the end) are still marked present. Pieces of any other file
    (added, or not present locally) are left `False`. Never raises for a
    single unreadable file – that file's pieces are just left `False`.

    `on_progress(fraction)` reports the share of to-be-read pieces checked so
    far; setting `cancel` stops the check with `PieceCheckCancelled`.
    """
    from deckdrop.core.content import pieces_for_file, safe_join

    unchanged = set(unchanged_rels)
    changed = set(changed_rels)
    piece_length = ti.piece_length()
    have = [False] * ti.num_pieces()

    to_read: list[tuple[str, Path, int, int, range]] = []
    fs = ti.files()
    for i in range(fs.num_files()):
        if fs.file_flags(i) & lt.file_storage.flag_pad_file:
            continue
        path = fs.file_path(i).replace("\\", "/")
        parts = path.split("/", 1)
        rel = parts[1] if len(parts) > 1 else parts[0]
        offset = fs.file_offset(i)
        size = fs.file_size(i)
        piece_range = pieces_for_file(offset, size, piece_length)
        if not piece_range:
            continue

        if rel in unchanged:
            for p in piece_range:
                have[p] = True
        elif rel in changed:
            local_path = safe_join(root, rel)
            if local_path is None or not local_path.is_file():
                continue
            to_read.append((rel, local_path, offset, size, piece_range))

    total = sum(len(r) for *_, r in to_read) or 1
    done = 0
    last_reported = -1.0

    def _tick() -> None:
        nonlocal done, last_reported
        if cancel is not None and cancel.is_set():
            raise PieceCheckCancelled()
        done += 1
        frac = done / total
        if on_progress is not None and (frac - last_reported >= 0.005 or done == total):
            last_reported = frac
            on_progress(frac)

    for rel, local_path, offset, size, piece_range in to_read:
        try:
            _verify_changed_file_pieces(ti, local_path, offset, size, piece_range, have, _tick)
        except OSError as exc:
            log.debug("have_pieces: could not read %s: %s", rel, exc)

    return have


def torrent_file_sizes(lt: object, ti: object) -> dict[str, int]:
    """rel (without the torrent's root folder) -> size for every non-pad file."""
    out: dict[str, int] = {}
    fs = ti.files()
    for i in range(fs.num_files()):
        if fs.file_flags(i) & lt.file_storage.flag_pad_file:
            continue
        path = fs.file_path(i).replace("\\", "/")
        parts = path.split("/", 1)
        rel = parts[1] if len(parts) > 1 else parts[0]
        out[rel] = fs.file_size(i)
    return out


def manifest_torrent_mismatches(
    lt: object,
    ti: object,
    files: dict[str, str],
    sizes: dict[str, int],
    ignore: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """Relpaths where a peer's manifest and its torrent disagree – no disk reads.

    libtorrent already verified every piece against the torrent, so the data
    on disk *is* the torrent's content. What it cannot vouch for is the
    manifest (blake2b per file) we adopt as the local baseline. Same file set
    and same sizes on both sides means the manifest describes this torrent;
    anything else is reported: files only in the manifest, files only in the
    torrent, and size differences (only where the manifest has a size).
    Torrent-only files matching the ignore patterns are fine: older torrents
    were built from the whole folder, the manifest never lists ignored files.
    """
    from deckdrop.core.content import is_ignored

    in_torrent = torrent_file_sizes(lt, ti)
    bad: set[str] = set()
    for rel in files:
        if rel not in in_torrent:
            bad.add(rel)
            continue
        expected = sizes.get(rel)
        if expected is not None and int(expected) != in_torrent[rel]:
            bad.add(rel)
    for rel in in_torrent:
        if rel not in files and not is_ignored(rel, ignore):
            bad.add(rel)
    return sorted(bad)


def _verify_changed_file_pieces(
    ti: object,
    local_path: Path,
    file_offset: int,
    file_size: int,
    piece_range: range,
    have: list[bool],
    tick: Callable[[], None] | None = None,
) -> None:
    import hashlib

    with open(local_path, "rb") as fh:
        for p in piece_range:
            if tick is not None:
                tick()
            piece_size = ti.piece_size(p)
            piece_start = p * ti.piece_length()
            file_pos = piece_start - file_offset
            to_read = max(0, min(piece_size, file_size - file_pos))
            fh.seek(max(file_pos, 0))
            data = fh.read(to_read) if to_read > 0 else b""
            if len(data) < piece_size:
                data = data + b"\x00" * (piece_size - len(data))
            if hashlib.sha1(data).digest() == ti.hash_for_piece(p):
                have[p] = True


def make_magnet(torrent_data: bytes) -> tuple[str, str]:
    """
    Parse torrent bytes and return (magnet_uri, info_hash_hex).
    No trackers are added – LAN-only via LSD.
    """
    lt = _lt()
    info = lt.torrent_info(lt.bdecode(torrent_data))
    info_hash = str(info.info_hashes().v1)
    magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={info.name()}"
    return magnet, info_hash


def lan_session(torrent_port: int) -> object:
    """Return a new libtorrent session configured for LAN-only operation."""
    from deckdrop.network import resume

    lt = _lt()
    listen = f"0.0.0.0:{torrent_port}"
    settings = dict(_LAN_SETTINGS_CORE)
    settings.update(_LAN_SETTINGS_OPTIONAL)
    settings["listen_interfaces"] = listen
    # Needed for save_resume_data / metadata alerts; optional so odd builds
    # still fall back to the core settings below.
    mask = resume.alert_mask(lt)
    if mask:
        settings["alert_mask"] = mask
    try:
        return lt.session(settings)
    except (KeyError, TypeError) as exc:
        if "unknown name" not in str(exc).lower():
            raise
        log.warning("Some libtorrent session settings unsupported (%s), using core set", exc)
        core = dict(_LAN_SETTINGS_CORE)
        core["listen_interfaces"] = listen
        return lt.session(core)
