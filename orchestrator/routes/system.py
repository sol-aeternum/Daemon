"""System status routes."""

from fastapi import APIRouter, Depends

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.db import get_app_state, AppState
from orchestrator.memory.embedding import (
    _last_retry_at,
    _retry_count,
    get_embedding_failures_total,
    get_embedding_provider_used_counts,
)

router = APIRouter(prefix="/status", tags=["system"])


@router.get("")
async def get_status(
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Get system status."""
    # Check DB health
    db_healthy = app_state.db_pool is not None

    # Check Redis health
    redis_healthy = app_state.redis is not None

    return {
        "status": "healthy" if db_healthy else "degraded",
        "db_healthy": db_healthy,
        "redis_healthy": redis_healthy,
        "memory_enabled": app_state.memory_store is not None,
        "embedding_retry_activations": _retry_count,
        "embedding_last_retry_at": _last_retry_at,
        "embedding_failures_total": get_embedding_failures_total(),
        "embedding_provider_used": get_embedding_provider_used_counts(),
    }
