"""GET /api/metadata/search?q=…, GET /api/metadata/steam/{app_id}

Online metadata for "Add game" / "Edit game": the UI shows the matches and the
user picks one – or adds the game without metadata.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from deckdrop.api import state as app_state
from deckdrop.api.deps import local_only
from deckdrop.core import metadata
from deckdrop.core.i18n import t

router = APIRouter(tags=["metadata"], dependencies=[Depends(local_only)])


def _lang() -> str:
    try:
        return metadata.steam_language(app_state.get().cfg.language)
    except RuntimeError:
        return "english"


@router.get("/metadata/search")
async def search_metadata(q: str = Query("", max_length=200)) -> dict:
    if not q.strip():
        return {"results": []}
    try:
        results = await metadata.search(q, lang=_lang())
    except metadata.MetadataError as exc:
        raise HTTPException(502, t("err.steam_search_failed", error=exc)) from exc
    return {"results": [r.to_dict() for r in results]}


@router.get("/metadata/steam/{app_id}")
async def steam_details(app_id: int) -> dict:
    if app_id <= 0:
        raise HTTPException(400, t("err.steam_no_match"))
    try:
        info = await metadata.details(app_id, lang=_lang())
    except metadata.MetadataError as exc:
        raise HTTPException(502, t("err.steam_search_failed", error=exc)) from exc
    if info is None:
        raise HTTPException(404, t("err.steam_no_match"))
    return info.to_dict()
