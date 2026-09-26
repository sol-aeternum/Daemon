"""Video credits are independent of the account's subscription plan."""

from __future__ import annotations

from config.video_pricing import estimate_cost


def test_xai_duration_cost_is_plan_independent() -> None:
    for seconds in (5, 10, 15, 20, 30, 7):
        assert estimate_cost(seconds, provider="xai") == seconds


def test_fal_cost_accounts_for_model_and_audio() -> None:
    assert estimate_cost(5, provider="fal", kling_model="o3-pro") == 10
    assert estimate_cost(5, provider="fal", kling_model="o3-pro", audio_enabled=True) == 10
    assert estimate_cost(5, provider="fal", kling_model="v3-pro") == 10
    assert estimate_cost(5, provider="fal", kling_model="v3-pro", audio_enabled=True) == 15
    assert estimate_cost(5, provider="FAL", kling_model="o3-pro") == 10
