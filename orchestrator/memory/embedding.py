"""Embedding utility for text embeddings with provider fallback and retry logic."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
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
OPENAI_MAX_TOKENS_PER_INPUT = 8_000
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


@dataclass(frozen=True)
class EmbeddingBatchResult:
    embeddings: list[list[float]]
    provider: str
    model: str
    storage_model: str


@dataclass(frozen=True)
class EmbeddingVectorResult:
    embedding: list[float]
    provider: str
    model: str
    storage_model: str


class EmbeddingVector(list[float]):
    def __init__(
        self,
        values: list[float],
        *,
        provider: str,
        model: str,
        storage_model: str,
    ) -> None:
        super().__init__(values)
        self.provider = provider
        self.model = model
        self.storage_model = storage_model


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


def _truncate_text_to_token_limit(text: str, max_tokens: int) -> str:
    if _estimate_tokens(text) <= max_tokens:
        return text
    return text[: max_tokens * 4]


def _openai_model_identity(model: str) -> str:
    return f"openai:{model}"


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


async def _embed_texts(
    texts: list[str],
    *,
    model: str,
    input_type: str,
    max_tokens: int,
) -> EmbeddingBatchResult:
    valid_texts = [t for t in texts if t and t.strip()]
    if not valid_texts:
        return EmbeddingBatchResult(
            embeddings=[],
            provider="none",
            model=model,
            storage_model=model,
        )

    settings = get_settings()
    output_dimension = settings.embedding_dimensions
    voyage_chunks = _chunk_texts(valid_texts, max_tokens=max_tokens)

    if not _is_voyage_circuit_open():
        try:
            all_embeddings: list[list[float]] = []
            total_tokens = 0
            for chunk in voyage_chunks:
                embeddings, chunk_tokens = await _embed_with_voyage_retry(
                    chunk,
                    model=model,
                    input_type=input_type,
                    output_dimension=output_dimension,
                )
                all_embeddings.extend(embeddings)
                total_tokens += chunk_tokens
            _record_provider_used("voyage")
            logger.info(
                "Embeddings generated",
                extra={
                    "embedding_model": model,
                    "input_type": input_type,
                    "texts": len(valid_texts),
                    "chunks": len(voyage_chunks),
                    "providers": {"voyage": 1},
                    "output_dimension": output_dimension,
                    "total_tokens": total_tokens,
                },
            )
            return EmbeddingBatchResult(
                embeddings=all_embeddings,
                provider="voyage",
                model=model,
                storage_model=model,
            )
        except Exception as error:
            _record_provider_failure("voyage")
            logger.warning("Voyage embedding provider failed; trying fallback", exc_info=True)
            voyage_error = error
    else:
        voyage_error = EmbeddingRequestError("voyage: circuit open")

    if "openai" not in get_configured_embedding_providers():
        raise EmbeddingRequestError(f"voyage: {voyage_error}") from voyage_error

    fallback_model = getattr(
        settings,
        "embedding_openai_fallback_model",
        "text-embedding-3-small",
    )
    openai_texts = [
        _truncate_text_to_token_limit(text, OPENAI_MAX_TOKENS_PER_INPUT) for text in valid_texts
    ]
    openai_chunks = _chunk_texts(openai_texts, max_tokens=OPENAI_MAX_TOKENS_PER_INPUT)
    all_embeddings = []
    total_tokens = 0
    try:
        for chunk in openai_chunks:
            embeddings, chunk_tokens = await _embed_with_openai_retry(
                chunk,
                model=fallback_model,
                output_dimension=output_dimension,
            )
            all_embeddings.extend(embeddings)
            total_tokens += chunk_tokens
    except Exception as error:
        _record_provider_failure("openai")
        raise EmbeddingRequestError(f"voyage: {voyage_error}; openai: {error}") from error

    storage_model = _openai_model_identity(fallback_model)
    _record_provider_used("openai")

    logger.info(
        "Embeddings generated",
        extra={
            "embedding_model": storage_model,
            "input_type": input_type,
            "texts": len(valid_texts),
            "chunks": len(openai_chunks),
            "providers": {"openai": 1},
            "output_dimension": output_dimension,
            "total_tokens": total_tokens,
        },
    )
    return EmbeddingBatchResult(
        embeddings=all_embeddings,
        provider="openai",
        model=storage_model,
        storage_model=storage_model,
    )


async def _embed_texts_with_openai(
    texts: list[str],
    *,
    input_type: str,
) -> EmbeddingBatchResult:
    valid_texts = [t for t in texts if t and t.strip()]
    settings = get_settings()
    fallback_model = getattr(
        settings,
        "embedding_openai_fallback_model",
        "text-embedding-3-small",
    )
    storage_model = _openai_model_identity(fallback_model)
    if not valid_texts:
        return EmbeddingBatchResult(
            embeddings=[],
            provider="openai",
            model=storage_model,
            storage_model=storage_model,
        )

    output_dimension = settings.embedding_dimensions
    openai_texts = [
        _truncate_text_to_token_limit(text, OPENAI_MAX_TOKENS_PER_INPUT) for text in valid_texts
    ]
    openai_chunks = _chunk_texts(openai_texts, max_tokens=OPENAI_MAX_TOKENS_PER_INPUT)
    all_embeddings: list[list[float]] = []
    total_tokens = 0
    try:
        for chunk in openai_chunks:
            embeddings, chunk_tokens = await _embed_with_openai_retry(
                chunk,
                model=fallback_model,
                output_dimension=output_dimension,
            )
            all_embeddings.extend(embeddings)
            total_tokens += chunk_tokens
    except Exception:
        _record_provider_failure("openai")
        raise

    _record_provider_used("openai")
    logger.info(
        "Embeddings generated",
        extra={
            "embedding_model": storage_model,
            "input_type": input_type,
            "texts": len(valid_texts),
            "chunks": len(openai_chunks),
            "providers": {"openai": 1},
            "output_dimension": output_dimension,
            "total_tokens": total_tokens,
        },
    )
    return EmbeddingBatchResult(
        embeddings=all_embeddings,
        provider="openai",
        model=storage_model,
        storage_model=storage_model,
    )


async def embed_documents_with_metadata(texts: list[str]) -> EmbeddingBatchResult:
    settings = get_settings()
    return await _embed_texts(
        texts,
        model=settings.embedding_document_model,
        input_type="document",
        max_tokens=DOCUMENT_MAX_TOKENS,
    )


async def embed_query_with_metadata(text: str) -> EmbeddingVectorResult:
    settings = get_settings()
    result = await _embed_texts(
        [text],
        model=settings.embedding_query_model,
        input_type="query",
        max_tokens=QUERY_MAX_TOKENS,
    )
    if not result.embeddings:
        raise EmbeddingRequestError("Cannot embed empty or whitespace-only query text")
    storage_model = (
        settings.embedding_document_model if result.provider == "voyage" else result.storage_model
    )
    return EmbeddingVectorResult(
        embedding=EmbeddingVector(
            result.embeddings[0],
            provider=result.provider,
            model=result.model,
            storage_model=storage_model,
        ),
        provider=result.provider,
        model=result.model,
        storage_model=storage_model,
    )


async def embed_query_for_configured_storage_models(text: str) -> list[EmbeddingVectorResult]:
    settings = get_settings()
    results = [await embed_query_with_metadata(text)]

    fallback_model = getattr(
        settings,
        "embedding_openai_fallback_model",
        "text-embedding-3-small",
    )
    openai_storage_model = _openai_model_identity(fallback_model)
    if (
        "openai" not in get_configured_embedding_providers()
        or results[0].storage_model == openai_storage_model
    ):
        return results

    try:
        openai_result = await _embed_texts_with_openai([text], input_type="query")
    except Exception:
        logger.warning("OpenAI fallback query embedding unavailable", exc_info=True)
        return results

    if not openai_result.embeddings:
        return results
    results.append(
        EmbeddingVectorResult(
            embedding=EmbeddingVector(
                openai_result.embeddings[0],
                provider="openai",
                model=openai_result.model,
                storage_model=openai_result.storage_model,
            ),
            provider="openai",
            model=openai_result.model,
            storage_model=openai_result.storage_model,
        )
    )
    return results


async def embed_documents(texts: list[str]) -> list[list[float]]:
    result = await embed_documents_with_metadata(texts)
    primary_storage_model = get_settings().embedding_document_model
    if result.storage_model != primary_storage_model:
        raise EmbeddingRequestError(
            "Legacy embed_documents cannot return fallback vectors without storage model metadata; "
            "use embed_documents_with_metadata for provider fallback support"
        )
    return result.embeddings


async def embed_query(text: str) -> list[float]:
    return (await embed_query_with_metadata(text)).embedding


async def embed_text(text: str, model: str = DEFAULT_MODEL) -> list[float]:
    del model
    return await embed_query(text)


async def embed_batch(texts: list[str], model: str = DEFAULT_MODEL) -> list[list[float]]:
    del model
    return await embed_documents(texts)
