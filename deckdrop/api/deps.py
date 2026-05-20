"""FastAPI dependencies shared across routes."""

from __future__ import annotations

from fastapi import HTTPException, Request

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def local_only(request: Request) -> None:
    """Reject requests not originating from localhost (403)."""
    host = request.client.host if request.client else ""
    if host not in _LOCAL_HOSTS:
        raise HTTPException(403, "This endpoint is only available from localhost")
