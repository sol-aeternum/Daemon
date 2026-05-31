from __future__ import annotations


from config.video_pricing import estimate_cost


def test_xai_pricing_backward_compatibility() -> None:
    """Test xAI pricing remains unchanged for backward compatibility."""
    assert estimate_cost(10, tier="pro", provider="xai") == 10
    assert estimate_cost(10, tier="max", provider="xai") == 8
    assert estimate_cost(10, tier="byok", provider="xai") == 0

    # Test all durations with xAI
    assert estimate_cost(5, tier="pro", provider="xai") == 5
    assert estimate_cost(10, tier="pro", provider="xai") == 10
    assert estimate_cost(15, tier="pro", provider="xai") == 15
    assert estimate_cost(20, tier="pro", provider="xai") == 20
    assert estimate_cost(30, tier="pro", provider="xai") == 30


def test_all_durations() -> None:
    """Test all supported durations with different providers and tiers."""
    durations = [5, 10, 15, 20, 30]

    # xAI provider
    xai_expected = [5, 10, 15, 20, 30]  # Pro tier
    for duration, expected in zip(durations, xai_expected):
        assert estimate_cost(duration, tier="pro", provider="xai") == expected

    # xAI Max tier
    xai_max_expected = [4, 8, 12, 16, 24]  # 0.8 discount
    for duration, expected in zip(durations, xai_max_expected):
        assert estimate_cost(duration, tier="max", provider="xai") == expected


def test_case_insensitive_tiers_and_providers() -> None:
    """Test that tier and provider parameters are case-insensitive."""
    # Test case insensitivity for tiers
    assert estimate_cost(10, tier="PRO", provider="xai") == 10
    assert estimate_cost(10, tier="Pro", provider="xai") == 10
    assert estimate_cost(10, tier="MAX", provider="xai") == 8
    assert estimate_cost(10, tier="Max", provider="xai") == 8


def test_unsupported_duration() -> None:
    """Test pricing for unsupported duration (should use per-second pricing)."""
    # For xAI with unsupported duration
    assert estimate_cost(7, tier="pro", provider="xai") == 7
    assert estimate_cost(7, tier="max", provider="xai") == 5


def test_fal_kling_pricing_o3_pro() -> None:
    assert (
        estimate_cost(5, tier="pro", provider="fal", kling_model="o3-pro", audio_enabled=False)
        == 10
    )
    assert (
        estimate_cost(10, tier="pro", provider="fal", kling_model="o3-pro", audio_enabled=False)
        == 20
    )
    assert (
        estimate_cost(15, tier="pro", provider="fal", kling_model="o3-pro", audio_enabled=False)
        == 30
    )

    assert (
        estimate_cost(5, tier="pro", provider="fal", kling_model="o3-pro", audio_enabled=True) == 10
    )
    assert (
        estimate_cost(10, tier="pro", provider="fal", kling_model="o3-pro", audio_enabled=True)
        == 20
    )
    assert (
        estimate_cost(15, tier="pro", provider="fal", kling_model="o3-pro", audio_enabled=True)
        == 30
    )


def test_fal_kling_pricing_v3_pro() -> None:
    assert (
        estimate_cost(5, tier="pro", provider="fal", kling_model="v3-pro", audio_enabled=False)
        == 10
    )
    assert (
        estimate_cost(10, tier="pro", provider="fal", kling_model="v3-pro", audio_enabled=False)
        == 20
    )
    assert (
        estimate_cost(15, tier="pro", provider="fal", kling_model="v3-pro", audio_enabled=False)
        == 30
    )

    assert (
        estimate_cost(5, tier="pro", provider="fal", kling_model="v3-pro", audio_enabled=True) == 15
    )
    assert (
        estimate_cost(10, tier="pro", provider="fal", kling_model="v3-pro", audio_enabled=True)
        == 30
    )
    assert (
        estimate_cost(15, tier="pro", provider="fal", kling_model="v3-pro", audio_enabled=True)
        == 45
    )


def test_fal_kling_pricing_tier_discounts() -> None:
    assert (
        estimate_cost(10, tier="pro", provider="fal", kling_model="o3-pro", audio_enabled=False)
        == 20
    )
    assert (
        estimate_cost(10, tier="max", provider="fal", kling_model="o3-pro", audio_enabled=False)
        == 16
    )
    assert (
        estimate_cost(10, tier="byok", provider="fal", kling_model="o3-pro", audio_enabled=False)
        == 0
    )


def test_fal_kling_case_insensitive() -> None:
    assert estimate_cost(5, tier="PRO", provider="FAL", kling_model="o3-pro") == 10
    assert estimate_cost(5, tier="Pro", provider="Fal", kling_model="o3-pro") == 10
    assert estimate_cost(5, tier="pro", provider="fal", kling_model="O3-PRO") == 10
    assert estimate_cost(5, tier="pro", provider="fal", kling_model="V3-PRO") == 10
