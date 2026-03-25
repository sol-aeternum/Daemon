"""Configuration for Council deliberation."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from dataclasses import dataclass, field

import yaml


DEFAULT_ROLE_TIMEOUT_SECONDS = 90.0


@dataclass
class PerspectiveConfig:
    """Configuration for a single perspective."""

    name: str
    system_prompt: str
    model: str | None = None
    temperature: float = 0.7


@dataclass
class CouncilSettings:
    """Global settings for council deliberation."""

    default_perspectives: list[str] = field(
        default_factory=lambda: ["expert", "skeptic", "creative"]
    )
    default_rounds: int = 3
    default_model: str | None = None
    enable_audit: bool = True
    max_concurrent_responses: int = 3


DEFAULT_SETTINGS = CouncilSettings()


def get_perspective_config(perspective_type: str) -> PerspectiveConfig:
    """Get configuration for a perspective type."""
    configs = {
        "expert": PerspectiveConfig(
            name="expert",
            system_prompt="You are an expert providing technical analysis.",
        ),
        "skeptic": PerspectiveConfig(
            name="skeptic",
            system_prompt="You are a skeptic identifying potential issues.",
        ),
        "creative": PerspectiveConfig(
            name="creative",
            system_prompt="You are a creative thinker providing novel approaches.",
        ),
        "pragmatic": PerspectiveConfig(
            name="pragmatic",
            system_prompt="You are a pragmatic advisor focusing on practical solutions.",
        ),
        "ethical": PerspectiveConfig(
            name="ethical",
            system_prompt="You are an ethical advisor evaluating moral implications.",
        ),
    }
    return configs.get(perspective_type, configs["expert"])


def load_roster(preset: str = "default") -> dict[str, str]:
    """Load roster configuration from roster.yaml.

    Args:
        preset: Name of preset to use (default, adversarial, lean).

    Returns:
        Dict mapping role -> model_id for active seats only.
    """
    role_config = load_roster_with_metadata(preset)
    return {role: cfg["model_id"] for role, cfg in role_config.items()}


def load_roster_with_metadata(preset: str = "default") -> dict[str, dict[str, Any]]:
    roster_path = Path(__file__).parent / "roster.yaml"
    with roster_path.open() as f:
        data = yaml.safe_load(f)

    presets = data.get("presets", {}) if isinstance(data, dict) else {}
    roles = data.get("roles", {}) if isinstance(data, dict) else {}
    default_preset = presets.get("default", {}) if isinstance(presets, dict) else {}
    preset_config = (
        presets.get(preset, default_preset) if isinstance(presets, dict) else {}
    )
    active_seats = preset_config.get("active_seats", [])

    role_map: dict[str, dict[str, Any]] = {}
    for role in active_seats:
        role_payload = roles.get(role, {})
        if not isinstance(role_payload, dict):
            continue
        role_map[role] = dict(role_payload)
    return role_map


def load_role_timeouts(preset: str = "default") -> dict[str, float]:
    role_config = load_roster_with_metadata(preset)
    timeouts: dict[str, float] = {}

    for role, cfg in role_config.items():
        raw_timeout = cfg.get("timeout_s", DEFAULT_ROLE_TIMEOUT_SECONDS)
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError):
            timeout = DEFAULT_ROLE_TIMEOUT_SECONDS
        if timeout <= 0:
            timeout = DEFAULT_ROLE_TIMEOUT_SECONDS
        timeouts[role] = timeout

    return timeouts
