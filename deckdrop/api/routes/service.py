"""GET /api/service, POST /api/service/enable, POST /api/service/disable"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from deckdrop.api import state as app_state
from deckdrop.api.deps import local_only
from deckdrop.core import service as svc
from deckdrop.core.config import save as save_cfg

router = APIRouter(tags=["service"], dependencies=[Depends(local_only)])


class ServiceStatus(BaseModel):
    enabled: bool
    active: bool
    install_type: str


@router.get("/service", response_model=ServiceStatus)
def get_service_status() -> ServiceStatus:
    return ServiceStatus(
        enabled=svc.is_enabled(),
        active=svc.is_active(),
        install_type=svc.detect_install_type(),
    )


@router.post("/service/enable", response_model=ServiceStatus)
def enable_service() -> ServiceStatus:
    cfg = app_state.get().cfg
    install_type = svc.detect_install_type()
    try:
        svc.enable(install_type, cfg.appimage_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    cfg.autostart = True
    save_cfg(cfg)
    return get_service_status()


@router.post("/service/disable", response_model=ServiceStatus)
def disable_service() -> ServiceStatus:
    cfg = app_state.get().cfg
    svc.disable()
    cfg.autostart = False
    save_cfg(cfg)
    return get_service_status()
