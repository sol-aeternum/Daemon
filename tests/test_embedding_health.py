"""Health/status repair contract for the memory embedding observation model.

Bug #445: ``GET /status`` must describe the memory embedding capability
honestly and without any network dispatch: configuration blockers as safe
fixed reason codes, terminal dispatch outcomes observed in this backend
process only, configuration denials counted separately from provider
failures (exactly once per public dispatch), and no secrets or raw
exception text in the payload or logs.
"""

import asyncio
import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import orchestrator.memory.embedding as embedding_module
from orchestrator.auth import AuthenticatedDevice
from orchestrator.config import Settings
from orchestrator.db import AppState
from orchestrator.entitlements.errors import PolicyError
from orchestrator.memory.embedding import (
    EMBEDDING_OBSERVATION_SCOPE,
    EMBEDDING_REASON_CODES,
    EmbeddingBatchResult,
    EmbeddingConfigurationError,
    EmbeddingRequestError,
    describe_embedding_configuration,
    embed_documents_with_metadata,
    embed_query_with_metadata,
    get_embedding_status,
    reset_embedding_metrics_for_tests,
)
from orchestrator.routes.system import get_status


@pytest.fixture(autouse=True)
def reset_embedding_state():
    """The reset fixture helper must also clear the new health fields."""
    reset_embedding_metrics_for_tests()
    yield
    reset_embedding_metrics_for_tests()


