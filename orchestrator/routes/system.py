"""System status routes."""

from fastapi import APIRouter, Depends

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.db import get_app_state, AppState
from orchestrator.memory.embedding import _last_retry_at, _retry_count
from orchestrator.memory.encryption import (
    ENCRYPTION_OPERATIONS_FAILED_TOTAL_KEY,
    get_encryption_operations_failed_total,
)

router = APIRouter(prefix="/status", tags=["system"])


async def _get_shared_encryption_failures(app_state: AppState) -> int:
    if app_state.redis is None:
        return 0

    try:
        raw_value = await app_state.redis.get(ENCRYPTION_OPERATIONS_FAILED_TOTAL_KEY)
    except Exception:
        return 0

    if raw_value is None:
        return 0
    if isinstance(raw_value, bytes):
        raw_value = raw_value.decode()
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return 0


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
    encryption_failures = (
        get_encryption_operations_failed_total() + await _get_shared_encryption_failures(app_state)
    )

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
