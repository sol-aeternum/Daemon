"""Embedding utility for Voyage text embeddings with retry logic."""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Any

import httpx

from orchestrator.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "voyage-4-large"
MAX_RETRIES = 3
INITIAL_BACKOFF_S = 1.0
MAX_ITEMS_PER_REQUEST = 1000
DOCUMENT_MAX_TOKENS = 120_000
QUERY_MAX_TOKENS = 1_000_000

_retry_count = 0
_last_retry_at: float | None = None


class EmbeddingError(Exception):
    pass


class EmbeddingConfigurationError(EmbeddingError):
    pass


class EmbeddingRequestError(EmbeddingError):
    pass


@lru_cache(maxsize=1)
def _get_voyage_api_key() -> str:
    settings = get_settings()
    api_key = settings.voyage_api_key
    if not api_key:
        raise EmbeddingConfigurationError("VOYAGE_API_KEY environment variable not set")
    return api_key


def _estimate_tokens(text: str) -> int:
    length = len(text)
    if length <= 0:
        return 0
    return max(1, length // 4)


def _chunk_texts(texts: list[str], max_tokens: int) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0

    for text in texts:
        estimate = _estimate_tokens(text)
        if estimate >= max_tokens:
            if current:
                chunks.append(current)
                current = []
                current_tokens = 0
            chunks.append([text])
            continue

        would_overflow_items = len(current) >= MAX_ITEMS_PER_REQUEST
        would_overflow_tokens = current_tokens + estimate > max_tokens
        if current and (would_overflow_items or would_overflow_tokens):
            chunks.append(current)
            current = []
            current_tokens = 0

        current.append(text)
        current_tokens += estimate

    if current:
        chunks.append(current)
    return chunks


async def _post_embeddings(
    *,
    api_key: str,
    texts: list[str],
    model: str,
    input_type: str,
    output_dimension: int,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "input": texts,
                "model": model,
                "input_type": input_type,
                "output_dimension": output_dimension,
            },
        )
        response.raise_for_status()
        return response.json()


async def _embed_with_retry(
    texts: list[str],
    *,
    model: str,
    input_type: str,
    output_dimension: int,
) -> tuple[list[list[float]], int]:
    global _retry_count, _last_retry_at

    if not texts:
        return [], 0

    api_key = _get_voyage_api_key()
    last_error: Exception | None = None
    backoff = INITIAL_BACKOFF_S

    for attempt in range(MAX_RETRIES):
        try:
            payload = await _post_embeddings(
                api_key=api_key,
                texts=texts,
                model=model,
                input_type=input_type,
                output_dimension=output_dimension,
            )
            data = payload.get("data")
            if not isinstance(data, list):
                raise EmbeddingRequestError("Invalid Voyage embedding response: missing data list")
            ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
            embeddings = [list(item.get("embedding", [])) for item in ordered]
            if len(embeddings) != len(texts):
                raise EmbeddingRequestError(
                    f"Embedding response size mismatch: expected {len(texts)} got {len(embeddings)}"
                )
            for index, vector in enumerate(embeddings):
                if len(vector) != output_dimension:
                    raise EmbeddingRequestError(
                        f"Embedding dimension mismatch at index {index}: expected {output_dimension} got {len(vector)}"
                    )
            usage = payload.get("usage")
            total_tokens = 0
            if isinstance(usage, dict):
                total_tokens = int(usage.get("total_tokens", 0) or 0)
            return embeddings, total_tokens
        except Exception as error:
            last_error = error
            if attempt < MAX_RETRIES - 1:
                _retry_count += 1
                _last_retry_at = asyncio.get_event_loop().time()
                await asyncio.sleep(backoff)
                backoff *= 2
                continue
            break

    raise EmbeddingRequestError(
        f"Failed to embed with Voyage after {MAX_RETRIES} attempts: {last_error}"
    ) from last_error


async def _embed_texts(
    texts: list[str],
    *,
    model: str,
    input_type: str,
    max_tokens: int,
) -> list[list[float]]:
    valid_texts = [t for t in texts if t and t.strip()]
    if not valid_texts:
        return []

    settings = get_settings()
    output_dimension = settings.embedding_dimensions
    chunks = _chunk_texts(valid_texts, max_tokens=max_tokens)
    all_embeddings: list[list[float]] = []
    total_tokens = 0

    for chunk in chunks:
        embeddings, chunk_tokens = await _embed_with_retry(
            chunk,
            model=model,
            input_type=input_type,
            output_dimension=output_dimension,
        )
        all_embeddings.extend(embeddings)
        total_tokens += chunk_tokens

    logger.info(
        "Voyage embeddings generated",
        extra={
            "embedding_model": model,
            "input_type": input_type,
            "texts": len(valid_texts),
            "chunks": len(chunks),
            "output_dimension": output_dimension,
            "total_tokens": total_tokens,
        },
    )
    return all_embeddings


async def embed_documents(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    return await _embed_texts(
        texts,
        model=settings.embedding_document_model,
        input_type="document",
        max_tokens=DOCUMENT_MAX_TOKENS,
    )


async def embed_query(text: str) -> list[float]:
    settings = get_settings()
    embeddings = await _embed_texts(
        [text],
        model=settings.embedding_query_model,
        input_type="query",
        max_tokens=QUERY_MAX_TOKENS,
    )
    if not embeddings:
        raise EmbeddingRequestError("Cannot embed empty or whitespace-only query text")
    return embeddings[0]


async def embed_text(text: str, model: str = DEFAULT_MODEL) -> list[float]:
    del model
    return await embed_query(text)


async def embed_batch(texts: list[str], model: str = DEFAULT_MODEL) -> list[list[float]]:
    del model
    return await embed_documents(texts)
