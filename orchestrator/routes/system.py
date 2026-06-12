"""System status routes."""

from fastapi import APIRouter, Depends

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.db import get_app_state, AppState
from orchestrator.memory.embedding import _last_retry_at, _retry_count
from orchestrator.memory.encryption import get_encryption_operations_failed_total

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
    encryption_failures = get_encryption_operations_failed_total()

    return {
        "status": "healthy" if db_healthy else "degraded",
        "db_healthy": db_healthy,
        "redis_healthy": redis_healthy,
        "memory_enabled": app_state.memory_store is not None,
        "embedding_retry_activations": _retry_count,
        "embedding_last_retry_at": _last_retry_at,
        "encryption_operations_failed_total": encryption_failures,
        "encryption_failure_alert": encryption_failures > 0,
    }
