"""Video credits API routes."""

import hmac

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from pydantic import BaseModel
from typing import List
import uuid

from orchestrator.auth import AuthenticatedDevice, require_device_auth
from orchestrator.db import get_app_state, AppState
from db.video_credits import Transaction
from orchestrator.config import Settings, get_settings
from config.video_pricing import estimate_cost

router = APIRouter(prefix="/video-credits", tags=["video_credits"])


def require_admin_api_key(settings: Settings, authorization: str | None) -> None:
    if not settings.daemon_admin_api_key:
        raise HTTPException(status_code=403, detail="Admin credit grants are disabled")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(token.encode(), settings.daemon_admin_api_key.encode()):
        raise HTTPException(status_code=403, detail="Invalid admin bearer token")


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
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Get current video credit balance for a user."""
    if app_state.video_credits_dal is None:
        raise HTTPException(status_code=503, detail="Video credits service unavailable")

    balance = await app_state.video_credits_dal.get_balance(auth.user_id)
    return BalanceResponse(balance=balance)


@router.get("/transactions", response_model=TransactionListResponse)
async def get_transactions(
    limit: int = Query(50, le=100),
    offset: int = Query(0),
    app_state: AppState = Depends(get_app_state),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Get paginated transaction history for a user."""
    if app_state.video_credits_dal is None:
        raise HTTPException(status_code=503, detail="Video credits service unavailable")

    transactions = await app_state.video_credits_dal.get_transactions(auth.user_id, limit, offset)
    # Get total count for pagination
    db_pool = app_state.db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    async with db_pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM video_credit_transactions WHERE user_id = $1",
            auth.user_id,
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
    """Admin-only endpoint to grant video credits to a user.

    Requires only the admin bearer token in the Authorization header.
    Does NOT require device authentication.
    """
    require_admin_api_key(settings, authorization)

    if grant_request.amount <= 0:
        raise HTTPException(status_code=422, detail="amount must be a positive integer")
    max_amount = settings.daemon_max_grant_amount_per_request
    if grant_request.amount > max_amount:
        raise HTTPException(
            status_code=422,
            detail=(
                f"amount exceeds per-request maximum of {max_amount} "
                "(adjust DAEMON_MAX_GRANT_AMOUNT_PER_REQUEST if needed)"
            ),
        )
    cleaned_description = grant_request.description.strip()
    if len(cleaned_description) < settings.daemon_min_grant_description_length:
        raise HTTPException(
            status_code=422,
            detail=(
                "description must be at least "
                f"{settings.daemon_min_grant_description_length} "
                "characters after trimming"
            ),
        )

    if app_state.video_credits_dal is None:
        raise HTTPException(status_code=503, detail="Video credits service unavailable")

    result = await app_state.video_credits_dal.credit_credits(
        grant_request.user_id,
        grant_request.amount,
        "admin_grant",
        cleaned_description,
    )

    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)

    return {
        "status": "success",
        "message": result.message,
        "transaction_id": str(result.transaction_id),
    }


VALID_TIERS = {"free", "starter", "pro", "max", "byok"}
VALID_VIDEO_PROVIDERS = {"xai", "fal"}


@router.get("/estimate", response_model=EstimateResponse)
async def estimate_video_cost(
    duration: int = Query(..., description="Video duration in seconds", ge=1),
    tier: str = Query(..., description="User tier (free, starter, pro, max, or byok)"),
    provider: str = Query("xai", description="Video provider (xai, kling)"),
    resolution: str | None = Query(None, description="Requested output resolution"),
    kling_model: str | None = Query(None, description="Kling model (kling-o3-pro, kling-v3-pro)"),
    audio_enabled: bool = Query(False, description="Whether audio is enabled for Kling"),
    app_state: AppState = Depends(get_app_state),
    settings: Settings = Depends(get_settings),
    auth: AuthenticatedDevice = Depends(require_device_auth),
):
    """Estimate credits required for a video of given duration and tier."""
    tier_lower = tier.lower().strip()
    if tier_lower not in VALID_TIERS:
        raise HTTPException(status_code=400, detail="Invalid tier")

    provider_name = provider.lower().strip()
    if provider_name not in VALID_VIDEO_PROVIDERS and provider_name != "kling":
        raise HTTPException(status_code=400, detail="Invalid provider")

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
            detail=(f"Duration exceeds tier limit ({tier_config.tier_video_max_duration}s)"),
        )

    normalized_kling_model = "o3-pro"
    if kling_model:
        model_lower = kling_model.lower().strip()
        if model_lower == "kling-v3-pro":
            normalized_kling_model = "v3-pro"
        elif model_lower in ("kling-o3-pro", "o3-pro"):
            normalized_kling_model = "o3-pro"

    # Get user's current balance
    current_balance = await app_state.video_credits_dal.get_balance(auth.user_id)

    # Calculate credits required
    pricing_provider = "fal" if provider_name == "kling" else provider_name
    credits_required = estimate_cost(
        duration_seconds=duration,
        tier=tier_lower,
        provider=pricing_provider,
        resolution=resolution,
        kling_model=normalized_kling_model,
        audio_enabled=audio_enabled,
    )

    return EstimateResponse(
        credits_required=credits_required,
        current_balance=current_balance,
        sufficient=current_balance >= credits_required,
    )
