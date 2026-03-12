from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TierName = Literal["free", "starter", "pro", "max", "byok"]
ModalityType = Literal["text_and_image", "image_only"]
PricingModel = Literal["token", "flat_image", "megapixel", "resolution_tiered"]

DEFAULT_ASPECT_RATIOS: tuple[str, ...] = ("1:1", "16:9", "9:16", "4:3", "3:4", "21:9")
DEFAULT_RESOLUTIONS: tuple[str, ...] = ("1K", "2K", "4K")


@dataclass(frozen=True)
class ImageModel:
    id: str
    name: str
    provider: str
    modality_type: ModalityType
    supports_editing: bool
    supports_aspect_ratio: bool
    supported_aspect_ratios: tuple[str, ...]
    supports_resolution: bool
    supported_resolutions: tuple[str, ...]
    pricing_info: str
    tier_minimum: TierName
    pricing_model: PricingModel
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    flat_image_price_usd: float | None = None
    megapixel_price_usd: float | None = None
    first_megapixel_price_usd: float | None = None
    additional_megapixel_price_usd: float | None = None
    resolution_prices_usd: dict[str, float] | None = None
    notes: str | None = None


IMAGE_MODEL_CATALOG: tuple[ImageModel, ...] = (
    ImageModel(
        id="google/gemini-2.5-flash-image-preview",
        name="Nano Banana",
        provider="google",
        modality_type="text_and_image",
        supports_editing=True,
        supports_aspect_ratio=True,
        supported_aspect_ratios=DEFAULT_ASPECT_RATIOS,
        supports_resolution=True,
        supported_resolutions=DEFAULT_RESOLUTIONS,
        pricing_info="$0.002/M input, $0.012/M output",
        tier_minimum="starter",
        pricing_model="token",
        input_cost_per_million=0.002,
        output_cost_per_million=0.012,
    ),
    ImageModel(
        id="google/gemini-2.5-flash-image",
        name="Nano Banana 2",
        provider="google",
        modality_type="text_and_image",
        supports_editing=True,
        supports_aspect_ratio=True,
        supported_aspect_ratios=DEFAULT_ASPECT_RATIOS,
        supports_resolution=True,
        supported_resolutions=DEFAULT_RESOLUTIONS,
        pricing_info="$0.002/M input, $0.012/M output",
        tier_minimum="starter",
        pricing_model="token",
        input_cost_per_million=0.002,
        output_cost_per_million=0.012,
        notes="Modeled as GA replacement for the preview Nano Banana line.",
    ),
    ImageModel(
        id="google/gemini-3-pro-image-preview",
        name="Nano Banana Pro",
        provider="google",
        modality_type="text_and_image",
        supports_editing=True,
        supports_aspect_ratio=True,
        supported_aspect_ratios=DEFAULT_ASPECT_RATIOS,
        supports_resolution=True,
        supported_resolutions=DEFAULT_RESOLUTIONS,
        pricing_info="$2.00/M input, $12.00/M output",
        tier_minimum="pro",
        pricing_model="token",
        input_cost_per_million=2.0,
        output_cost_per_million=12.0,
    ),
    ImageModel(
        id="openai/gpt-5-image-mini",
        name="GPT-5 Image Mini",
        provider="openai",
        modality_type="text_and_image",
        supports_editing=True,
        supports_aspect_ratio=True,
        supported_aspect_ratios=DEFAULT_ASPECT_RATIOS,
        supports_resolution=True,
        supported_resolutions=DEFAULT_RESOLUTIONS,
        pricing_info="$2.50/M input, $2.00/M output",
        tier_minimum="starter",
        pricing_model="token",
        input_cost_per_million=2.5,
        output_cost_per_million=2.0,
    ),
    ImageModel(
        id="openai/gpt-5-image",
        name="GPT-5 Image",
        provider="openai",
        modality_type="text_and_image",
        supports_editing=True,
        supports_aspect_ratio=True,
        supported_aspect_ratios=DEFAULT_ASPECT_RATIOS,
        supports_resolution=True,
        supported_resolutions=DEFAULT_RESOLUTIONS,
        pricing_info="$10.00/M input, $10.00/M output",
        tier_minimum="pro",
        pricing_model="token",
        input_cost_per_million=10.0,
        output_cost_per_million=10.0,
    ),
    ImageModel(
        id="bytedance-seed/seedream-4.5",
        name="Seedream 4.5",
        provider="bytedance-seed",
        modality_type="image_only",
        supports_editing=True,
        supports_aspect_ratio=True,
        supported_aspect_ratios=DEFAULT_ASPECT_RATIOS,
        supports_resolution=True,
        supported_resolutions=DEFAULT_RESOLUTIONS,
        pricing_info="$0.04 per image",
        tier_minimum="starter",
        pricing_model="flat_image",
        flat_image_price_usd=0.04,
    ),
    ImageModel(
        id="black-forest-labs/flux.2-klein-4b",
        name="FLUX.2 Klein",
        provider="black-forest-labs",
        modality_type="image_only",
        supports_editing=True,
        supports_aspect_ratio=True,
        supported_aspect_ratios=DEFAULT_ASPECT_RATIOS,
        supports_resolution=True,
        supported_resolutions=DEFAULT_RESOLUTIONS,
        pricing_info="$0.014 first MP, $0.001 each additional MP",
        tier_minimum="starter",
        pricing_model="megapixel",
        first_megapixel_price_usd=0.014,
        additional_megapixel_price_usd=0.001,
    ),
    ImageModel(
        id="black-forest-labs/flux.2-flex",
        name="FLUX.2 Flex",
        provider="black-forest-labs",
        modality_type="image_only",
        supports_editing=True,
        supports_aspect_ratio=True,
        supported_aspect_ratios=DEFAULT_ASPECT_RATIOS,
        supports_resolution=True,
        supported_resolutions=DEFAULT_RESOLUTIONS,
        pricing_info="$0.06 per MP",
        tier_minimum="starter",
        pricing_model="megapixel",
        megapixel_price_usd=0.06,
    ),
    ImageModel(
        id="black-forest-labs/flux.2-pro",
        name="FLUX.2 Pro",
        provider="black-forest-labs",
        modality_type="image_only",
        supports_editing=True,
        supports_aspect_ratio=True,
        supported_aspect_ratios=DEFAULT_ASPECT_RATIOS,
        supports_resolution=True,
        supported_resolutions=DEFAULT_RESOLUTIONS,
        pricing_info="$0.03 first MP, $0.015 additional MP",
        tier_minimum="pro",
        pricing_model="megapixel",
        first_megapixel_price_usd=0.03,
        additional_megapixel_price_usd=0.015,
    ),
    ImageModel(
        id="black-forest-labs/flux.2-max",
        name="FLUX.2 Max",
        provider="black-forest-labs",
        modality_type="image_only",
        supports_editing=True,
        supports_aspect_ratio=True,
        supported_aspect_ratios=DEFAULT_ASPECT_RATIOS,
        supports_resolution=True,
        supported_resolutions=DEFAULT_RESOLUTIONS,
        pricing_info="$0.07 first MP, $0.03 additional MP",
        tier_minimum="max",
        pricing_model="megapixel",
        first_megapixel_price_usd=0.07,
        additional_megapixel_price_usd=0.03,
    ),
    ImageModel(
        id="sourceful/riverflow-v2-fast",
        name="Riverflow V2 Fast",
        provider="sourceful",
        modality_type="image_only",
        supports_editing=True,
        supports_aspect_ratio=True,
        supported_aspect_ratios=DEFAULT_ASPECT_RATIOS,
        supports_resolution=True,
        supported_resolutions=("1K", "2K"),
        pricing_info="$0.02 (1K), $0.04 (2K)",
        tier_minimum="starter",
        pricing_model="resolution_tiered",
        resolution_prices_usd={"1K": 0.02, "2K": 0.04},
    ),
    ImageModel(
        id="sourceful/riverflow-v2-pro",
        name="Riverflow V2 Pro",
        provider="sourceful",
        modality_type="image_only",
        supports_editing=True,
        supports_aspect_ratio=True,
        supported_aspect_ratios=DEFAULT_ASPECT_RATIOS,
        supports_resolution=True,
        supported_resolutions=DEFAULT_RESOLUTIONS,
        pricing_info="$0.15 (1K/2K), $0.33 (4K)",
        tier_minimum="pro",
        pricing_model="resolution_tiered",
        resolution_prices_usd={"1K": 0.15, "2K": 0.15, "4K": 0.33},
    ),
)

_TIER_ORDER: dict[TierName, int] = {
    "free": 0,
    "starter": 1,
    "pro": 2,
    "max": 3,
    "byok": 4,
}

_MODEL_BY_ID: dict[str, ImageModel] = {model.id: model for model in IMAGE_MODEL_CATALOG}


def get_image_models_for_tier(tier: TierName) -> list[ImageModel]:
    tier_rank = _TIER_ORDER.get(tier, _TIER_ORDER["free"])
    return [
        model
        for model in IMAGE_MODEL_CATALOG
        if _TIER_ORDER.get(model.tier_minimum, _TIER_ORDER["max"]) <= tier_rank
    ]


def get_image_model(model_id: str) -> ImageModel | None:
    return _MODEL_BY_ID.get(model_id)
