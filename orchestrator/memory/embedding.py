"""Embedding utility for text embeddings with provider fallback and retry logic."""

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
VOYAGE_CIRCUIT_BREAKER_FAILURES = 5
VOYAGE_CIRCUIT_BREAKER_WINDOW_S = 60.0

_retry_count = 0
_last_retry_at: float | None = None
_embedding_failures_total = 0
_embedding_provider_used: dict[str, int] = {"voyage": 0, "openai": 0}
_voyage_failure_timestamps: list[float] = []


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


@lru_cache(maxsize=1)
def _get_openai_api_key() -> str:
    settings = get_settings()
    api_key = settings.openai_api_key
    if not api_key:
        raise EmbeddingConfigurationError("OPENAI_API_KEY environment variable not set")
    return api_key


def get_configured_embedding_providers() -> tuple[str, ...]:
    settings = get_settings()
    fallback_raw = getattr(settings, "embedding_fallback_providers", "openai")
    fallbacks = [
        provider.strip().lower() for provider in str(fallback_raw).split(",") if provider.strip()
    ]
    providers: list[str] = ["voyage"]
    providers.extend(provider for provider in fallbacks if provider not in providers)
    return tuple(providers)


def get_embedding_provider_used_counts() -> dict[str, int]:
    return dict(_embedding_provider_used)


def get_embedding_failures_total() -> int:
    return _embedding_failures_total


def reset_embedding_metrics_for_tests() -> None:
    global _embedding_failures_total, _last_retry_at, _retry_count
    _retry_count = 0
    _last_retry_at = None
    _embedding_failures_total = 0
    _embedding_provider_used.clear()
    _embedding_provider_used.update({"voyage": 0, "openai": 0})
    _voyage_failure_timestamps.clear()
    _get_voyage_api_key.cache_clear()
    _get_openai_api_key.cache_clear()


def _record_provider_used(provider: str) -> None:
    _embedding_provider_used[provider] = _embedding_provider_used.get(provider, 0) + 1


def _record_provider_failure(provider: str) -> None:
    global _embedding_failures_total
    _embedding_failures_total += 1
    if provider != "voyage":
        return

    now = asyncio.get_running_loop().time()
    _voyage_failure_timestamps.append(now)
    _prune_voyage_failures(now)


def _prune_voyage_failures(now: float) -> None:
    cutoff = now - VOYAGE_CIRCUIT_BREAKER_WINDOW_S
    kept = [timestamp for timestamp in _voyage_failure_timestamps if timestamp >= cutoff]
    _voyage_failure_timestamps[:] = kept


def _is_voyage_circuit_open() -> bool:
    now = asyncio.get_running_loop().time()
    _prune_voyage_failures(now)
    return len(_voyage_failure_timestamps) >= VOYAGE_CIRCUIT_BREAKER_FAILURES


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


async def _post_openai_embeddings(
    *,
    api_key: str,
    texts: list[str],
    model: str,
    output_dimension: int,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "input": texts,
                "model": model,
                "dimensions": output_dimension,
            },
        )
        response.raise_for_status()
        return response.json()


def _parse_embedding_payload(
    payload: dict[str, Any],
    *,
    provider: str,
    texts_count: int,
    output_dimension: int,
) -> tuple[list[list[float]], int]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise EmbeddingRequestError(f"Invalid {provider} embedding response: missing data list")
    ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
    embeddings = [list(item.get("embedding", [])) for item in ordered]
    if len(embeddings) != texts_count:
        raise EmbeddingRequestError(
            f"Embedding response size mismatch: expected {texts_count} got {len(embeddings)}"
        )
    for index, vector in enumerate(embeddings):
        if len(vector) != output_dimension:
            raise EmbeddingRequestError(
                f"Embedding dimension mismatch at index {index}: expected {output_dimension} got {len(vector)}"
            )
    usage = payload.get("usage")
    total_tokens = 0
    if isinstance(usage, dict):
        total_tokens = int(usage.get("total_tokens", usage.get("prompt_tokens", 0)) or 0)
    return embeddings, total_tokens


async def _embed_with_voyage_retry(
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
            return _parse_embedding_payload(
                payload,
                provider="Voyage",
                texts_count=len(texts),
                output_dimension=output_dimension,
            )
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


async def _embed_with_openai_retry(
    texts: list[str],
    *,
    model: str,
    output_dimension: int,
) -> tuple[list[list[float]], int]:
    global _retry_count, _last_retry_at

    if not texts:
        return [], 0

    api_key = _get_openai_api_key()
    last_error: Exception | None = None
    backoff = INITIAL_BACKOFF_S

    for attempt in range(MAX_RETRIES):
        try:
            payload = await _post_openai_embeddings(
                api_key=api_key,
                texts=texts,
                model=model,
                output_dimension=output_dimension,
            )
            return _parse_embedding_payload(
                payload,
                provider="OpenAI",
                texts_count=len(texts),
                output_dimension=output_dimension,
            )
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
        f"Failed to embed with OpenAI after {MAX_RETRIES} attempts: {last_error}"
    ) from last_error


async def _embed_chunk_with_fallback(
    texts: list[str],
    *,
    model: str,
    input_type: str,
    output_dimension: int,
) -> tuple[list[list[float]], int, str]:
    provider_errors: list[str] = []

    if not _is_voyage_circuit_open():
        try:
            embeddings, tokens = await _embed_with_voyage_retry(
                texts,
                model=model,
                input_type=input_type,
                output_dimension=output_dimension,
            )
            _record_provider_used("voyage")
            return embeddings, tokens, "voyage"
        except Exception as error:
            _record_provider_failure("voyage")
            provider_errors.append(f"voyage: {error}")
            logger.warning("Voyage embedding provider failed; trying fallback", exc_info=True)
    else:
        provider_errors.append("voyage: circuit open")

    settings = get_settings()
    if "openai" in get_configured_embedding_providers():
        fallback_model = getattr(
            settings,
            "embedding_openai_fallback_model",
            "text-embedding-3-small",
        )
        try:
            embeddings, tokens = await _embed_with_openai_retry(
                texts,
                model=fallback_model,
                output_dimension=output_dimension,
            )
            _record_provider_used("openai")
            return embeddings, tokens, "openai"
        except Exception as error:
            _record_provider_failure("openai")
            provider_errors.append(f"openai: {error}")

    raise EmbeddingRequestError("; ".join(provider_errors))


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
    provider_counts: dict[str, int] = {}

    for chunk in chunks:
        embeddings, chunk_tokens, provider = await _embed_chunk_with_fallback(
            chunk,
            model=model,
            input_type=input_type,
            output_dimension=output_dimension,
        )
        all_embeddings.extend(embeddings)
        total_tokens += chunk_tokens
        provider_counts[provider] = provider_counts.get(provider, 0) + 1

    logger.info(
        "Embeddings generated",
        extra={
            "embedding_model": model,
            "input_type": input_type,
            "texts": len(valid_texts),
            "chunks": len(chunks),
            "providers": provider_counts,
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