def _settings(**overrides) -> SimpleNamespace:
    base = dict(
        embedding_document_model="voyage-4-large",
        embedding_query_model="voyage-4-lite",
        embedding_dimensions=1024,
        embedding_fallback_providers="",
        embedding_openrouter_document_model="voyageai/voyage-4-large",
        embedding_openai_fallback_model="text-embedding-3-small",
        voyage_api_key=None,
        openai_api_key=None,
        openrouter_api_key=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _Policy:
    def __init__(self, approved: set[str], *, error: Exception | None = None):
        self._approved = set(approved)
        self._error = error

    def is_tool_service_approved(self, service_id: str, *, now=None) -> bool:
        if self._error is not None:
            raise self._error
        return service_id in self._approved


def _set_policy(monkeypatch, policy: object) -> None:
    monkeypatch.setattr(embedding_module, "load_inference_policy", lambda: policy)


def _forbid_network(monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("embedding status must not dispatch a provider request")

    monkeypatch.setattr(embedding_module.httpx, "AsyncClient", forbidden)


def _approve(monkeypatch) -> None:
    _set_policy(monkeypatch, _Policy({"voyage-embeddings"}))


def _voyage_response(vector: list[float]) -> dict:
    return {
        "data": [{"index": 0, "embedding": vector}],
        "usage": {"total_tokens": 8},
    }


def test_status_reports_blockers_without_network(monkeypatch):
    """Key absent + unapproved route + no budget adapter, observed offline."""
    _forbid_network(monkeypatch)
    monkeypatch.setattr(embedding_module, "get_settings", lambda: _settings())
    _set_policy(monkeypatch, _Policy(approved=set()))

    configuration, reasons = describe_embedding_configuration()

    assert configuration == "unavailable"
    assert reasons == (
        "missing_credentials",
        "route_unapproved",
        "budget_adapter_unavailable",
    )
    assert set(reasons) <= EMBEDDING_REASON_CODES

    status = get_embedding_status()
    assert status["observation_scope"] == EMBEDDING_OBSERVATION_SCOPE == "backend_process"
    assert status["configuration"] == "unavailable"
    assert status["last_outcome"] == "never_attempted"
    assert status["last_success_at"] is None
    assert status["last_failure_at"] is None
    assert status["configuration_denials_total"] == 0
    assert status["configuration_denial_reason_counts"] == {}


def test_unmapped_fallback_adapter_reports_adapters_and_keeps_checks(monkeypatch):
    """A configured provider with no embedding adapter still reports every
    per-provider verdict; the absent mapping is ``adapter_unavailable``, not
    an invalid configuration, and the checks never short-circuit."""
    _forbid_network(monkeypatch)
    monkeypatch.setattr(
        embedding_module,
        "get_settings",
        lambda: _settings(embedding_fallback_providers="gemini"),
    )
    _set_policy(monkeypatch, _Policy(approved=set()))

    configuration, reasons = describe_embedding_configuration()

    assert configuration == "unavailable"
    # Deterministic, de-duplicated, canonical order.
    assert reasons == (
        "missing_credentials",
        "route_unapproved",
        "adapter_unavailable",
        "budget_adapter_unavailable",
    )
    assert reasons == tuple(dict.fromkeys(reasons))
    assert "invalid_configuration" not in reasons


def test_eligible_configuration_still_advertises_budget_adapter_gap(monkeypatch):
    """Key + approved route are not operational-ready: eligibility is not a
    provider test, and the budgeted adapter is missing."""
    _forbid_network(monkeypatch)
    monkeypatch.setattr(
        embedding_module,
        "get_settings",
        lambda: _settings(voyage_api_key="voyage-key"),
    )
    _approve(monkeypatch)

    configuration, reasons = describe_embedding_configuration()

    assert configuration == "eligible"
    assert reasons == ("budget_adapter_unavailable",)


def test_invalid_policy_is_invalid_configuration(monkeypatch):
    _forbid_network(monkeypatch)
    monkeypatch.setattr(embedding_module, "get_settings", lambda: _settings())
    _set_policy(monkeypatch, _Policy(approved=set(), error=PolicyError("bad policy")))

    configuration, reasons = describe_embedding_configuration()

    assert configuration == "unavailable"
    assert "invalid_configuration" in reasons
    assert "route_unapproved" not in reasons


def test_malformed_configuration_values_are_reported(monkeypatch):
    _forbid_network(monkeypatch)
    monkeypatch.setattr(
        embedding_module,
        "get_settings",
        lambda: _settings(embedding_dimensions=0),
    )
    configuration, reasons = describe_embedding_configuration()
    assert configuration == "unavailable"
    assert "invalid_configuration" in reasons


@pytest.mark.asyncio
async def test_configuration_denial_counted_exactly_once_and_separately(monkeypatch):
    """One public dispatch that is denied counts one denial, never a provider
    failure, and never one per internal path or retry."""
    forbid = AsyncMock(side_effect=AssertionError("must not reach the transport"))
    monkeypatch.setattr(embedding_module, "_post_embeddings", forbid)
    awaitable = AsyncMock(side_effect=AssertionError("no fallback dispatch"))
    monkeypatch.setattr(embedding_module, "_post_openai_embeddings", awaitable)
    monkeypatch.setattr(embedding_module, "_post_openrouter_embeddings", awaitable)
    monkeypatch.setattr(
        embedding_module,
        "get_settings",
        lambda: _settings(embedding_fallback_providers="openai"),
    )

    with pytest.raises(EmbeddingConfigurationError):
        await embed_documents_with_metadata(["stored fact"])

    status = get_embedding_status()
    assert status["last_outcome"] == "configuration_denied"
    assert status["configuration_denials_total"] == 1
    assert status["configuration_denial_reason_counts"] == {"missing_credentials": 1}
    assert status["last_failure_at"] is not None
    assert status["last_success_at"] is None
    # Configuration denials stay separate from provider failure counters.
    assert embedding_module.get_embedding_failures_total() == 0
    assert all(
        count == 0 for count in embedding_module.get_embedding_provider_used_counts().values()
    )

    # A second public dispatch is its own terminal attempt.
    with pytest.raises(EmbeddingConfigurationError):
        await embed_documents_with_metadata(["stored fact"])
    assert get_embedding_status()["configuration_denials_total"] == 2
    forbid.assert_not_awaited()
    awaitable.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_loop_records_only_the_terminal_outcome(monkeypatch):
    """Internal retries must not multiply outcome records or denial counts."""
    monkeypatch.setattr(embedding_module, "MAX_RETRIES", 3)
    monkeypatch.setattr(embedding_module, "INITIAL_BACKOFF_S", 0.0)
    monkeypatch.setattr(
        embedding_module,
        "get_settings",
        lambda: _settings(voyage_api_key="voyage-key"),
    )
    _approve(monkeypatch)
    transport = AsyncMock(side_effect=httpx.ConnectError("network down"))
    monkeypatch.setattr(embedding_module, "_post_embeddings", transport)

    with pytest.raises(EmbeddingRequestError):
        await embed_query_with_metadata("recall this")

    assert transport.await_count == 3
    assert embedding_module._retry_count == 2
    assert embedding_module.get_embedding_last_retry_at() is not None

    status = get_embedding_status()
    assert status["last_outcome"] == "provider_error"
    assert status["last_failure_at"] is not None
    assert status["last_success_at"] is None
    assert status["configuration_denials_total"] == 0
    assert status["configuration_denial_reason_counts"] == {}
    # Terminal attempt, not retries: one provider failure counter bump.
    assert embedding_module.get_embedding_failures_total() == 1
    assert embedding_module.get_embedding_provider_used_counts()["voyage"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openrouter"])
async def test_fallback_configuration_denial_is_not_provider_failure(monkeypatch, provider):
    monkeypatch.setattr(embedding_module, "MAX_RETRIES", 1)
    monkeypatch.setattr(
        embedding_module,
        "get_settings",
        lambda: _settings(voyage_api_key="voyage-key", embedding_fallback_providers=provider),
    )
    monkeypatch.setattr(
        embedding_module,
        "_post_embeddings",
        AsyncMock(side_effect=httpx.ConnectError("offline")),
    )
    with pytest.raises(EmbeddingConfigurationError):
        await embed_documents_with_metadata(["Fictional fact"])
    assert get_embedding_status()["configuration_denials_total"] == 1
    # Only the Voyage transport failed; the fallback stopped before transport.
    assert embedding_module.get_embedding_failures_total() == 1
    assert get_embedding_status()["last_outcome"] == "configuration_denied"


@pytest.mark.asyncio
async def test_success_and_provider_error_stamp_real_outcomes(monkeypatch):
    monkeypatch.setattr(embedding_module, "MAX_RETRIES", 1)
    monkeypatch.setattr(
        embedding_module,
        "get_settings",
        lambda: _settings(voyage_api_key="voyage-key"),
    )
    _approve(monkeypatch)

    with patch(
        "orchestrator.memory.embedding._post_embeddings",
        new_callable=AsyncMock,
        return_value=_voyage_response([0.1] * 1024),
    ):
        await embed_documents_with_metadata(["stored fact"])

    status = get_embedding_status()
    assert status["last_outcome"] == "success"
    assert status["last_success_at"] is not None
    assert status["last_failure_at"] is None
    assert embedding_module.get_embedding_provider_used_counts()["voyage"] == 1

    transport = AsyncMock(side_effect=httpx.ConnectError("network down"))
    monkeypatch.setattr(embedding_module, "_post_embeddings", transport)
    with pytest.raises(EmbeddingRequestError):
        await embed_query_with_metadata("recall this")

    status = get_embedding_status()
    assert status["last_outcome"] == "provider_error"
    assert status["last_success_at"] is not None
    assert status["last_failure_at"] is not None


@pytest.mark.asyncio
async def test_blank_query_records_no_outcome(monkeypatch):
    _forbid_network(monkeypatch)
    monkeypatch.setattr(embedding_module, "get_settings", lambda: _settings())

    with pytest.raises(EmbeddingRequestError, match="whitespace-only query text"):
        await embed_query_with_metadata("   ")

    status = get_embedding_status()
    assert status["last_outcome"] == "never_attempted"
    assert status["configuration_denials_total"] == 0
    assert status["last_failure_at"] is None
    assert status["last_success_at"] is None
    assert embedding_module.get_embedding_failures_total() == 0


@pytest.mark.asyncio
async def test_query_dispatch_without_embeddings_is_terminal_provider_error(monkeypatch):
    """The restored guard: an empty vector reply is a provider failure, not
    an ``IndexError``, and never a success."""
    monkeypatch.setattr(embedding_module, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        embedding_module,
        "_embed_texts",
        AsyncMock(
            return_value=EmbeddingBatchResult(
                embeddings=[],
                provider="voyage",
                model="voyage-4-lite",
                storage_model="voyage-4-lite",
            )
        ),
    )

    with pytest.raises(EmbeddingRequestError, match="no embeddings"):
        await embed_query_with_metadata("recall this")

    status = get_embedding_status()
    assert status["last_outcome"] == "provider_error"
    assert status["last_failure_at"] is not None
    assert status["last_success_at"] is None
    assert status["configuration_denials_total"] == 0


@pytest.mark.asyncio
async def test_cancellation_records_no_outcome(monkeypatch):
    """Directive cancellation (a BaseException) must not be counted as a
    provider error or a configuration denial."""
    monkeypatch.setattr(
        embedding_module,
        "get_settings",
        lambda: _settings(voyage_api_key="voyage-key"),
    )
    _approve(monkeypatch)
    monkeypatch.setattr(
        embedding_module, "_embed_texts", AsyncMock(side_effect=asyncio.CancelledError())
    )

    with pytest.raises(asyncio.CancelledError):
        await embed_documents_with_metadata(["stored fact"])

    status = get_embedding_status()
    assert status["last_outcome"] == "never_attempted"
    assert status["configuration_denials_total"] == 0
    assert status["last_failure_at"] is None
    assert status["last_success_at"] is None
    assert embedding_module.get_embedding_failures_total() == 0


def test_status_never_logs_or_exposes_settings_material(monkeypatch, caplog):
    leak = ValueError("voyage=sk-voyage-secret-xyz while validating settings")

    def broken_settings():
        raise leak

    monkeypatch.setattr(embedding_module, "get_settings", broken_settings)

    assert describe_embedding_configuration() == ("unknown", ())
    assert "sk-voyage-secret-xyz" not in caplog.text

    rendered = json.dumps(get_embedding_status())
    assert "sk-voyage-secret-xyz" not in rendered


@pytest.mark.asyncio
async def test_reset_fixture_helper_resets_new_fields(monkeypatch):
    monkeypatch.setattr(embedding_module, "get_settings", lambda: _settings())
    with pytest.raises(EmbeddingConfigurationError):
        await embed_documents_with_metadata(["stored fact"])
    assert get_embedding_status()["configuration_denials_total"] == 1

    reset_embedding_metrics_for_tests()

    status = get_embedding_status()
    assert status["last_outcome"] == "never_attempted"
    assert status["last_success_at"] is None
    assert status["last_failure_at"] is None
    assert status["configuration_denials_total"] == 0
    assert status["configuration_denial_reason_counts"] == {}
    assert status["observation_scope"] == EMBEDDING_OBSERVATION_SCOPE
    # Legacy counters still reset too.
    assert embedding_module.get_embedding_failures_total() == 0
    assert all(
        count == 0 for count in embedding_module.get_embedding_provider_used_counts().values()
    )
    assert embedding_module._retry_count == 0
    assert embedding_module.get_embedding_last_retry_at() is None


@pytest.mark.asyncio
async def test_route_status_keeps_legacy_keys_and_adds_embeddings_object(monkeypatch, caplog):
    _forbid_network(monkeypatch)
    monkeypatch.setattr(embedding_module, "get_settings", lambda: _settings())
    _set_policy(monkeypatch, _Policy(approved=set()))
    app_state = AppState(settings=Settings(daemon_environment="development"))

    first = await get_status(
        app_state=app_state,
        auth=cast(AuthenticatedDevice, object()),
    )
    second = await get_status(
        app_state=app_state,
        auth=cast(AuthenticatedDevice, object()),
    )

    for legacy_key in (
        "embedding_retry_activations",
        "embedding_last_retry_at",
        "embedding_failures_total",
        "embedding_provider_used",
    ):
        assert legacy_key in first
    embeddings = first["embeddings"]
    assert embeddings["observation_scope"] == "backend_process"
    assert embeddings["configuration"] == "unavailable"
    assert "budget_adapter_unavailable" in embeddings["reason_codes"]
    # A read-only GET produces no observation writes and no external effects.
    assert first["embeddings"] == second["embeddings"]
    assert "sk-voyage-secret" not in json.dumps(first, default=str)
