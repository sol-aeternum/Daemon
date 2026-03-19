from __future__ import annotations

import pytest

from config.video_pricing import estimate_cost


def test_xai_pricing_backward_compatibility() -> None:
    """Test xAI pricing remains unchanged for backward compatibility."""
    assert estimate_cost(10, tier="pro", provider="xai") == 10
    assert estimate_cost(10, tier="max", provider="xai") == 8
    assert estimate_cost(10, tier="byok", provider="xai") == 0

    # Test all durations with xAI
    assert estimate_cost(5, tier="pro", provider="xai") == 5
    assert estimate_cost(15, tier="pro", provider="xai") == 15
    assert estimate_cost(20, tier="pro", provider="xai") == 20
    assert estimate_cost(30, tier="pro", provider="xai") == 30


def test_sora_pricing_default() -> None:
    assert estimate_cost(5, tier="pro", provider="sora") == 30
    assert estimate_cost(10, tier="pro", provider="sora") == 60
    assert estimate_cost(15, tier="pro", provider="sora") == 90
    assert estimate_cost(20, tier="pro", provider="sora") == 120
    assert estimate_cost(30, tier="pro", provider="sora") == 180

    # Test with openai_sora provider (same pricing)
    assert estimate_cost(10, tier="pro", provider="openai_sora") == 60


def test_sora_pricing_pro_tier() -> None:
    """Test Sora sora-2-pro Pro tier pricing."""
    assert estimate_cost(5, tier="pro", provider="sora") == 30
    assert estimate_cost(10, tier="pro", provider="sora") == 60
    assert estimate_cost(15, tier="pro", provider="sora") == 90
    assert estimate_cost(20, tier="pro", provider="sora") == 120
    assert estimate_cost(30, tier="pro", provider="sora") == 180


def test_sora_pricing_max_tier() -> None:
    """Test Sora sora-2-pro Max tier pricing."""
    assert estimate_cost(5, tier="max", provider="sora") == 50
    assert estimate_cost(10, tier="max", provider="sora") == 100
    assert estimate_cost(15, tier="max", provider="sora") == 150
    assert estimate_cost(20, tier="max", provider="sora") == 200
    assert estimate_cost(30, tier="max", provider="sora") == 300


def test_sora_pricing_other_tiers() -> None:
    """Test Sora pricing with other tiers (should use default sora-2 pricing)."""
    # BYOK tier with Sora should use default pricing (2 credits/sec)
    assert estimate_cost(10, tier="byok", provider="sora") == 20

    # Starter tier with Sora should use default pricing (2 credits/sec)
    assert estimate_cost(10, tier="starter", provider="sora") == 20

    # Any unrecognized tier with Sora should use default pricing (2 credits/sec)
    assert estimate_cost(10, tier="unknown", provider="sora") == 20


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

    # Sora default (sora-2)
    sora_expected = [10, 20, 30, 40, 60]  # 2 credits/sec
    for duration, expected in zip(durations, sora_expected):
        assert estimate_cost(duration, tier="starter", provider="sora") == expected

    # Sora Pro tier (sora-2-pro)
    sora_pro_expected = [30, 60, 90, 120, 180]  # 6 credits/sec
    for duration, expected in zip(durations, sora_pro_expected):
        assert estimate_cost(duration, tier="pro", provider="sora") == expected

    # Sora Max tier (sora-2-pro)
    sora_max_expected = [50, 100, 150, 200, 300]  # 10 credits/sec
    for duration, expected in zip(durations, sora_max_expected):
        assert estimate_cost(duration, tier="max", provider="sora") == expected


def test_case_insensitive_tiers_and_providers() -> None:
    """Test that tier and provider parameters are case-insensitive."""
    # Test case insensitivity for tiers
    assert estimate_cost(10, tier="PRO", provider="xai") == 10
    assert estimate_cost(10, tier="Pro", provider="xai") == 10
    assert estimate_cost(10, tier="MAX", provider="xai") == 8
    assert estimate_cost(10, tier="Max", provider="xai") == 8

    # Test case insensitivity for providers
    assert estimate_cost(10, tier="pro", provider="SORA") == 60
    assert estimate_cost(10, tier="pro", provider="Sora") == 60
    assert estimate_cost(10, tier="pro", provider="OPENAI_SORA") == 60
    assert estimate_cost(10, tier="pro", provider="Openai_Sora") == 60
    assert estimate_cost(10, tier="max", provider="SORA") == 100
    assert estimate_cost(10, tier="max", provider="Openai_Sora") == 100


def test_unsupported_duration() -> None:
    """Test pricing for unsupported duration (should use per-second pricing)."""
    # For xAI with unsupported duration
    assert estimate_cost(7, tier="pro", provider="xai") == 7
    assert estimate_cost(7, tier="max", provider="xai") == 5

    # For Sora with unsupported duration
    assert estimate_cost(7, tier="pro", provider="sora") == 42
    assert estimate_cost(7, tier="max", provider="sora") == 70
    assert estimate_cost(7, tier="starter", provider="sora") == 14
