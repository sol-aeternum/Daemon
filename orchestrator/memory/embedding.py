"""Embedding utility for text embeddings with provider fallback and retry logic."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Iterable

import httpx

from orchestrator.config import get_settings
from orchestrator.entitlements.errors import PolicyError
from orchestrator.entitlements.policy import load_inference_policy

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
_embedding_provider_used: dict[str, int] = {"voyage": 0, "openrouter": 0, "openai": 0}
_voyage_failure_timestamps: list[float] = []

#: Observation scope of the dispatch outcome records below. They live in this
#: backend process only: observed attempts, successes and denials seen by this
#: process. Background workers keep their own process, and a restart clears
#: this history; nothing here may be presented as a global or stored claim.
EMBEDDING_OBSERVATION_SCOPE = "backend_process"

#: Safe, fixed reason codes for the ``/status`` embeddings object. They expose
#: persisted configuration verdicts without raw exception text, secrets,
#: account identifiers or facts about users or their content. Unknown
#: conditions in future code must not invent strings outside this set.
EMBEDDING_REASON_CODES: frozenset[str] = frozenset(
    {
        "missing_credentials",
        "route_unapproved",
        "invalid_configuration",
        "adapter_unavailable",
        "budget_adapter_unavailable",
    }
)

#: Codes that block an embedding dispatch outright. ``budget_adapter_unavailable``
#: is deliberately not a blocker: current adapters reserve no account compute
#: budget, so a configuration can be eligible while this code honestly records
#: that no qualified, budgeted adapter exists and the capability stays
#: unverified until one does.
_EMBEDDING_CONFIGURATION_BLOCKERS: frozenset[str] = EMBEDDING_REASON_CODES - {
    "budget_adapter_unavailable"
}

#: Canonical publication order for reason codes so ``/status`` always exposes
#: the same deterministic list, whatever order the checks discovered them in.
_EMBEDDING_REASON_CODE_ORDER: tuple[str, ...] = (
    "missing_credentials",
    "route_unapproved",
    "invalid_configuration",
    "adapter_unavailable",
    "budget_adapter_unavailable",
)
assert set(_EMBEDDING_REASON_CODE_ORDER) == set(EMBEDDING_REASON_CODES)

_last_embed_success_at: datetime | None = None
_last_embed_failure_at: datetime | None = None
_last_embed_outcome: str = "never_attempted"
_embedding_configuration_denials_total = 0
_embedding_configuration_denial_reason_counts: dict[str, int] = {}


class EmbeddingError(Exception):
    pass


class EmbeddingConfigurationError(EmbeddingError):
    """Configuration denial before any provider dispatch.

    ``reason`` carries the safe reason code for ``/status`` (one of
    :data:`EMBEDDING_REASON_CODES`) or ``None`` when it cannot be attributed.
    """

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason


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
        raise EmbeddingConfigurationError(
            "VOYAGE_API_KEY environment variable not set",
            reason="missing_credentials",
        )
    return api_key


@lru_cache(maxsize=1)
def _get_openai_api_key() -> str:
    settings = get_settings()
    api_key = settings.openai_api_key
    if not api_key:
        raise EmbeddingConfigurationError(
            "OPENAI_API_KEY environment variable not set",
            reason="missing_credentials",
        )
    return api_key


@lru_cache(maxsize=1)
def _get_openrouter_api_key() -> str:
    settings = get_settings()
    api_key = settings.openrouter_api_key
    if not api_key:
        raise EmbeddingConfigurationError(
            "OPENROUTER_API_KEY environment variable not set",
            reason="missing_credentials",
        )
    return api_key


def get_configured_embedding_providers() -> tuple[str, ...]:
    settings = get_settings()
    fallback_raw = getattr(settings, "embedding_fallback_providers", "")
    fallbacks = [
        provider.strip().lower() for provider in str(fallback_raw).split(",") if provider.strip()
    ]
    providers: list[str] = ["voyage"]
    providers.extend(provider for provider in fallbacks if provider not in providers)
    return tuple(providers)


def get_configured_embedding_fallback_storage_models() -> tuple[str, ...]:
    settings = get_settings()
    models: list[str] = []
    providers = get_configured_embedding_providers()
    if "openrouter" in providers:
        document_model = getattr(
            settings,
            "embedding_openrouter_document_model",
            "voyageai/voyage-4-large",
        )
        models.append(_openrouter_model_identity(document_model))
    if "openai" in providers:
        openai_model = getattr(
            settings,
            "embedding_openai_fallback_model",
            "text-embedding-3-small",
        )
        models.append(_openai_model_identity(openai_model))
    return tuple(models)


def get_embedding_provider_used_counts() -> dict[str, int]:
    return dict(_embedding_provider_used)


def get_embedding_failures_total() -> int:
    return _embedding_failures_total


def get_embedding_retry_activations() -> int:
    return _retry_count


def get_embedding_last_retry_at() -> float | None:
    return _last_retry_at


def _embedding_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _record_dispatch_success() -> None:
    """Record the terminal outcome of one public dispatch: success."""
    global _last_embed_success_at, _last_embed_outcome
    _last_embed_success_at = _embedding_utc_now()
    _last_embed_outcome = "success"


def _record_provider_failure_outcome() -> None:
    """Record the terminal outcome of one public dispatch: provider error.

    Provider failures are already counted by the existing per-provider paths;
    this only sets the outcome stamp.
    """
    global _last_embed_failure_at, _last_embed_outcome
    _last_embed_failure_at = _embedding_utc_now()
    _last_embed_outcome = "provider_error"


def _record_configuration_denied_outcome(error: EmbeddingConfigurationError) -> None:
    """Record the terminal outcome of one public dispatch: configuration denial.

    Exactly one record per public dispatch attempt; the denial is counted
    separately from provider failures and attributed to the safe reason code
    carried by the error, never to its message text.
    """
    global _last_embed_failure_at, _last_embed_outcome
    global _embedding_configuration_denials_total
    _last_embed_failure_at = _embedding_utc_now()
    _last_embed_outcome = "configuration_denied"
    _embedding_configuration_denials_total += 1
    reason = getattr(error, "reason", None)
    if reason in EMBEDDING_REASON_CODES:
        _embedding_configuration_denial_reason_counts[reason] = (
            _embedding_configuration_denial_reason_counts.get(reason, 0) + 1
        )


def _record_dismissed_exception(error: BaseException) -> None:
    """Record a public dispatch that failed on a provider attempt."""
    if isinstance(error, EmbeddingConfigurationError):
        _record_configuration_denied_outcome(error)
    else:
        _record_provider_failure_outcome()


def _has_dispatchable_text(texts: list[str]) -> bool:
    return any(text and text.strip() for text in texts)


def _format_status_timestamp(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _describe_provider_credentials(provider: str, settings: Any) -> str | None:
    key_attributes = {
        "voyage": "voyage_api_key",
        "openrouter": "openrouter_api_key",
        "openai": "openai_api_key",
    }
    attribute = key_attributes.get(provider)
    if attribute is None or not getattr(settings, attribute, None):
        return "missing_credentials"
    return None


def _is_provider_route_approved(provider: str) -> bool | None:
    """Whether the loaded policy approves ``provider``'s embedding service.

    ``None`` means the policy itself could not be validated (a configuration
    problem, not an approval verdict).
    """
    service_id = _EMBEDDING_TOOL_SERVICES.get(provider)
    if service_id is None:
        return False
    try:
        return bool(load_inference_policy().is_tool_service_approved(service_id))
    except PolicyError:
        return None


def _canonical_reason_codes(present: Iterable[str]) -> tuple[str, ...]:
    """Deterministic, de-duplicated reason codes in canonical enum order."""
    found = set(present)
    return tuple(code for code in _EMBEDDING_REASON_CODE_ORDER if code in found)


def describe_embedding_configuration() -> tuple[str, tuple[str, ...]]:
    """Return ``(configuration, reason_codes)`` without any network activity.

    ``eligible`` means every static precondition for a dispatch attempt is
    present. It is not a provider test: no request has been made and the
    capability is not verified. ``budget_adapter_unavailable`` is always
    reported for the current adapters because they reserve no account compute
    budget, so an eligible configuration still is not operational-ready.
    """
    try:
        settings = get_settings()
    except Exception as error:
        # The configuration cannot even be evaluated; stay safe and unknown
        # rather than guess at details that could mislead. The exception
        # (and its message) may originate inside settings validation and can
        # contain credential material, so the log stays to the type name.
        logger.warning("Embedding configuration could not be evaluated: %s", type(error).__name__)
        return "unknown", ()

    present: set[str] = set()
    dimensions = getattr(settings, "embedding_dimensions", None)
    if not isinstance(dimensions, int) or dimensions <= 0:
        present.add("invalid_configuration")
    for model_attribute in ("embedding_document_model", "embedding_query_model"):
        model = getattr(settings, model_attribute, None)
        if not isinstance(model, str) or not model.strip():
            present.add("invalid_configuration")

    for provider in get_configured_embedding_providers():
        # No `continue` before the key checks: a configured provider with no
        # embedding adapter still reports its own credential and route state.
        if provider not in _EMBEDDING_TOOL_SERVICES:
            # The embedding adapter for this provider does not exist yet.
            # The configuration listing it is still validated below, but the
            # absent adapter itself is the reason, not an invalid value.
            present.add("adapter_unavailable")
        missing = _describe_provider_credentials(provider, settings)
        if missing is not None:
            present.add(missing)
        approved = _is_provider_route_approved(provider)
        if approved is None:
            present.add("invalid_configuration")
        elif not approved:
            present.add("route_unapproved")

    # Current direct adapters never reserve account price, so no deployment
    # with them may report an operational-ready embedding capability.
    present.add("budget_adapter_unavailable")

    unexpected = present - EMBEDDING_REASON_CODES
    if unexpected:
        logger.error("Unexpected embedding reason codes: %s", sorted(unexpected))
        return "unknown", ()
    if present & _EMBEDDING_CONFIGURATION_BLOCKERS:
        return "unavailable", _canonical_reason_codes(present)
    return "eligible", _canonical_reason_codes(present)


def get_embedding_status() -> dict[str, Any]:
    """JSON-safe additive ``embeddings`` object for ``GET /status``.

    Observation scope is this backend process only: attempt outcomes and
    timestamps live in process memory and disappear on restart. No provider
    request is made, nothing is keyed by account, and no exception text,
    credential or account fact is included.
    """
    try:
        configuration, reasons = describe_embedding_configuration()
    except Exception as error:
        # Status itself must never fail the request; unknown is the safe state.
        # Type name only: the underlying exception may carry raw settings
        # validation text that must not reach logs or the response.
        logger.warning("Embedding status fell back to unknown: %s", type(error).__name__)
        configuration, reasons = "unknown", ()

    return {
        "observation_scope": EMBEDDING_OBSERVATION_SCOPE,
        "configuration": configuration,
        "reason_codes": list(reasons),
        "last_outcome": _last_embed_outcome,
        "last_success_at": _format_status_timestamp(_last_embed_success_at),
        "last_failure_at": _format_status_timestamp(_last_embed_failure_at),
        "configuration_denials_total": _embedding_configuration_denials_total,
        "configuration_denial_reason_counts": dict(
            sorted(_embedding_configuration_denial_reason_counts.items())
        ),
    }


def reset_embedding_metrics_for_tests() -> None:
    global _embedding_failures_total, _last_retry_at, _retry_count
    global _last_embed_success_at, _last_embed_failure_at, _last_embed_outcome
    global _embedding_configuration_denials_total
    _retry_count = 0
    _last_retry_at = None
    _embedding_failures_total = 0
    _embedding_provider_used.clear()
    _embedding_provider_used.update({"voyage": 0, "openrouter": 0, "openai": 0})
    _voyage_failure_timestamps.clear()
    _last_embed_success_at = None
    _last_embed_failure_at = None
    _last_embed_outcome = "never_attempted"
    _embedding_configuration_denials_total = 0
    _embedding_configuration_denial_reason_counts.clear()
    _get_voyage_api_key.cache_clear()
    _get_openrouter_api_key.cache_clear()
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


def _openrouter_model_identity(model: str) -> str:
    return f"openrouter:{model}"


#: Inference-policy tool service that must be approved before private memory
#: text is sent to each embedding provider. A provider without an entry has no
#: qualified route and is denied, like any tool service absent from the policy.
_EMBEDDING_TOOL_SERVICES: dict[str, str] = {"voyage": "voyage-embeddings"}


def _require_approved_embedding_service(provider: str) -> None:
    """Deny unless the inference policy currently approves ``provider``'s service."""
    service_id = _EMBEDDING_TOOL_SERVICES.get(provider)
    try:
        approved = service_id is not None and load_inference_policy().is_tool_service_approved(
            service_id
        )
    except PolicyError:
        # The policy itself cannot be validated; the route is not the problem.
        raise EmbeddingConfigurationError(
            "Embedding inference policy invalid",
            reason="invalid_configuration",
        ) from None
    if not approved:
        # Lexical memory retrieval stays available without embeddings.
        raise EmbeddingConfigurationError(
            "Approved embedding route unavailable",
            reason="route_unapproved",
        )


