"""Background service + system install.

GET  /api/service            service + install state
POST /api/service/enable     run DeckDrop in the background at every boot
POST /api/service/disable    remove the background service
POST /api/install            copy the AppImage to ~/Applications + menu entries
POST /api/install/remove     remove the menu entries (file stays)
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from deckdrop.api import state as app_state
from deckdrop.api.deps import local_only
from deckdrop.core import install as install_mod
from deckdrop.core import service as svc
from deckdrop.core.config import save as save_cfg
from deckdrop.core.i18n import t

router = APIRouter(tags=["service"], dependencies=[Depends(local_only)])


class ServiceStatus(BaseModel):
    enabled: bool
    active: bool
    install_type: str
    # this process was started by the service (closing the UI keeps it running)
    running_as_service: bool = False
    # AppImage install state (see deckdrop.core.install)
    running_from: str = ""
    install_path: str = ""
    installed: bool = False
    running_installed: bool = False
    desktop_entries: bool = False
    can_install: bool = False


def _service_appimage(cfg: object) -> str:
    """The AppImage the service should start: the installed copy if there is one."""
    installed = install_mod.install_path()
    if installed.is_file():
        return str(installed)
    return getattr(cfg, "appimage_path", "") or ""


@router.get("/service", response_model=ServiceStatus)
def get_service_status() -> ServiceStatus:
    cfg = app_state.get().cfg
    return ServiceStatus(
        enabled=svc.is_enabled(),
        active=svc.is_active(),
        install_type=svc.detect_install_type(cfg.appimage_path),
        running_as_service=svc.running_as_service(),
        **install_mod.status().to_dict(),
    )


@router.post("/service/enable", response_model=ServiceStatus)
def enable_service() -> ServiceStatus:
    cfg = app_state.get().cfg
    appimage = _service_appimage(cfg)
    install_type = svc.detect_install_type(appimage)
    try:
        svc.enable(install_type, appimage)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    cfg.autostart = True
    save_cfg(cfg)
    return get_service_status()


@router.post("/service/disable", response_model=ServiceStatus)
def disable_service() -> ServiceStatus:
    cfg = app_state.get().cfg
    if svc.running_as_service():
        # `disable --now` would kill this very process mid-request.
        svc.disable_keep_running()
    else:
        svc.disable()
    cfg.autostart = False
    save_cfg(cfg)
    return get_service_status()


@router.post("/install", response_model=ServiceStatus)
def install_appimage() -> ServiceStatus:
    cfg = app_state.get().cfg
    try:
        target = install_mod.install()
    except ValueError as exc:
        raise HTTPException(400, t("err.install_not_appimage")) from exc
    except OSError as exc:
        raise HTTPException(500, t("err.install_failed", error=exc)) from exc
    cfg.appimage_path = str(target)
    save_cfg(cfg)
    svc.refresh_appimage_path(str(target))
    # A later "restart" (POST /api/selfupdate/restart) execs the installed
    # copy, and self-updates replace that one from now on.
    os.environ["APPIMAGE"] = str(target)
    return get_service_status()


@router.post("/install/remove", response_model=ServiceStatus)
def remove_install() -> ServiceStatus:
    install_mod.uninstall()
    return get_service_status()
