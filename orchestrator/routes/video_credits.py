"""Video credits API routes."""

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from pydantic import BaseModel
from typing import List
import uuid

from orchestrator.db import get_app_state, AppState
from db.video_credits import Transaction
from orchestrator.config import Settings, get_settings
from config.video_pricing import estimate_cost

router = APIRouter(prefix="/video-credits", tags=["video_credits"])

DEFAULT_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def require_api_key(settings: Settings, authorization: str | None) -> None:
    """Require valid API key for authentication."""
    if not settings.daemon_api_key:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != settings.daemon_api_key:
        raise HTTPException(status_code=401, detail="Invalid bearer token")


def require_admin_api_key(settings: Settings, authorization: str | None) -> None:
    if not settings.daemon_admin_api_key:
        raise HTTPException(status_code=403, detail="Admin credit grants are disabled")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != settings.daemon_admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid admin bearer token")


def get_bound_user_id() -> uuid.UUID:
    return DEFAULT_USER_ID


def get_bound_tier(settings: Settings) -> str:
    tier = settings.default_tier.lower().strip()
    if tier not in VALID_TIERS:
        raise HTTPException(status_code=500, detail="Invalid configured default tier")
    return tier


class BalanceResponse(BaseModel):
    balance: int


class TransactionResponse(Transaction):
    pass


class GrantRequest(BaseModel):
    user_id: uuid.UUID
    amount: int
    description: str


class TransactionListResponse(BaseModel):
    transactions: List[TransactionResponse]
    total: int


class EstimateResponse(BaseModel):
    credits_required: int
    current_balance: int
    sufficient: bool


@router.get("/balance", response_model=BalanceResponse)
async def get_balance(
    app_state: AppState = Depends(get_app_state),
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None, alias="Authorization"),
    user_id: uuid.UUID = Query(..., description="User ID to get balance for"),
):
    """Get current video credit balance for a user."""
    require_api_key(settings, authorization)

    if app_state.video_credits_dal is None:
        raise HTTPException(status_code=503, detail="Video credits service unavailable")

    balance = await app_state.video_credits_dal.get_balance(user_id)
    return BalanceResponse(balance=balance)


@router.get("/transactions", response_model=TransactionListResponse)
async def get_transactions(
    limit: int = Query(50, le=100),
    offset: int = Query(0),
    app_state: AppState = Depends(get_app_state),
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None, alias="Authorization"),
    user_id: uuid.UUID = Query(..., description="User ID to get transactions for"),
):
    """Get paginated transaction history for a user."""
    require_api_key(settings, authorization)

    if app_state.video_credits_dal is None:
        raise HTTPException(status_code=503, detail="Video credits service unavailable")

    transactions = await app_state.video_credits_dal.get_transactions(
        user_id, limit, offset
    )
    # Get total count for pagination
    db_pool = app_state.db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    async with db_pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM video_credit_transactions WHERE user_id = $1",
            user_id,
        )

    return TransactionListResponse(
        transactions=[TransactionResponse(**t.__dict__) for t in transactions],
        total=total,
    )


@router.post("/grant", status_code=201)
async def grant_credits(
    grant_request: GrantRequest,
    app_state: AppState = Depends(get_app_state),
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    """Admin-only endpoint to grant video credits to a user."""
    require_admin_api_key(settings, authorization)

    if app_state.video_credits_dal is None:
        raise HTTPException(status_code=503, detail="Video credits service unavailable")

    result = await app_state.video_credits_dal.credit_credits(
        grant_request.user_id,
        grant_request.amount,
        "admin_grant",
        grant_request.description,
    )

    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)

    return {
        "status": "success",
        "message": result.message,
        "transaction_id": str(result.transaction_id),
    }


VALID_TIERS = {"free", "starter", "pro", "max", "byok"}
VALID_VIDEO_PROVIDERS = {"xai", "openai_sora", "sora"}


@router.get("/estimate", response_model=EstimateResponse)
async def estimate_video_cost(
    duration: int = Query(..., description="Video duration in seconds", ge=1),
    tier: str = Query(..., description="User tier (free, starter, pro, max, or byok)"),
    provider: str = Query("xai", description="Video provider (xai or openai_sora)"),
    resolution: str | None = Query(None, description="Requested output resolution"),
    user_id: uuid.UUID = Query(..., description="User ID"),
    app_state: AppState = Depends(get_app_state),
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None, alias="Authorization"),
):
    """Estimate credits required for a video of given duration and tier."""
    require_api_key(settings, authorization)

    tier_lower = tier.lower().strip()
    if tier_lower not in VALID_TIERS:
        raise HTTPException(status_code=400, detail="Invalid tier")

    provider_name = provider.lower().strip()
    if provider_name not in VALID_VIDEO_PROVIDERS:
        raise HTTPException(status_code=400, detail="Invalid provider")
    if provider_name == "sora":
        provider_name = "openai_sora"

    if app_state.video_credits_dal is None:
        raise HTTPException(status_code=503, detail="Video credits service unavailable")

    tier_config = settings.get_tier_config(tier_lower)
    if not tier_config.tier_video_enabled:
        raise HTTPException(
            status_code=403,
            detail=f"Video generation is not available for {tier_lower.capitalize()} tier",
        )

    if (
        tier_config.tier_video_max_duration is not None
        and duration > tier_config.tier_video_max_duration
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Duration exceeds tier limit ({tier_config.tier_video_max_duration}s)"
            ),
        )

    # Get user's current balance
    current_balance = await app_state.video_credits_dal.get_balance(user_id)

    # Calculate credits required
    credits_required = estimate_cost(
        duration_seconds=duration,
        tier=tier_lower,
        provider=provider_name,
        resolution=resolution,
    )

    return EstimateResponse(
        credits_required=credits_required,
        current_balance=current_balance,
        sufficient=current_balance >= credits_required,
    )