async def _post_embeddings(
    *,
    api_key: str,
    texts: list[str],
    model: str,
    input_type: str,
    output_dimension: int,
) -> dict[str, Any]:
    _require_approved_embedding_service("voyage")
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
    _require_approved_embedding_service("openai")
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


async def _post_openrouter_embeddings(
    *,
    api_key: str,
    texts: list[str],
    model: str,
    input_type: str,
    output_dimension: int,
) -> dict[str, Any]:
    _require_approved_embedding_service("openrouter")
    settings = get_settings()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if settings.openrouter_referer:
        headers["HTTP-Referer"] = settings.openrouter_referer
    if settings.openrouter_title:
        headers["X-Title"] = settings.openrouter_title

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{settings.openrouter_base_url.rstrip('/')}/embeddings",
            headers=headers,
            json={
                "input": texts,
                "model": model,
                "input_type": input_type,
                "dimensions": output_dimension,
                "encoding_format": "float",
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
        except EmbeddingConfigurationError:
            raise
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
        except EmbeddingConfigurationError:
            raise
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


async def _embed_with_openrouter_retry(
    texts: list[str],
    *,
    model: str,
    input_type: str,
    output_dimension: int,
) -> tuple[list[list[float]], int]:
    global _retry_count, _last_retry_at

    if not texts:
        return [], 0

    api_key = _get_openrouter_api_key()
    last_error: Exception | None = None
    backoff = INITIAL_BACKOFF_S

    for attempt in range(MAX_RETRIES):
        try:
            payload = await _post_openrouter_embeddings(
                api_key=api_key,
                texts=texts,
                model=model,
                input_type=input_type,
                output_dimension=output_dimension,
            )
            return _parse_embedding_payload(
                payload,
                provider="OpenRouter",
                texts_count=len(texts),
                output_dimension=output_dimension,
            )
        except EmbeddingConfigurationError:
            raise
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
        f"Failed to embed with OpenRouter after {MAX_RETRIES} attempts: {last_error}"
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
        except EmbeddingConfigurationError:
            raise
        except Exception as error:
            _record_provider_failure("voyage")
            logger.warning("Voyage embedding provider failed; trying fallback", exc_info=True)
            voyage_error = error
    else:
        voyage_error = EmbeddingRequestError("voyage: circuit open")

    provider_errors = [f"voyage: {voyage_error}"]
    last_error: Exception = voyage_error
    for provider in get_configured_embedding_providers()[1:]:
        try:
            if provider == "openrouter":
                return await _embed_texts_with_openrouter(
                    valid_texts,
                    input_type=input_type,
                )
            if provider == "openai":
                return await _embed_texts_with_openai(
                    valid_texts,
                    input_type=input_type,
                )
        except EmbeddingConfigurationError:
            raise
        except Exception as error:
            last_error = error
            provider_errors.append(f"{provider}: {error}")
            logger.warning(
                "Embedding fallback provider failed",
                extra={"embedding_provider": provider},
                exc_info=True,
            )

    raise EmbeddingRequestError("; ".join(provider_errors)) from last_error


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
    except EmbeddingConfigurationError:
        raise
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


async def _embed_texts_with_openrouter(
    texts: list[str],
    *,
    input_type: str,
) -> EmbeddingBatchResult:
    valid_texts = [t for t in texts if t and t.strip()]
    settings = get_settings()
    if input_type == "document":
        model = getattr(
            settings,
            "embedding_openrouter_document_model",
            "voyageai/voyage-4-large",
        )
        max_tokens = DOCUMENT_MAX_TOKENS
    else:
        model = getattr(
            settings,
            "embedding_openrouter_query_model",
            "voyageai/voyage-4-lite",
        )
        max_tokens = QUERY_MAX_TOKENS
    model_identity = _openrouter_model_identity(model)
    if not valid_texts:
        return EmbeddingBatchResult(
            embeddings=[],
            provider="openrouter",
            model=model_identity,
            storage_model=model_identity,
        )

    output_dimension = settings.embedding_dimensions
    chunks = _chunk_texts(valid_texts, max_tokens=max_tokens)
    all_embeddings: list[list[float]] = []
    total_tokens = 0
    try:
        for chunk in chunks:
            embeddings, chunk_tokens = await _embed_with_openrouter_retry(
                chunk,
                model=model,
                input_type=input_type,
                output_dimension=output_dimension,
            )
            all_embeddings.extend(embeddings)
            total_tokens += chunk_tokens
    except EmbeddingConfigurationError:
        raise
    except Exception:
        _record_provider_failure("openrouter")
        raise

    _record_provider_used("openrouter")
    logger.info(
        "Embeddings generated",
        extra={
            "embedding_model": model_identity,
            "input_type": input_type,
            "texts": len(valid_texts),
            "chunks": len(chunks),
            "providers": {"openrouter": 1},
            "output_dimension": output_dimension,
            "total_tokens": total_tokens,
        },
    )
    return EmbeddingBatchResult(
        embeddings=all_embeddings,
        provider="openrouter",
        model=model_identity,
        storage_model=model_identity,
    )


async def embed_documents_with_metadata(texts: list[str]) -> EmbeddingBatchResult:
    """Public document dispatch: records exactly one terminal outcome.

    A batch with no dispatchable text contacts no provider and records no
    outcome; the attempt never happened. Settings are resolved before the
    attempt starts, exactly like the pre-observation dispatch, so a settings
    failure is not miscounted as a provider error.
    """
    settings = get_settings()
    try:
        result = await _embed_texts(
            texts,
            model=settings.embedding_document_model,
            input_type="document",
            max_tokens=DOCUMENT_MAX_TOKENS,
        )
    except Exception as error:
        # ``CancelledError`` is a BaseException, so cancellation escapes this
        # handler and is never recorded as a provider or configuration result.
        _record_dismissed_exception(error)
        raise
    if result.provider != "none":
        _record_dispatch_success()
    return result


async def embed_query_with_metadata(text: str) -> EmbeddingVectorResult:
    """Public query dispatch: records exactly one terminal outcome.

    A blank or whitespace-only query contacts no provider and raises without
    recording an outcome: no attempt was made, so none is observed.
    """
    settings = get_settings()
    if not _has_dispatchable_text([text]):
        raise EmbeddingRequestError("Cannot embed empty or whitespace-only query text")
    try:
        result = await _embed_texts(
            [text],
            model=settings.embedding_query_model,
            input_type="query",
            max_tokens=QUERY_MAX_TOKENS,
        )
    except Exception as error:
        # ``CancelledError`` is a BaseException, so cancellation escapes this
        # handler and is never recorded as a provider or configuration result.
        _record_dismissed_exception(error)
        raise

    if not result.embeddings:
        # Restored guard: a provider consumed by the dispatch must deliver
        # exactly one vector for the query. An empty response is a terminal
        # provider failure — recorded as such — never an index-time surprise.
        if result.provider != "none":
            _record_provider_failure_outcome()
        raise EmbeddingRequestError("Query embedding dispatch returned no embeddings for the query")

    if result.provider == "voyage":
        storage_model = settings.embedding_document_model
    elif result.provider == "openrouter":
        storage_model = _openrouter_model_identity(
            getattr(
                settings,
                "embedding_openrouter_document_model",
                "voyageai/voyage-4-large",
            )
        )
    else:
        storage_model = result.storage_model
    _record_dispatch_success()
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


async def embed_query_for_configured_storage_models(
    text: str,
    *,
    primary_result: EmbeddingVectorResult | None = None,
    fallback_storage_models: set[str] | None = None,
) -> list[EmbeddingVectorResult]:
    settings = get_settings()
    results = [primary_result or await embed_query_with_metadata(text)]

    fallback_specs = {
        "openrouter": (
            _openrouter_model_identity(
                getattr(
                    settings,
                    "embedding_openrouter_document_model",
                    "voyageai/voyage-4-large",
                )
            ),
            _embed_texts_with_openrouter,
        ),
        "openai": (
            _openai_model_identity(
                getattr(
                    settings,
                    "embedding_openai_fallback_model",
                    "text-embedding-3-small",
                )
            ),
            _embed_texts_with_openai,
        ),
    }

    for provider in get_configured_embedding_providers()[1:]:
        spec = fallback_specs.get(provider)
        if spec is None:
            continue
        storage_model, embedder = spec
        if results[0].storage_model == storage_model or (
            fallback_storage_models is not None and storage_model not in fallback_storage_models
        ):
            continue

        try:
            fallback_result = await embedder([text], input_type="query")
        except Exception:
            logger.warning(
                "Fallback query embedding unavailable",
                extra={"embedding_provider": provider},
                exc_info=True,
            )
            continue

        if not fallback_result.embeddings:
            continue
        results.append(
            EmbeddingVectorResult(
                embedding=EmbeddingVector(
                    fallback_result.embeddings[0],
                    provider=provider,
                    model=fallback_result.model,
                    storage_model=storage_model,
                ),
                provider=provider,
                model=fallback_result.model,
                storage_model=storage_model,
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
    result = await embed_query_with_metadata(text)
    primary_storage_model = get_settings().embedding_document_model
    if result.storage_model != primary_storage_model:
        raise EmbeddingRequestError(
            "Legacy embed_query cannot return fallback vectors without storage model metadata; "
            "use embed_query_with_metadata for provider fallback support"
        )
    return result.embedding


async def embed_text(text: str, model: str = DEFAULT_MODEL) -> list[float]:
    del model
    return await embed_query(text)


async def embed_batch(texts: list[str], model: str = DEFAULT_MODEL) -> list[list[float]]:
    del model
    return await embed_documents(texts)
