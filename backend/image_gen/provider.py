from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from time import perf_counter
from typing import cast

import httpx

from backend.image_gen.cost import estimate_cost
from backend.image_gen.models import ImageModel, get_image_model
from orchestrator.config import Settings, get_settings


@dataclass(frozen=True)
class ImageResult:
    image_b64: str
    model_id: str
    generation_time_ms: int
    cost_estimate: float
    width: int | None
    height: int | None


class ImageProvider:
    settings: Settings

    def __init__(self) -> None:
        self.settings = get_settings()

    async def generate(
        self,
        model_id: str,
        prompt: str,
        reference_image_b64: str | None = None,
        aspect_ratio: str | None = None,
        resolution: str | None = None,
    ) -> ImageResult:
        model = get_image_model(model_id)
        if model is None:
            raise ValueError(f"Unsupported image model: {model_id}")

        if (
            aspect_ratio
            and model.supports_aspect_ratio
            and aspect_ratio not in model.supported_aspect_ratios
        ):
            raise ValueError(
                f"Aspect ratio {aspect_ratio} is not supported for model {model_id}"
            )

        if (
            resolution
            and model.supports_resolution
            and resolution not in model.supported_resolutions
        ):
            raise ValueError(
                f"Resolution {resolution} is not supported for model {model_id}"
            )

        payload = self._build_payload(
            model=model,
            prompt=prompt,
            reference_image_b64=reference_image_b64,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
        )
        headers = self._build_headers()

        started = perf_counter()
        async with httpx.AsyncClient(
            timeout=max(self.settings.request_timeout_s, 120)
        ) as client:
            response = await client.post(
                f"{self.settings.openrouter_base_url}/chat/completions",
                headers=headers,
                json=payload,
            )

        _ = response.raise_for_status()
        elapsed_ms = int((perf_counter() - started) * 1000)

        body = cast(object, response.json())
        image_b64 = self._extract_image_b64(body)
        width, height = _dimensions_from_base64(image_b64)
        cost = estimate_cost(
            model_id=model_id,
            resolution=resolution,
            has_reference=reference_image_b64 is not None,
            aspect_ratio=aspect_ratio,
        )

        return ImageResult(
            image_b64=image_b64,
            model_id=model_id,
            generation_time_ms=elapsed_ms,
            cost_estimate=cost,
            width=width,
            height=height,
        )

    def _build_headers(self) -> dict[str, str]:
        if not self.settings.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is required for image generation")

        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
        }
        if self.settings.openrouter_referer:
            headers["HTTP-Referer"] = self.settings.openrouter_referer
        if self.settings.openrouter_title:
            headers["X-Title"] = self.settings.openrouter_title
        return headers

    def _build_payload(
        self,
        *,
        model: ImageModel,
        prompt: str,
        reference_image_b64: str | None,
        aspect_ratio: str | None,
        resolution: str | None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": model.id,
            "messages": self._build_messages(
                model=model, prompt=prompt, reference_image_b64=reference_image_b64
            ),
            "modalities": ["image", "text"]
            if model.modality_type == "text_and_image"
            else ["image"],
        }

        image_config: dict[str, str] = {}
        if aspect_ratio and model.supports_aspect_ratio:
            image_config["aspect_ratio"] = aspect_ratio
        if resolution and model.supports_resolution:
            image_config["image_size"] = resolution
        if image_config:
            payload["image_config"] = image_config

        return payload

    def _build_messages(
        self,
        *,
        model: ImageModel,
        prompt: str,
        reference_image_b64: str | None,
    ) -> list[dict[str, object]]:
        if reference_image_b64 is None:
            return [{"role": "user", "content": prompt}]

        reference_url = _to_data_url(reference_image_b64)
        if model.modality_type == "text_and_image":
            return [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Use this image as visual reference for an edited variant.",
                        },
                        {"type": "image_url", "image_url": {"url": reference_url}},
                    ],
                },
                {"role": "user", "content": prompt},
            ]

        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": reference_url}},
                ],
            }
        ]

    def _extract_image_b64(self, body: object) -> str:
        body_dict = _as_object_dict(
            body, "Image provider response is not a JSON object"
        )

        error_obj = body_dict.get("error")
        if error_obj:
            raise RuntimeError(f"Image provider returned error: {error_obj}")

        choices = _as_object_list(
            body_dict.get("choices"), "No choices found in image provider response"
        )
        if not choices:
            raise ValueError("No choices found in image provider response")

        first_choice = _as_object_dict(
            choices[0], "Invalid choice payload in image provider response"
        )
        message = _as_object_dict(
            first_choice.get("message"), "No message payload in image provider response"
        )

        image_b64 = _extract_from_images_field(message)
        if image_b64:
            return image_b64

        image_b64 = _extract_from_content_field(message)
        if image_b64:
            return image_b64

        raise ValueError("No image payload found in provider response")


