"""Redis cache for fetch results."""

import asyncio
import json
import logging
import os
import urllib.parse

from arq.connections import ArqRedis

from orchestrator.services.fetch.models import FetchResult

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 3600


def normalize_url(url: str) -> str:
    """Normalize URL for consistent caching."""
    parsed = urllib.parse.urlparse(url.lower())
    normalized_path = parsed.path.rstrip("/") if parsed.path else ""
    normalized = parsed._replace(path=normalized_path, fragment="")
    return urllib.parse.urlunparse(normalized)


class FetchCache:
    """Redis cache for FetchResult objects."""

    def __init__(self, redis_url: str | None = None):
        """Initialize cache with Redis connection."""
        self.redis_url: str | None = redis_url or os.getenv("REDIS_URL")
        self.redis: ArqRedis | None = None
        self._connect_lock: asyncio.Lock = asyncio.Lock()

    async def _ensure_connection(self) -> bool:
        """Ensure Redis connection is established."""
        if self.redis is not None:
            return True

        if not self.redis_url:
            logger.debug("Redis URL not configured, cache disabled")
            return False

        async with self._connect_lock:
            if self.redis is not None:
                return True

            try:
                from arq.connections import (
                    RedisSettings,
                    create_pool as arq_create_pool,
                )

                self.redis = await arq_create_pool(
                    RedisSettings.from_dsn(self.redis_url)
                )
                logger.debug("Redis connection established for fetch cache")
                return True
            except Exception as e:
                logger.warning(
                    f"Failed to connect to Redis for fetch cache: {e}", exc_info=True
                )
                return False

    def _serialize_result(self, result: FetchResult) -> str:
        """Serialize FetchResult to JSON string."""
        data = {
            "url": result.url,
            "content": result.content,
            "title": result.title,
            "strategy_used": result.strategy_used,
            "fetch_time_ms": result.fetch_time_ms,
            "content_length": result.content_length,
        }
        return json.dumps(data)

    def _deserialize_result(self, data: str, url: str) -> FetchResult | None:
        """Deserialize JSON string to FetchResult."""
        try:
            parsed: dict[str, object] = json.loads(data)

            # Extract and validate required fields
            content = parsed.get("content")
            title = parsed.get("title")
            strategy_used = parsed.get("strategy_used")
            fetch_time_ms = parsed.get("fetch_time_ms")
            content_length = parsed.get("content_length")

            if (
                content is None
                or title is None
                or strategy_used is None
                or fetch_time_ms is None
                or content_length is None
            ):
                logger.warning(f"Missing required fields in cached result for {url}")
                return None

            # Convert fields with proper type handling
            content_str = content if isinstance(content, str) else str(content)
            title_str = title if isinstance(title, str) else str(title)
            strategy_str = (
                strategy_used if isinstance(strategy_used, str) else str(strategy_used)
            )

            # Handle numeric conversions safely
            if isinstance(fetch_time_ms, (int, float)):
                fetch_time_ms_float = float(fetch_time_ms)
            elif isinstance(fetch_time_ms, str):
                fetch_time_ms_float = float(fetch_time_ms)
            else:
                fetch_time_ms_float = float(str(fetch_time_ms))

            if isinstance(content_length, int):
                content_length_int = content_length
            elif isinstance(content_length, (int, float)):
                content_length_int = int(content_length)
            elif isinstance(content_length, str):
                content_length_int = int(content_length)
            else:
                content_length_int = int(str(content_length))

            return FetchResult(
                url=url,
                content=content_str,
                title=title_str,
                strategy_used=strategy_str,
                cached=True,
                fetch_time_ms=fetch_time_ms_float,
                content_length=content_length_int,
            )
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"Failed to deserialize cached result for {url}: {e}")
            return None

    async def get(self, url: str) -> FetchResult | None:
        """Retrieve FetchResult from cache by URL."""
        if not await self._ensure_connection():
            return None

        try:
            normalized_url = normalize_url(url)
            key = f"fetch:result:{normalized_url}"

            # Type narrowing - _ensure_connection guarantees redis is not None here
            assert self.redis is not None

            data: str | None = await self.redis.get(key)
            if data is None:
                logger.debug(f"Cache miss for {normalized_url}")
                return None

            result = self._deserialize_result(data, url)
            if result is not None:
                logger.debug(f"Cache hit for {normalized_url}")
            return result
        except Exception as e:
            logger.warning(f"Error retrieving from fetch cache: {e}", exc_info=True)
            return None

    async def set(self, url: str, result: FetchResult, ttl: int | None = None) -> bool:
        """Store FetchResult in cache."""
        if not await self._ensure_connection():
            return False

        if result.cached:
            return False

        try:
            normalized_url = normalize_url(url)
            key = f"fetch:result:{normalized_url}"
            data = self._serialize_result(result)

            cache_ttl = ttl or int(
                os.getenv("FETCH_CACHE_TTL_SECONDS", DEFAULT_TTL_SECONDS)
            )

            # Type narrowing - _ensure_connection guarantees redis is not None here
            assert self.redis is not None

            await self.redis.set(key, data, ex=cache_ttl)
            logger.debug(f"Cached result for {normalized_url} with TTL {cache_ttl}s")
            return True
        except Exception as e:
            logger.warning(f"Error storing in fetch cache: {e}", exc_info=True)
            return False
