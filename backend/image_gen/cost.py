from __future__ import annotations

from math import ceil

from backend.image_gen.models import ImageModel, get_image_model

_DEFAULT_RESOLUTION = "1K"
_DEFAULT_ASPECT_RATIO = "1:1"

_MEGAPIXELS_BY_RESOLUTION: dict[str, float] = {
    "0.5K": 0.5,
    "1K": 1.0,
    "2K": 2.0,
    "4K": 4.0,
}

_ASPECT_RATIO_FACTORS: dict[str, float] = {
    "1:1": 1.0,
    "16:9": 1.12,
    "9:16": 1.12,
    "4:3": 1.06,
    "3:4": 1.06,
    "21:9": 1.2,
    "2:3": 1.08,
    "3:2": 1.08,
    "4:5": 1.1,
    "5:4": 1.1,
}


def estimate_cost(
    model_id: str,
    resolution: str | None,
    has_reference: bool,
    aspect_ratio: str | None = None,
) -> float:
    model = get_image_model(model_id)
    if model is None:
        raise ValueError(f"Unknown model_id: {model_id}")

    resolved_resolution = resolution or _DEFAULT_RESOLUTION
    resolved_aspect_ratio = aspect_ratio or _DEFAULT_ASPECT_RATIO

    cost = _estimate_by_model(
        model=model,
        resolution=resolved_resolution,
        has_reference=has_reference,
        aspect_ratio=resolved_aspect_ratio,
    )
    return round(max(cost, 0.0), 6)


def _estimate_by_model(
    *,
    model: ImageModel,
    resolution: str,
    has_reference: bool,
    aspect_ratio: str,
) -> float:
    if model.pricing_model == "token":
        return _estimate_token_cost(
            model=model, resolution=resolution, has_reference=has_reference
        )
    if model.pricing_model == "flat_image":
        return _estimate_flat_cost(model=model, has_reference=has_reference)
    if model.pricing_model == "megapixel":
        return _estimate_megapixel_cost(
            model=model,
            resolution=resolution,
            has_reference=has_reference,
            aspect_ratio=aspect_ratio,
        )
    if model.pricing_model == "resolution_tiered":
        return _estimate_resolution_tiered_cost(
            model=model, resolution=resolution, has_reference=has_reference
        )
    raise ValueError(f"Unsupported pricing model: {model.pricing_model}")


def _estimate_token_cost(
    *, model: ImageModel, resolution: str, has_reference: bool
) -> float:
    input_rate = model.input_cost_per_million or 0.0
    output_rate = model.output_cost_per_million or 0.0

    base_input_tokens = 2200
    reference_input_tokens = 12000 if has_reference else 0
    output_tokens_by_resolution = {
        "0.5K": 11000,
        "1K": 22000,
        "2K": 44000,
        "4K": 88000,
    }
    output_tokens = output_tokens_by_resolution.get(
        resolution, output_tokens_by_resolution[_DEFAULT_RESOLUTION]
    )

    input_cost = (
        (base_input_tokens + reference_input_tokens) / 1_000_000.0
    ) * input_rate
    output_cost = (output_tokens / 1_000_000.0) * output_rate
    return input_cost + output_cost


def _estimate_flat_cost(*, model: ImageModel, has_reference: bool) -> float:
    base_price = model.flat_image_price_usd or 0.0
    if not has_reference:
        return base_price
    return base_price * 1.2


def _estimate_megapixel_cost(
    *,
    model: ImageModel,
    resolution: str,
    has_reference: bool,
    aspect_ratio: str,
) -> float:
    megapixels = _resolution_megapixels(
        resolution=resolution, aspect_ratio=aspect_ratio
    )

    if model.first_megapixel_price_usd is not None:
        first = model.first_megapixel_price_usd
        additional = model.additional_megapixel_price_usd or first
        additional_mp = max(megapixels - 1.0, 0.0)
        total = first + (additional_mp * additional)
    else:
        per_mp = model.megapixel_price_usd or 0.0
        total = megapixels * per_mp

    if has_reference:
        total *= 1.08
    return total


def _estimate_resolution_tiered_cost(
    *, model: ImageModel, resolution: str, has_reference: bool
) -> float:
    prices = model.resolution_prices_usd or {}
    base = prices.get(resolution)
    if base is None:
        base = prices.get(_DEFAULT_RESOLUTION, next(iter(prices.values()), 0.0))

    if has_reference:
        return base * 1.1
    return base


def _resolution_megapixels(*, resolution: str, aspect_ratio: str) -> float:
    base_mp = _MEGAPIXELS_BY_RESOLUTION.get(
        resolution, _MEGAPIXELS_BY_RESOLUTION[_DEFAULT_RESOLUTION]
    )
    ratio_factor = _ASPECT_RATIO_FACTORS.get(aspect_ratio, 1.0)
    value = base_mp * ratio_factor
    return ceil(value * 1000) / 1000.0
