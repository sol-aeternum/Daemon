"""Embedding utility for OpenAI text embeddings with retry logic."""

import asyncio
import logging
import os
from typing import Optional

from openai import AsyncOpenAI, OpenAIError

import litellm

logger = logging.getLogger(__name__)


# Default model produces 1536-dimensional vectors
DEFAULT_MODEL = "text-embedding-3-small"
MAX_RETRIES = 3
INITIAL_BACKOFF_S = 1.0

# Fallback observability counters
_fallback_count = 0
_last_fallback_at: float | None = None


class EmbeddingError(Exception):
    """Raised when embedding fails after retries."""

    pass


def _get_client() -> AsyncOpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EmbeddingError("OPENAI_API_KEY environment variable not set")
    return AsyncOpenAI(api_key=api_key)


async def _embed_text_fallback(text: str) -> list[float]:
    """Fallback embedding via OpenRouter through LiteLLM."""
    global _fallback_count, _last_fallback_at

    response = await litellm.aembedding(
        model="openrouter/openai/text-embedding-3-small",
        input=text,
    )
    _fallback_count += 1
    _last_fallback_at = asyncio.get_event_loop().time()
    return response.data[0].embedding


async def _embed_batch_fallback(texts: list[str]) -> list[list[float]]:
    """Fallback batch embedding via OpenRouter through LiteLLM."""
    global _fallback_count, _last_fallback_at

    response = await litellm.aembedding(
        model="openrouter/openai/text-embedding-3-small",
        input=texts,
    )
    _fallback_count += 1
    _last_fallback_at = asyncio.get_event_loop().time()
    sorted_data = sorted(response.data, key=lambda x: x.index)
    return [item.embedding for item in sorted_data]


async def embed_text(
    text: str,
    model: str = DEFAULT_MODEL,
    client: Optional[AsyncOpenAI] = None,
) -> list[float]:
    """
    Generate embedding for a single text string.

    Args:
        text: Text to embed. Must not be empty/whitespace-only.
        model: OpenAI embedding model (default: text-embedding-3-small)
        client: Optional pre-configured OpenAI client

    Returns:
        1536-dimensional embedding vector (for text-embedding-3-small)

    Raises:
        EmbeddingError: If text is empty or embedding fails after retries
    """
    if not text or not text.strip():
        raise EmbeddingError("Cannot embed empty or whitespace-only text")

    if client is None:
        client = _get_client()

    last_error = None
    backoff = INITIAL_BACKOFF_S

    try:
        for attempt in range(MAX_RETRIES):
            try:
                response = await client.embeddings.create(
                    model=model,
                    input=text,
                )
                return response.data[0].embedding

            except OpenAIError as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                continue

        raise EmbeddingError(
            f"Failed to embed text after {MAX_RETRIES} attempts: {last_error}"
        )
    except Exception as e:
        logger.warning(f"Primary embedding failed, attempting fallback: {e}")
        try:
            result = await _embed_text_fallback(text)
            logger.info("Fallback embedding succeeded via OpenRouter")
            return result
        except Exception as fallback_error:
            logger.error(f"Fallback embedding also failed: {fallback_error}")
            raise EmbeddingError(
                f"Failed to embed text after primary ({e}) and fallback ({fallback_error})"
            ) from fallback_error


async def embed_batch(
    texts: list[str],
    model: str = DEFAULT_MODEL,
    client: Optional[AsyncOpenAI] = None,
) -> list[list[float]]:
    """
    Generate embeddings for multiple texts.

    Args:
        texts: List of texts to embed. Empty/whitespace texts will be skipped.
        model: OpenAI embedding model (default: text-embedding-3-small)
        client: Optional pre-configured OpenAI client

    Returns:
        List of 1536-dimensional embedding vectors (for text-embedding-3-small)

    Raises:
        EmbeddingError: If all texts are empty or embedding fails after retries
    """
    if not texts:
        return []

    valid_texts = [t for t in texts if t and t.strip()]
    if not valid_texts:
        raise EmbeddingError("Cannot embed batch: all texts are empty or whitespace")

    if client is None:
        client = _get_client()

    last_error = None
    backoff = INITIAL_BACKOFF_S

    try:
        for attempt in range(MAX_RETRIES):
            try:
                response = await client.embeddings.create(
                    model=model,
                    input=valid_texts,
                )
                # Sort by index to maintain order
                sorted_data = sorted(response.data, key=lambda x: x.index)
                return [item.embedding for item in sorted_data]

            except OpenAIError as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                continue

        raise EmbeddingError(
            f"Failed to embed batch after {MAX_RETRIES} attempts: {last_error}"
        )
    except Exception as e:
        logger.warning(f"Primary batch embedding failed, attempting fallback: {e}")
        try:
            result = await _embed_batch_fallback(valid_texts)
            logger.info("Fallback batch embedding succeeded via OpenRouter")
            return result
        except Exception as fallback_error:
            logger.error(f"Fallback batch embedding also failed: {fallback_error}")
            raise EmbeddingError(
                f"Failed to embed batch after primary ({e}) and fallback ({fallback_error})"
            ) from fallback_error