def _to_data_url(reference_image_b64: str) -> str:
    if reference_image_b64.startswith("data:image"):
        return reference_image_b64
    return f"data:image/png;base64,{reference_image_b64}"


def _extract_from_images_field(message: dict[str, object]) -> str | None:
    images = message.get("images")
    if not isinstance(images, list):
        return None
    images_list = cast(list[object], images)

    for image_item in images_list:
        image_item_dict = _as_object_dict_or_none(image_item)
        if image_item_dict is None:
            continue

        image_url_obj = _as_object_dict_or_none(image_item_dict.get("image_url"))
        if image_url_obj is not None:
            url = image_url_obj.get("url")
            if isinstance(url, str):
                extracted = _extract_b64_from_url(url)
                if extracted:
                    return extracted

        b64_json = image_item_dict.get("b64_json")
        if isinstance(b64_json, str) and b64_json:
            return b64_json

    return None


def _extract_from_content_field(message: dict[str, object]) -> str | None:
    content = message.get("content")

    if isinstance(content, str):
        return _extract_b64_from_url(content)

    if not isinstance(content, list):
        return None
    content_list = cast(list[object], content)

    for part in content_list:
        part_dict = _as_object_dict_or_none(part)
        if part_dict is None:
            continue

        image_url_obj = _as_object_dict_or_none(part_dict.get("image_url"))
        if image_url_obj is not None:
            url = image_url_obj.get("url")
            if isinstance(url, str):
                extracted = _extract_b64_from_url(url)
                if extracted:
                    return extracted

    return None


def _extract_b64_from_url(value: str) -> str | None:
    if not value:
        return None
    if value.startswith("data:image") and "," in value:
        return value.split(",", 1)[1]
    return None


def _dimensions_from_base64(image_b64: str) -> tuple[int | None, int | None]:
    try:
        image_bytes = base64.b64decode(image_b64, validate=True)
    except (binascii.Error, ValueError):
        return None, None

    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n") and len(image_bytes) >= 24:
        width = int.from_bytes(image_bytes[16:20], "big")
        height = int.from_bytes(image_bytes[20:24], "big")
        return width, height

    if len(image_bytes) >= 2 and image_bytes[0:2] == b"\xff\xd8":
        return _jpeg_dimensions(image_bytes)

    return None, None


def _jpeg_dimensions(data: bytes) -> tuple[int | None, int | None]:
    index = 2
    length = len(data)

    while index + 9 < length:
        if data[index] != 0xFF:
            index += 1
            continue

        marker = data[index + 1]
        index += 2

        if marker in {0xD8, 0xD9}:
            continue

        if index + 2 > length:
            break

        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > length:
            break

        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            if index + 7 <= length:
                height = int.from_bytes(data[index + 3 : index + 5], "big")
                width = int.from_bytes(data[index + 5 : index + 7], "big")
                return width, height
            return None, None

        index += segment_length

    return None, None


def _as_object_dict(value: object, error_message: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(error_message)
    return cast(dict[str, object], value)


def _as_object_dict_or_none(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def _as_object_list(value: object, error_message: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(error_message)
    return cast(list[object], value)
