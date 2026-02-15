"""
LLM Provider model fetching with caching.
Primary: OpenRouter.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

# In-memory cache (simple dict with timestamp)
_models_cache: dict[str, Any] = {
    "data": None,
    "timestamp": 0,
    "ttl_seconds": 300,  # 5 minutes
}


async def fetch_openrouter_models(
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch models from OpenRouter API.

    Args:
        api_key: OpenRouter API key (optional for public listing)

    Returns:
        List of model objects in OpenAI format with openrouter/ prefix
    """
    url = "https://openrouter.ai/api/v1/models"

    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, timeout=10.0)
        response.raise_for_status()
        data = response.json()

    # Handle OpenRouter format - returns {"data": [...]}
    models_list = data.get("data", []) if isinstance(data, dict) else []

    models = []
    for item in models_list:
        model_id = item.get("id", "")
        if not model_id:
            continue

        # OpenRouter IDs already include provider prefix (e.g., "anthropic/claude-3-opus")
        # We keep them as-is for LiteLLM compatibility with openrouter/ prefix
        lite_llm_id = f"openrouter/{model_id}"

        model: dict[str, Any] = {
            "id": lite_llm_id,
            "object": "model",
            "created": item.get("created", int(time.time())),
            "owned_by": item.get("owned_by", "openrouter"),
        }

        # Include pricing and context length as metadata
        if "pricing" in item:
            model["pricing"] = item["pricing"]
        if "context_length" in item:
            model["context_length"] = item["context_length"]

        models.append(model)

    return models


async def fetch_provider_models(
    provider: str = "openrouter",
    api_key: str | None = None,
    base_url: str | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch models from any OpenAI-compatible provider.

    Args:
        provider: Provider name (e.g., "openrouter")
        api_key: API key for the provider
        base_url: Custom base URL for the provider

    Returns:
        List of model objects in OpenAI format
    """
    if provider == "openrouter":
        return await fetch_openrouter_models(api_key)

    # Generic OpenAI-compatible endpoint
    url = f"{base_url or 'https://api.openai.com/v1'}/models"

    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, timeout=10.0)
        response.raise_for_status()
        data = response.json()

    models_list = data.get("data", []) if isinstance(data, dict) else []

    models = []
    for item in models_list:
        model_id = item.get("id", "")
        if not model_id:
            continue

        # Add provider prefix for LiteLLM compatibility
        prefix = f"{provider}/"
        lite_llm_id = model_id if model_id.startswith(prefix) else f"{prefix}{model_id}"

        model: dict[str, Any] = {
            "id": lite_llm_id,
            "object": "model",
            "created": item.get("created", int(time.time())),
            "owned_by": item.get("owned_by", provider),
        }

        models.append(model)

    return models


def get_cached_models(
    provider: str = "openrouter",
    api_key: str | None = None,
    base_url: str | None = None,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """
    Get models from cache or fetch from API.

    Note: This is a synchronous wrapper around the async fetch.
    For use in sync contexts, it runs the async function via asyncio.run().

    Returns cached data if:
    - Cache exists
    - Not expired (within TTL)
    - Not force_refresh

    Otherwise fetches fresh data.
    """
    global _models_cache

    now = time.time()
    cache_valid = (
        _models_cache["data"] is not None
        and (now - _models_cache["timestamp"]) < _models_cache["ttl_seconds"]
        and not force_refresh
    )

    if cache_valid:
        return _models_cache["data"]

    # Fetch fresh data - need to run async function in sync context
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're in an async context, can't use run()
            # Return cached data if available, otherwise empty
            if _models_cache["data"] is not None:
                return _models_cache["data"]
            return []
    except RuntimeError:
        pass  # No event loop, safe to use asyncio.run()

    try:
        models = asyncio.run(fetch_provider_models(provider, api_key, base_url))

        # Update cache
        _models_cache["data"] = models
        _models_cache["timestamp"] = now

        return models
    except Exception:
        # Return stale cache if available, otherwise empty
        if _models_cache["data"] is not None:
            return _models_cache["data"]
        return []


async def get_models_async(
    provider: str = "openrouter",
    api_key: str | None = None,
    base_url: str | None = None,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """
    Async version of get_cached_models for use in async contexts.
    """
    global _models_cache

    now = time.time()
    cache_valid = (
        _models_cache["data"] is not None
        and (now - _models_cache["timestamp"]) < _models_cache["ttl_seconds"]
        and not force_refresh
    )

    if cache_valid:
        return _models_cache["data"]

    try:
        models = await fetch_provider_models(provider, api_key, base_url)

        # Update cache
        _models_cache["data"] = models
        _models_cache["timestamp"] = now

        return models
    except Exception:
        # Return stale cache if available
        if _models_cache["data"] is not None:
            return _models_cache["data"]
        return []


def clear_models_cache() -> None:
    """Clear the models cache."""
    global _models_cache
    _models_cache["data"] = None
    _models_cache["timestamp"] = 0


def get_fallback_model(
    provider: str = "openrouter",
    default_model: str = "kimi/kimi-k2.5",
) -> dict[str, Any]:
    """
    Return a single fallback model when provider API is unavailable.
    """
    return {
        "id": f"openrouter/{default_model}",
        "object": "model",
        "created": int(time.time()),
        "owned_by": provider,
    }
