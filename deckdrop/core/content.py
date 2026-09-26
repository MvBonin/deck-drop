"""
Pure content/manifest helpers (no libtorrent, no write side-effects except in
the plan-apply functions, which are explicitly I/O helpers used by Phase 5).

Everything here is testable without libtorrent installed.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from deckdrop.core.integrity import TORRENT_SKIP_FILENAMES

DEFAULT_IGNORE: tuple[str, ...] = (
    "*.log",
    "*.tmp",
    "*.dxvk-cache",
    "*.vkd3d-proton.cache*",
    "Thumbs.db",
    ".DS_Store",
    "desktop.ini",
    *TORRENT_SKIP_FILENAMES,
)


def is_ignored(rel: str, patterns: Iterable[str]) -> bool:
    """True if rel (POSIX relpath) matches TORRENT_SKIP_FILENAMES, DEFAULT_IGNORE or patterns."""
    basename = rel.rsplit("/", 1)[-1]
    if basename in TORRENT_SKIP_FILENAMES:
        return True
    for pattern in (*DEFAULT_IGNORE, *patterns):
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if rel == prefix or rel.startswith(prefix + "/"):
                return True
            continue
        if fnmatch.fnmatchcase(rel, pattern) or fnmatch.fnmatchcase(basename, pattern):
            return True
    return False


def iter_content_files(root: Path, patterns: Iterable[str]) -> list[str]:
    """Sorted POSIX relpaths of all files under root that are not ignored."""
    patterns = list(patterns)
    result: list[str] = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if is_ignored(rel, patterns):
            continue
        result.append(rel)
    return sorted(result)


def compute_content_hash(files: dict[str, str], sizes: dict[str, int]) -> str:
    """blake2b(digest_size=16) over the sorted manifest; '' if files is empty."""
    import hashlib

    if not files:
        return ""
    lines = [f"{rel}\t{files[rel]}\t{sizes.get(rel, -1)}" for rel in sorted(files)]
    return hashlib.blake2b("\n".join(lines).encode("utf-8"), digest_size=16).hexdigest()


def take_snapshot(root: Path, rels: Iterable[str]) -> dict[str, list[int]]:
    """rel -> [st_size, st_mtime_ns]; missing files are left out."""
    snapshot: dict[str, list[int]] = {}
    for rel in rels:
        path = root / rel
        try:
            st = path.stat()
        except OSError:
            continue
        snapshot[rel] = [st.st_size, st.st_mtime_ns]
    return snapshot


@dataclass
class ScanResult:
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    mtime_only: list[str] = field(default_factory=list)


def compare_snapshot(
    root: Path,
    manifest_files: dict[str, str],
    snapshot: dict[str, list[int]],
    patterns: Iterable[str],
) -> ScanResult:
    """Compare the manifest against the current snapshot using stat() only."""
    result = ScanResult()
    patterns = list(patterns)

    for rel in manifest_files:
        path = root / rel
        try:
            st = path.stat()
        except OSError:
            result.removed.append(rel)
            continue
        old = snapshot.get(rel)
        if old is None:
            # No snapshot entry yet – treat as changed so it gets hashed/recorded.
            result.changed.append(rel)
            continue
        old_size, old_mtime_ns = old
        if st.st_size != old_size:
            result.changed.append(rel)
        elif st.st_mtime_ns != old_mtime_ns:
            result.mtime_only.append(rel)

    for rel in iter_content_files(root, patterns):
        if rel not in manifest_files:
            result.added.append(rel)

    result.changed.sort()
    result.removed.sort()
    result.added.sort()
    result.mtime_only.sort()
    return result


@dataclass
class ManifestDiff:
    unchanged: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    moved: dict[str, str] = field(default_factory=dict)  # new_rel -> old_rel
    download_estimate: int | None = None


def diff_manifests(
    old_files: dict[str, str],
    old_sizes: dict[str, int],
    new_files: dict[str, str],
    new_sizes: dict[str, int],
) -> ManifestDiff:
    """Diff two manifests (relpath -> hash / relpath -> size)."""
    diff = ManifestDiff()

    # Index old files by hash to detect moves.
    old_by_hash: dict[str, list[str]] = {}
    for rel, h in old_files.items():
        old_by_hash.setdefault(h, []).append(rel)
    used_old: set[str] = set()

    for rel in sorted(new_files):
        new_hash = new_files[rel]
        if rel in old_files:
            if old_files[rel] == new_hash:
                diff.unchanged.append(rel)
            else:
                diff.changed.append(rel)
            used_old.add(rel)
            continue
        # Not present under the same path – see if it moved from elsewhere.
        candidates = [c for c in old_by_hash.get(new_hash, []) if c not in used_old]
        candidates = [c for c in candidates if c not in new_files]
        if candidates:
            old_rel = candidates[0]
            diff.moved[rel] = old_rel
            used_old.add(old_rel)
        else:
            diff.added.append(rel)

    for rel in sorted(old_files):
        if rel in new_files:
            continue
        if rel in diff.moved.values():
            continue
        diff.removed.append(rel)

    if new_sizes:
        to_load = [*diff.changed, *diff.added]
        diff.download_estimate = sum(new_sizes.get(rel, 0) for rel in to_load)
    else:
        diff.download_estimate = None

    return diff


@dataclass
class LocalPrepPlan:
    moves: list[tuple[str, str]] = field(default_factory=list)  # (old_rel, new_rel)
    copies: list[tuple[str, str]] = field(default_factory=list)  # (old_rel, new_rel)
    truncates: list[tuple[str, int]] = field(default_factory=list)  # (rel, new_size)
    deletes_after: list[str] = field(default_factory=list)


def plan_local_prep(
    root: Path,
    diff: ManifestDiff,
    new_sizes: dict[str, int],
    new_ignore: Iterable[str],
) -> LocalPrepPlan:
    """Build the local filesystem plan to run before add_torrent for an update."""
    plan = LocalPrepPlan()

    # A source used by more than one new path (duplicated file) can only be
    # renamed once; every other destination is a copy that leaves it in place.
    uses: dict[str, int] = {}
    for old_rel in diff.moved.values():
        uses[old_rel] = uses.get(old_rel, 0) + 1

    moved_once: set[str] = set()
    for new_rel, old_rel in sorted(diff.moved.items()):
        if uses[old_rel] == 1:
            plan.moves.append((old_rel, new_rel))
        elif old_rel not in moved_once:
            plan.moves.append((old_rel, new_rel))
            moved_once.add(old_rel)
        else:
            plan.copies.append((old_rel, new_rel))

    for rel in diff.changed:
        new_size = new_sizes.get(rel)
        if new_size is None:
            continue
        path = root / rel
        try:
            current_size = path.stat().st_size
        except OSError:
            continue
        if current_size > new_size:
            plan.truncates.append((rel, new_size))

    new_ignore = list(new_ignore)
    for rel in diff.removed:
        if is_ignored(rel, new_ignore):
            continue
        plan.deletes_after.append(rel)

    return plan


def safe_join(root: Path, rel: str) -> Path | None:
    """Join root with an untrusted relative path from a peer manifest.

    Returns None if rel is absolute or escapes root once resolved (path
    traversal, symlink tricks, ...). Never raises.
    """
    if not rel or os.path.isabs(rel) or rel.startswith(("/", "\\")):
        return None
    root_resolved = root.resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None
    return candidate


def apply_local_prep(root: Path, plan: LocalPrepPlan) -> None:
    """Apply moves/copies/truncates. Never touches deletes (see delete_removed)."""
    for old_rel, new_rel in plan.moves:
        src = safe_join(root, old_rel)
        dst = safe_join(root, new_rel)
        if src is None or dst is None or not src.is_file():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)

    for old_rel, new_rel in plan.copies:
        src = safe_join(root, old_rel)
        dst = safe_join(root, new_rel)
        if src is None or dst is None or not src.is_file():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    for rel, new_size in plan.truncates:
        target = safe_join(root, rel)
        if target is None or not target.is_file():
            continue
        os.truncate(target, new_size)


def delete_removed(root: Path, rels: Iterable[str]) -> None:
    """Delete files, then remove empty parent directories bottom-up."""
    dirs_to_check: set[Path] = set()
    for rel in rels:
        target = safe_join(root, rel)
        if target is None:
            continue
        try:
            target.unlink(missing_ok=True)
        except OSError:
            continue
        dirs_to_check.add(target.parent)

    # Remove now-empty directories, deepest first, without ever touching root.
    root_resolved = root.resolve()
    for d in sorted(dirs_to_check, key=lambda p: len(p.parts), reverse=True):
        current = d
        while current.resolve() != root_resolved and root_resolved in current.resolve().parents:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
