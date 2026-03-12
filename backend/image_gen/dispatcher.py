from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal

from backend.image_gen.provider import ImageProvider
from backend.image_gen.storage import ImageStorage

GenerationStatus = Literal["queued", "generating", "complete", "error"]


@dataclass(frozen=True)
class DispatchResult:
    model_id: str
    image_id: str
    image_url: str
    image_b64: str
    generation_time_ms: int
    cost_estimate: float
    width: int | None
    height: int | None


@dataclass(frozen=True)
class GenerationEvent:
    model_id: str
    status: GenerationStatus
    result: DispatchResult | None = None
    error: str | None = None


async def dispatch_parallel(
    models: list[str],
    prompt: str,
    reference_image_b64: str | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    provider: ImageProvider | None = None,
    storage: ImageStorage | None = None,
    max_concurrent: int = 4,
) -> AsyncGenerator[GenerationEvent, None]:
    if not models:
        raise ValueError("At least one model is required")
    if len(models) > 4:
        raise ValueError("A maximum of 4 models can be dispatched in one request")

    active_provider = provider or ImageProvider()
    active_storage = storage or ImageStorage()

    bounded_concurrency = max(1, min(max_concurrent, 4))
    semaphore = asyncio.Semaphore(bounded_concurrency)
    queue: asyncio.Queue[GenerationEvent] = asyncio.Queue()

    async def worker(model_id: str) -> None:
        async with semaphore:
            await queue.put(GenerationEvent(model_id=model_id, status="generating"))
            try:
                result = await active_provider.generate(
                    model_id=model_id,
                    prompt=prompt,
                    reference_image_b64=reference_image_b64,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                )
                image_id = active_storage.save_image(
                    result.image_b64,
                    {
                        "prompt": prompt,
                        "model": model_id,
                        "aspect_ratio": aspect_ratio,
                        "resolution": resolution,
                        "generation_time_ms": result.generation_time_ms,
                        "cost_estimate": result.cost_estimate,
                        "width": result.width,
                        "height": result.height,
                    },
                )

                dispatch_result = DispatchResult(
                    model_id=model_id,
                    image_id=image_id,
                    image_url=f"/api/images/{image_id}",
                    image_b64=result.image_b64,
                    generation_time_ms=result.generation_time_ms,
                    cost_estimate=result.cost_estimate,
                    width=result.width,
                    height=result.height,
                )
                await queue.put(
                    GenerationEvent(
                        model_id=model_id,
                        status="complete",
                        result=dispatch_result,
                    )
                )
            except Exception as exc:
                await queue.put(
                    GenerationEvent(
                        model_id=model_id,
                        status="error",
                        error=str(exc),
                    )
                )

    tasks = [asyncio.create_task(worker(model_id)) for model_id in models]

    for model_id in models:
        yield GenerationEvent(model_id=model_id, status="queued")

    completed = 0
    target_completions = len(models)
    while completed < target_completions:
        event = await queue.get()
        if event.status in {"complete", "error"}:
            completed += 1
        yield event

    _ = await asyncio.gather(*tasks, return_exceptions=True)
