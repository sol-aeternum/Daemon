"""Video generation credit cost mapping.

Pricing: $0.05/second (1 credit = $0.05)
- 5s = 5 credits ($0.25)
- 10s = 10 credits ($0.50)
- 15s = 15 credits ($0.75)
- 20s = 20 credits ($1.00)
- 30s = 30 credits ($1.50)

Sora Pricing:
- sora-2: $0.10/sec → 2 credits/sec
- sora-2-pro: $0.30/sec → 6 credits/sec (Pro tier)
- sora-2-pro: $0.50/sec → 10 credits/sec (Max tier)
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

DURATION_5S = 5
DURATION_10S = 10
DURATION_15S = 15
DURATION_20S = 20
DURATION_30S = 30

# Sora pricing constants (credits per second)
SORA_2_CREDITS_PER_SECOND = 2
SORA_2_PRO_CREDITS_PER_SECOND_PRO_TIER = 6
SORA_2_PRO_CREDITS_PER_SECOND_MAX_TIER = 10


class VideoPricingConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="VIDEO_", extra="ignore"
    )

    credits_per_second: int = 1
    cost_5s: int = 5
    cost_10s: int = 10
    cost_15s: int = 15
    cost_20s: int = 20
    cost_30s: int = 30

    tier_pro_discount: float = 1.0
    tier_max_discount: float = 0.8
    tier_byok_discount: float = 0.0


_pricing_config: VideoPricingConfig | None = None


def get_pricing_config() -> VideoPricingConfig:
    global _pricing_config
    if _pricing_config is None:
        _pricing_config = VideoPricingConfig()
    return _pricing_config


DURATION_COSTS: dict[int, int] = {
    5: 5,
    10: 10,
    15: 15,
    20: 20,
    30: 30,
}


def estimate_cost(
    duration_seconds: int,
    tier: str = "pro",
    provider: str = "xai",
    resolution: str | None = None,
) -> int:
    config = get_pricing_config()

    _ = resolution

    if duration_seconds == 5:
        base_cost = config.cost_5s
    elif duration_seconds == 10:
        base_cost = config.cost_10s
    elif duration_seconds == 15:
        base_cost = config.cost_15s
    elif duration_seconds == 20:
        base_cost = config.cost_20s
    elif duration_seconds == 30:
        base_cost = config.cost_30s
    else:
        base_cost = duration_seconds * config.credits_per_second

    provider_name = provider.lower()
    is_sora = provider_name in ("sora", "openai_sora")

    if is_sora:
        # Sora uses per-second pricing (tier already included in pricing)
        if tier.lower() == "max":
            # Max tier = sora-2-pro at $0.50/sec = 10 credits/sec
            base_cost = duration_seconds * SORA_2_PRO_CREDITS_PER_SECOND_MAX_TIER
        elif tier.lower() == "pro":
            # Pro tier = sora-2-pro at $0.30/sec = 6 credits/sec
            base_cost = duration_seconds * SORA_2_PRO_CREDITS_PER_SECOND_PRO_TIER
        else:
            # Default/fallback = sora-2 at $0.10/sec = 2 credits/sec
            base_cost = duration_seconds * SORA_2_CREDITS_PER_SECOND
        # Sora pricing already includes tier - no additional discount
        return int(base_cost)

    # xAI uses duration-based pricing (already calculated above)
    # Apply tier discount for xAI
    tier_name = tier.lower()
    if tier_name == "pro":
        discount = config.tier_pro_discount
    elif tier_name == "max":
        discount = config.tier_max_discount
    elif tier_name == "byok":
        discount = config.tier_byok_discount
    else:
        discount = config.tier_pro_discount

    return int(base_cost * discount)


def get_tier_discount(tier: str) -> float:
    config = get_pricing_config()
    tier_name = tier.lower()

    if tier_name == "pro":
        return config.tier_pro_discount
    elif tier_name == "max":
        return config.tier_max_discount
    elif tier_name == "byok":
        return config.tier_byok_discount

    return config.tier_pro_discount
