"""Video generation credit cost mapping.

Pricing: $0.05/second (1 credit = $0.05)
- 5s = 5 credits ($0.25)
- 10s = 10 credits ($0.50)
- 15s = 15 credits ($0.75)
- 20s = 20 credits ($1.00)
- 30s = 30 credits ($1.50)
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

DURATION_5S = 5
DURATION_10S = 10
DURATION_15S = 15
DURATION_20S = 20
DURATION_30S = 30


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
    kling_model: str = "o3-pro",
    audio_enabled: bool = False,
) -> int:
    """Estimate video generation cost in credits.

    Args:
        duration_seconds: Video duration in seconds
        tier: User tier (free, starter, pro, max, byok)
        provider: Video provider (xai, fal)
        resolution: Optional resolution parameter
        kling_model: Kling model type (o3-pro, v3-pro) for fal provider
        audio_enabled: Whether audio is enabled for fal provider

    Returns:
        Estimated cost in credits
    """
    config = get_pricing_config()

    _ = resolution

    if provider.lower() == "fal":
        # Calculate per-second credits based on model and audio settings
        if kling_model == "v3-pro":
            if audio_enabled:
                credits_per_second = int(0.196 * 20)  # Voice control: $0.196/sec
            else:
                credits_per_second = int(0.112 * 20)  # Standard: $0.112/sec
        else:
            if audio_enabled:
                credits_per_second = int(0.14 * 20)  # O3 Pro with audio: $0.14/sec
            else:
                credits_per_second = int(0.112 * 20)  # O3 Pro without audio: $0.112/sec

        base_cost = duration_seconds * credits_per_second
    else:
        # xAI uses fixed duration-based pricing
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

    # Apply tier discount
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
