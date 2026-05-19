"""GET /api/settings, PUT /api/settings"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from deckdrop.api import state as app_state
from deckdrop.core.config import save as save_cfg

router = APIRouter(tags=["settings"])


class SettingsOut(BaseModel):
    user_name: str
    peer_id: str
    download_dir: str
    port: int
    torrent_port: int
    max_upload_speed: int
    max_download_speed: int
    seed_after_download: bool


class SettingsPatch(BaseModel):
    user_name: str | None = None
    download_dir: str | None = None
    max_upload_speed: int | None = None
    max_download_speed: int | None = None
    seed_after_download: bool | None = None
    onboarding_complete: bool | None = None


@router.get("/settings", response_model=SettingsOut)
def get_settings() -> SettingsOut:
    cfg = app_state.get().cfg
    return SettingsOut(
        user_name=cfg.user_name,
        peer_id=cfg.peer_id,
        download_dir=str(cfg.download_dir),
        port=cfg.port,
        torrent_port=cfg.torrent_port,
        max_upload_speed=cfg.max_upload_speed,
        max_download_speed=cfg.max_download_speed,
        seed_after_download=cfg.seed_after_download,
    )


@router.put("/settings", response_model=SettingsOut)
def update_settings(req: SettingsPatch) -> SettingsOut:
    s = app_state.get()
    cfg = s.cfg

    if req.user_name is not None:
        cfg.user_name = req.user_name
    if req.download_dir is not None:
        from pathlib import Path

        cfg.download_dir = Path(req.download_dir)
    if req.max_upload_speed is not None:
        cfg.max_upload_speed = req.max_upload_speed
    if req.max_download_speed is not None:
        cfg.max_download_speed = req.max_download_speed
    if req.seed_after_download is not None:
        cfg.seed_after_download = req.seed_after_download
    if req.onboarding_complete is not None:
        cfg.onboarding_complete = req.onboarding_complete

    save_cfg(cfg)

    # Apply rate limits to running transfer session immediately
    transfer = s.transfer
    if transfer is not None and hasattr(transfer, "apply_rate_limits"):
        transfer.apply_rate_limits()

    return get_settings()
