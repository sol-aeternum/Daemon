"""Redis cache for fetch results."""

import asyncio
import json
import logging
import urllib.parse

from arq.connections import ArqRedis

from orchestrator.config import get_settings
from orchestrator.services.fetch.models import (
    EXTRACTION_VERSION_V1,
    FetchResult,
)

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 3600

# Cache namespace for the bounded/extraction-aware pipeline (#358).
# Keys never mix representations: mode and extraction version are part of
# the key, so a legacy lossy entry can never be served and a mode change
# cannot silently alias to another mode's content. Legacy
# ``fetch:result:*`` data is intentionally left in place (it decays via
# TTL); this namespace simply never reads or deletes it.
CACHE_NAMESPACE_V2 = "fetch:v2"
_DEFAULT_MODE = "article"


def normalize_url(url: str) -> str:
    """Return the cache-identity form of ``url`` (#358).

    Normalizes scheme and host only — host to its canonical lowercase
    IDNA ASCII form, scheme lowercased — and drops the fragment (never
    sent to the server). Everything that participates in resource
    identity is preserved verbatim: path case, query case and parameter
    order, and a meaningful trailing slash. Explicit default ports are
    collapsed because they name the same origin over the same scheme.
    """
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    try:
        host = encode_idna_hostname(host)
    except (UnicodeError, ValueError):
        # Identity normalization must not fail closed for cache lookups:
        # URL safety is enforced by the service's policy gate before any
        # cache access. The lowercased raw host keeps distinct-looking
        # malformed hosts distinct rather than normalizing them to "".
        host = host.lower()
    netloc = f"[{host}]" if ":" in host else host
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
        netloc = f"{netloc}:{port}"
    normalized = urllib.parse.urlunsplit((scheme, netloc, parts.path, parts.query, ""))
    if not parts.query and "?" in url.split("#", 1)[0]:
        normalized += "?"
    return normalized


def result_cache_key(
    url: str,
    extract: str = _DEFAULT_MODE,
    extraction_version: str = EXTRACTION_VERSION_V1,
) -> str:
    """Versioned cache key: namespace + extraction mode + version + identity."""
    mode = (extract or _DEFAULT_MODE).strip().lower() or _DEFAULT_MODE
    return f"{CACHE_NAMESPACE_V2}:{mode}:{extraction_version}:{normalize_url(url)}"


def encode_idna_hostname(host: str) -> str:
    """ASCII IDNA form of ``host`` (lowercase, trailing dot stripped).

    Raises ``UnicodeError`` when the host is not a valid IDNA name; the
    caller decides whether that is a rejection (URL policy gate) or a
    fallback (cache identity).
    """
    return host.rstrip(".").encode("idna").decode("ascii").lower()


class FetchCache:
    """Redis cache for FetchResult objects."""

    def __init__(self, redis_url: str | None = None):
        """Initialize cache with Redis connection."""
        self.redis_url: str | None = redis_url or get_settings().redis_url
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

                self.redis = await arq_create_pool(RedisSettings.from_dsn(self.redis_url))
                logger.debug("Redis connection established for fetch cache")
                return True
            except Exception as e:
                logger.warning(f"Failed to connect to Redis for fetch cache: {e}", exc_info=True)
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
            "source_url": result.source_url,
            "final_url": result.final_url,
            "content_type": result.content_type,
            "extraction_version": result.extraction_version,
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
            strategy_str = strategy_used if isinstance(strategy_used, str) else str(strategy_used)
            source_url = parsed.get("source_url")
            final_url = parsed.get("final_url")
            content_type = parsed.get("content_type")
            extraction_version = parsed.get("extraction_version")

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
                source_url=source_url if isinstance(source_url, str) else None,
                final_url=final_url if isinstance(final_url, str) else None,
                content_type=content_type if isinstance(content_type, str) else None,
                extraction_version=(
                    extraction_version
                    if isinstance(extraction_version, str)
                    else EXTRACTION_VERSION_V1
                ),
            )
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(f"Failed to deserialize cached result for {url}: {e}")
            return None

    async def get(self, url: str, extract: str = _DEFAULT_MODE) -> FetchResult | None:
        """Retrieve FetchResult from cache by URL and extraction mode."""
        if not await self._ensure_connection():
            return None

        try:
            key = result_cache_key(url, extract)

            # Type narrowing - _ensure_connection guarantees redis is not None here
            assert self.redis is not None

            data: str | None = await self.redis.get(key)
            if data is None:
                logger.debug(f"Cache miss for {key}")
                return None

            result = self._deserialize_result(data, url)
            if result is not None:
                logger.debug(f"Cache hit for {key}")
            return result
        except Exception as e:
            logger.warning(f"Error retrieving from fetch cache: {e}", exc_info=True)
            return None

    async def set(
        self,
        url: str,
        result: FetchResult,
        ttl: int | None = None,
        extract: str = _DEFAULT_MODE,
    ) -> bool:
        """Store FetchResult in cache."""
        if not await self._ensure_connection():
            return False

        if result.cached:
            return False

        try:
            key = result_cache_key(url, extract)
            data = self._serialize_result(result)

            settings = get_settings()
            configured_ttl = (
                settings.fetch_cache_ttl_seconds
                if "fetch_cache_ttl_seconds" in settings.model_fields_set
                else DEFAULT_TTL_SECONDS
            )
            cache_ttl = ttl or configured_ttl

            # Type narrowing - _ensure_connection guarantees redis is not None here
            assert self.redis is not None

            await self.redis.set(key, data, ex=cache_ttl)
            logger.debug(f"Cached result for {key} with TTL {cache_ttl}s")
            return True
        except Exception as e:
            logger.warning(f"Error storing in fetch cache: {e}", exc_info=True)
            return False
