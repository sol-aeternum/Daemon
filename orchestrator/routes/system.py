"""System status routes."""

from fastapi import APIRouter, Depends

from orchestrator.db import get_app_state, AppState
from orchestrator.memory.embedding import _fallback_count, _last_fallback_at

router = APIRouter(prefix="/status", tags=["system"])


@router.get("")
async def get_status(app_state: AppState = Depends(get_app_state)):
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
        "embedding_fallback_activations": _fallback_count,
        "embedding_last_fallback_at": _last_fallback_at,
    }
