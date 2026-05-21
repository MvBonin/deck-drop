"""Per-game comments stored in comments.toml alongside deckdrop.toml."""

from __future__ import annotations

import tomllib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import tomli_w

COMMENTS_FILENAME = "comments.toml"


@dataclass
class Comment:
    id: str  # uuid4 – used for deduplication across peers
    author: str
    text: str
    created_at: str  # ISO 8601 UTC


def load_comments(game_path: Path) -> list[Comment]:
    p = game_path / COMMENTS_FILENAME
    if not p.exists():
        return []
    with p.open("rb") as f:
        data = tomllib.load(f)
    return [
        Comment(
            id=c.get("id", str(uuid.uuid4())),
            author=c.get("author", ""),
            text=c.get("text", ""),
            created_at=c.get("created_at", ""),
        )
        for c in data.get("comment", [])
    ]


def save_comments(game_path: Path, comments: list[Comment]) -> None:
    p = game_path / COMMENTS_FILENAME
    data = {
        "comment": [
            {"id": c.id, "author": c.author, "text": c.text, "created_at": c.created_at}
            for c in comments
        ]
    }
    with p.open("wb") as f:
        tomli_w.dump(data, f)


def merge_comments(local: list[Comment], incoming: list[Comment]) -> list[Comment]:
    """Union by id, sorted chronologically ascending."""
    by_id = {c.id: c for c in local}
    for c in incoming:
        by_id.setdefault(c.id, c)
    return sorted(by_id.values(), key=lambda c: c.created_at)


def new_comment(author: str, text: str) -> Comment:
    return Comment(
        id=str(uuid.uuid4()),
        author=author,
        text=text,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
