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
    # Google Gemini models
    ImageModel(
        id="google/gemini-3.1-flash-image-preview",
        name="Nano Banana 2 (Gemini 3.1 Flash Image Preview)",
        provider="google",
        modality_type="text_and_image",
        supports_editing=True,
        supports_aspect_ratio=True,
        supported_aspect_ratios=DEFAULT_ASPECT_RATIOS + ("1:4", "4:1", "1:8", "8:1"),
        supports_resolution=True,
        supported_resolutions=("0.5K", "1K", "2K", "4K"),
        pricing_info="$0.50/M input, $3.00/M output tokens",
        tier_minimum="pro",
        pricing_model="token",
        input_cost_per_million=0.50,
        output_cost_per_million=3.00,
    ),
    ImageModel(
        id="google/gemini-2.5-flash-image",
        name="Nano Banana (Gemini 2.5 Flash Image)",
        provider="google",
        modality_type="text_and_image",
        supports_editing=True,
        supports_aspect_ratio=True,
        supported_aspect_ratios=DEFAULT_ASPECT_RATIOS,
        supports_resolution=True,
        supported_resolutions=DEFAULT_RESOLUTIONS,
        pricing_info="$0.30/M input, $2.50/M output tokens",
        tier_minimum="pro",
        pricing_model="token",
        input_cost_per_million=0.30,
        output_cost_per_million=2.50,
    ),
    ImageModel(
        id="google/gemini-3-pro-image-preview",
        name="Nano Banana Pro (Gemini 3 Pro Image Preview)",
        provider="google",
        modality_type="text_and_image",
        supports_editing=True,
        supports_aspect_ratio=True,
        supported_aspect_ratios=DEFAULT_ASPECT_RATIOS,
        supports_resolution=True,
        supported_resolutions=DEFAULT_RESOLUTIONS,
        pricing_info="$2.00/M input, $12.00/M output tokens",
        tier_minimum="max",
        pricing_model="token",
        input_cost_per_million=2.00,
        output_cost_per_million=12.00,
    ),
    # OpenAI GPT models
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
        pricing_info="$2.50/M input, $2.00/M output tokens",
        tier_minimum="pro",
        pricing_model="token",
        input_cost_per_million=2.50,
        output_cost_per_million=2.00,
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
        pricing_info="$10.00/M input, $10.00/M output tokens",
        tier_minimum="max",
        pricing_model="token",
        input_cost_per_million=10.00,
        output_cost_per_million=10.00,
    ),
    # Black Forest Labs FLUX models
    ImageModel(
        id="black-forest-labs/flux.2-klein-4b",
        name="FLUX.2 Klein 4B",
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
        tier_minimum="starter",
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
        tier_minimum="starter",
        pricing_model="megapixel",
        first_megapixel_price_usd=0.07,
        additional_megapixel_price_usd=0.03,
    ),
    # Sourceful Riverflow models (production)
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
        pricing_info="$0.02 per image (1K), $0.04 per image (2K)",
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
        pricing_info="$0.15 per image (1K/2K), $0.33 per image (4K)",
        tier_minimum="starter",
        pricing_model="resolution_tiered",
        resolution_prices_usd={"1K": 0.15, "2K": 0.15, "4K": 0.33},
    ),
    # Sourceful Riverflow models (preview)
    ImageModel(
        id="sourceful/riverflow-v2-fast-preview",
        name="Riverflow V2 Fast Preview",
        provider="sourceful",
        modality_type="image_only",
        supports_editing=True,
        supports_aspect_ratio=True,
        supported_aspect_ratios=DEFAULT_ASPECT_RATIOS,
        supports_resolution=True,
        supported_resolutions=DEFAULT_RESOLUTIONS,
        pricing_info="$0.03 per image",
        tier_minimum="starter",
        pricing_model="flat_image",
        flat_image_price_usd=0.03,
        notes="Preview model - $0.03 per output image regardless of size",
    ),
    ImageModel(
        id="sourceful/riverflow-v2-standard-preview",
        name="Riverflow V2 Standard Preview",
        provider="sourceful",
        modality_type="image_only",
        supports_editing=True,
        supports_aspect_ratio=True,
        supported_aspect_ratios=DEFAULT_ASPECT_RATIOS,
        supports_resolution=True,
        supported_resolutions=DEFAULT_RESOLUTIONS,
        pricing_info="$0.035 per image",
        tier_minimum="starter",
        pricing_model="flat_image",
        flat_image_price_usd=0.035,
        notes="Preview model - $0.035 per output image regardless of size",
    ),
    ImageModel(
        id="sourceful/riverflow-v2-max-preview",
        name="Riverflow V2 Max Preview",
        provider="sourceful",
        modality_type="image_only",
        supports_editing=True,
        supports_aspect_ratio=True,
        supported_aspect_ratios=DEFAULT_ASPECT_RATIOS,
        supports_resolution=True,
        supported_resolutions=DEFAULT_RESOLUTIONS,
        pricing_info="$0.075 per image",
        tier_minimum="starter",
        pricing_model="flat_image",
        flat_image_price_usd=0.075,
        notes="Preview model - $0.075 per output image regardless of size",
    ),
    # ByteDance Seed models
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
