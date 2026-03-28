"""Admin routes for the PresenceService."""
import logging
from typing import Optional

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from log_buffer import query_logs

router = APIRouter(prefix="/admin", tags=["admin"])
_bearer = HTTPBearer()
logger  = logging.getLogger(__name__)

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _require_admin(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """Dependency: decode JWT and assert 'admin' role."""
    # Lazy import avoids circular dependency (main imports this router)
    import main as _main
    try:
        payload = _main.decode_token(credentials.credentials)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        )
    except pyjwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    if "admin" not in payload.get("roles", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return payload


@router.get("/logs")
def get_logs(
    minutes: float            = Query(default=5.0,   ge=0.1, le=1440),
    level:   Optional[str]    = Query(default=None),
    limit:   int              = Query(default=200,   ge=1,   le=1000),
    offset:  int              = Query(default=0,     ge=0),
    _admin:  dict             = Depends(_require_admin),
):
    """Return recent log entries from the in-memory ring-buffer."""
    if level is not None and level.upper() not in _VALID_LEVELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid level '{level}'. Must be one of: {', '.join(sorted(_VALID_LEVELS))}",
        )

    norm_level = level.upper() if level else None
    logs, total = query_logs(
        minutes=minutes,
        min_level=norm_level,
        limit=limit,
        offset=offset,
    )

    return {
        "status":   "ok",
        "total":    total,
        "returned": len(logs),
        "query": {
            "minutes": minutes,
            "level":   level,
            "limit":   limit,
            "offset":  offset,
        },
        "logs": logs,
    }
