"""Embedding utility for OpenAI text embeddings with retry logic."""

import asyncio
import os
from typing import Optional

from openai import AsyncOpenAI, OpenAIError


# Default model produces 1536-dimensional vectors
DEFAULT_MODEL = "text-embedding-3-small"
MAX_RETRIES = 3
INITIAL_BACKOFF_S = 1.0


class EmbeddingError(Exception):
    """Raised when embedding fails after retries."""

    pass


def _get_client() -> AsyncOpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EmbeddingError("OPENAI_API_KEY environment variable not set")
    return AsyncOpenAI(api_key=api_key)


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
