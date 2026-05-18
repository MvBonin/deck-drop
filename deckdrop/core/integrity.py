"""File hashing and verification using blake2b (stdlib)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

CHUNK_SIZE = 1024 * 1024  # 1 MB


def hash_file(path: Path, progress: Callable[[int], None] | None = None) -> str:
    """Return blake2b hex digest for a single file."""
    h = hashlib.blake2b()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            h.update(chunk)
            if progress:
                progress(len(chunk))
    return h.hexdigest()


def hash_directory(
    root: Path,
    progress: Callable[[str, int], None] | None = None,
) -> tuple[dict[str, str], int]:
    """
    Hash all files under root recursively.

    Returns (filename_to_hash, total_bytes).
    filename keys are relative to root, using forward slashes.
    """
    results: dict[str, str] = {}
    total_bytes = 0

    files = sorted(p for p in root.rglob("*") if p.is_file() and p.name != "deckdrop.toml")

    for file_path in files:
        rel = file_path.relative_to(root).as_posix()

        def _progress(n: int, rel: str = rel) -> None:
            nonlocal total_bytes
            total_bytes += n
            if progress:
                progress(rel, n)

        results[rel] = hash_file(file_path, _progress)

    return results, total_bytes


def verify_files(root: Path, expected: dict[str, str]) -> list[str]:
    """
    Check files against expected hashes.

    Returns list of relative paths that are missing or have wrong hashes.
    """
    failures: list[str] = []
    for rel, expected_hash in expected.items():
        file_path = root / rel
        if not file_path.exists():
            failures.append(rel)
            continue
        actual = hash_file(file_path)
        if actual != expected_hash:
            failures.append(rel)
    return failures
