"""
/api/fs  – browse local folders (used by the "Add game" folder picker).

GET /api/fs/browse?path=...   Subfolders of a directory (default: home)

A browser can't hand the web UI an absolute folder path, so the picker
asks the backend instead. Local only: peers must not list our disk.
"""

from __future__ import annotations

import os
import string
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from deckdrop.api import state as app_state
from deckdrop.api.deps import local_only
from deckdrop.core.i18n import t

router = APIRouter(tags=["fs"], dependencies=[Depends(local_only)])


class DirEntry(BaseModel):
    name: str
    path: str


class BrowseOut(BaseModel):
    path: str
    parent: str | None
    dirs: list[DirEntry]
    shortcuts: list[DirEntry]


def _shortcuts() -> list[DirEntry]:
    """Quick jumps: home, download folder, drives / removable media."""
    out: list[DirEntry] = []
    seen: set[str] = set()

    def add(name: str, p: Path) -> None:
        key = str(p)
        if key not in seen and p.is_dir():
            seen.add(key)
            out.append(DirEntry(name=name, path=key))

    home = Path.home()
    add("~", home)
    try:
        dl = app_state.get().cfg.download_dir
        add(dl.name or str(dl), dl)
    except Exception:
        pass
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            add(f"{letter}:", Path(f"{letter}:\\"))
    else:
        add("/", Path("/"))
        # Steam Deck / desktop Linux mount SD cards and USB drives here.
        for media in (
            Path("/run/media") / home.name,
            Path("/run/media"),
            Path("/media") / home.name,
        ):
            try:
                for p in sorted(media.iterdir()):
                    if p.is_dir() and p.name != home.name:
                        add(p.name, p)
            except OSError:
                pass
    return out


@router.get("/fs/browse", response_model=BrowseOut)
def browse(path: str = "") -> BrowseOut:
    target = Path(path).expanduser() if path.strip() else Path.home()
    try:
        target = target.resolve()
    except OSError:
        pass
    if not target.is_dir():
        raise HTTPException(404, t("err.folder_not_found", path=str(target)))

    dirs: list[DirEntry] = []
    try:
        with os.scandir(target) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                try:
                    if e.is_dir():
                        dirs.append(DirEntry(name=e.name, path=e.path))
                except OSError:
                    continue
    except PermissionError:
        raise HTTPException(403, t("err.folder_no_access", path=str(target))) from None
    dirs.sort(key=lambda d: d.name.lower())

    parent = str(target.parent) if target.parent != target else None
    return BrowseOut(path=str(target), parent=parent, dirs=dirs, shortcuts=_shortcuts())
