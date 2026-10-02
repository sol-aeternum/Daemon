"""Automatic background calls carry no legacy sampling controls.

The approved configuration makes one Luna-only automatic candidate serve the
background profile, and Luna declares **seed-only** sampling support. Every
background helper used to send ``temperature`` and/or ``top_p``, so the runtime
correctly refused the request before reserving spend and the workload had no
eligible route at all.

These tests pin the fix at both layers, because either alone would be a weaker
contract:

1. **Caller integration, through the real** ``guarded_completion``. Each helper
   is driven end to end inside a real account scope with a mocked provider
   transport and a mocked ledger, and the assertion is made against the kwargs
   that actually reached ``litellm.acompletion``. The production routing config
   is used unmodified, so a helper that regressed to sending ``temperature``
   would not merely fail an assertion: it would have no dispatchable route.
2. **The runtime is not weakened to hide the mismatch.** A seed-only candidate
   still refuses a request that carries a sampling control it does not declare.
   That is what makes the caller change meaningful rather than cosmetic.

The contradiction check is a separate reasoning-profile control; it retains its
automatic temperature. Explicit benchmark/manual model pins also keep their
historical sampling controls, with benchmark determinism asserted against the
real guard rather than a stub.
"""

from __future__ import annotations

import importlib
import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator import compute_runtime as runtime
from orchestrator import model_routing
from orchestrator.memory.dedup import (
    AUTOMATIC_CONTRADICTION_MAX_TOKENS,
    BENCHMARK_CONTRADICTION_MAX_TOKENS,
    BENCHMARK_CONTRADICTION_MODEL,
    CONTRADICTION_TEMPERATURE,
    DEDUP_BENCHMARK_SEED,
    check_contradiction,
)
from orchestrator.memory.entities import (
    CandidateMention,
    EntityResolution,
    confirm_merge_llm,
)
from orchestrator.memory.extraction import (
    BENCHMARK_EXTRACTION_MODEL,
    BENCHMARK_SEED,
    EXTRACTION_MAX_TOKENS,
    EXTRACTION_TEMPERATURE,
    EXTRACTION_TOP_P,
    extract_facts_from_text,
)
from orchestrator.memory.store import MemoryStore
from orchestrator.memory.summary import (
    SUMMARY_TEMPERATURE,
    generate_or_update_summary,
)
from orchestrator.memory.summarization import generate_summary
from orchestrator.memory.titles import (
    TITLE_TEMPERATURE,
    generate_conversation_title,
)
from test_model_routing import LUNA, PRODUCTION_ROUTING, dispatch_fixture, last_kwargs, named_route

#: The demanding candidate the reasoning profile dispatches for a contradiction
#: check. Its temperature support preserves the reasoning workload contract.
REASONING_MODEL = "openrouter/z-ai/glm-5.3"

#: Sampling controls an automatic call must never send. A seed-only candidate
#: declares none of them, so any one of them makes the request undispatchable.
LEGACY_SAMPLING = ("temperature", "top_p")

TITLE_REPLY = "Understanding The Daemon Project"
SUMMARY_REPLY = "The user asked about the daemon project. Open: none"
ENTITY_REPLY = "UNSURE: the nickname and the formal name may be one person."
CONTRADICTION_REPLY = "NO. The two facts describe the same preference."
EXTRACTION_REPLY = json.dumps(
    {"facts": [{"content": "User lives in Adelaide", "category": "fact", "confidence": 0.9}]}
)


class TransportResponse:
    """A litellm-shaped reply that every background helper can read.

    The helpers disagree about how to reach the content — attribute access on
    ``choices`` or ``model_dump()`` — so one reply supports both, exactly as the
    SDK object does.
    """

    def __init__(self, content: str) -> None:
        self._content = content
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]
        self.usage = None

    def model_dump(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": self._content}}]}


def _record_routing(monkeypatch: pytest.MonkeyPatch, module_path: str) -> list[Any]:
    """Capture the ``RoutingState`` a helper binds, so dispatch can be attributed.

    The helpers import ``routing_context`` into their own namespace, so this
    wraps that symbol rather than the routing module, and each call still runs
    the real context manager.
    """
    module = importlib.import_module(module_path)
    real = module.routing_context
    states: list[Any] = []

    @contextmanager
    def recording(profile: str, **kwargs: Any):
        with real(profile, **kwargs) as state:
            states.append(state)
            yield state

    monkeypatch.setattr(module, "routing_context", recording)
    return states


def _summary_store() -> Any:
    """A store whose summary-cursor API satisfies the inline summary path."""
    store = MagicMock(spec=MemoryStore)
    store.get_conversation = AsyncMock(return_value={"summary": None, "summary_updated_at": None})
    store.count_summary_messages = AsyncMock(return_value=1)
    store.count_contiguous_finalized_messages_at = AsyncMock(return_value=1)
    store.get_summary_message_batch = AsyncMock(
        return_value=[{"role": "user", "content": "Tell me about the daemon project"}]
    )
    store.update_conversation_summary = AsyncMock(return_value=True)
    return store


# ---------------------------------------------------------------------------
# 1. Automatic background calls: no legacy sampling, one exact dispatch
# ---------------------------------------------------------------------------


def _titles_call() -> Any:
    return generate_conversation_title(
        [{"role": "user", "content": "Tell me about the daemon project"}]
    )


def _extraction_call() -> Any:
    return extract_facts_from_text("I live in Adelaide")


def _summarization_call() -> Any:
    return generate_summary([{"role": "user", "content": "Tell me about the daemon project"}])


def _summary_call() -> Any:
    return generate_or_update_summary(uuid.uuid4(), _summary_store())


def _entities_call() -> Any:
    return confirm_merge_llm(
        EntityResolution(
            mention=CandidateMention(text="Mike", normalized_key="mike", context="my brother Mike"),
            canonical_name="Michael",
            similarity=0.72,
            merge_decision="ambiguous",
        )
    )


#: Each automatic background helper, the module whose routing state to capture,
#: the content its mocked transport should return, and the profile it must bind.
BACKGROUND_HELPERS: list[tuple[str, str, str, str]] = [
    ("titles", "orchestrator.memory.titles", TITLE_REPLY, "background"),
    ("extraction", "orchestrator.memory.extraction", EXTRACTION_REPLY, "background"),
    ("summarization", "orchestrator.memory.summarization", SUMMARY_REPLY, "background"),
    ("summary", "orchestrator.memory.summary", SUMMARY_REPLY, "background"),
    ("entities", "orchestrator.memory.entities", ENTITY_REPLY, "background"),
]


@pytest.mark.parametrize(
    ("helper", "module_path", "reply", "profile"),
    BACKGROUND_HELPERS,
    ids=[case[0] for case in BACKGROUND_HELPERS],
)
@pytest.mark.asyncio
async def test_automatic_background_call_sends_no_legacy_sampling(
    monkeypatch: pytest.MonkeyPatch,
    helper: str,
    module_path: str,
    reply: str,
    profile: str,
) -> None:
    """A background helper dispatches on Luna without any sampling control.

    ``PRODUCTION_ROUTING`` is used unmodified, so the seed-only background
    candidate is the only automatic route: had the helper still sent
    ``temperature``/``top_p``, the guard would have found no candidate and this
    call would have raised instead of reaching the transport.
    """
    states = _record_routing(monkeypatch, module_path)
    provider = AsyncMock(return_value=TransportResponse(reply))
    routes = [named_route(LUNA)]

    with dispatch_fixture(
        monkeypatch, routes, routing=None, provider=provider, auto_route=True
    ) as (service, _provider, scope):
        # The production config, not a permissive fixture.
        assert model_routing.load_model_routing() is PRODUCTION_ROUTING
        assert PRODUCTION_ROUTING.profile("background").ranked_models() == (LUNA,)
        assert (
            model_routing.supports_sampling_parameters(LUNA, {"temperature": 0.1, "top_p": 1.0})
            is False
        )

        await _call_helper(helper)

    sent = last_kwargs(provider)
    for control in LEGACY_SAMPLING:
        assert control not in sent, f"{helper} leaked {control} to the transport"
    # Luna serves background work at its default effort (medium since the B3
    # evaluation), and the seed it does declare is never invented by the helper.
    assert sent["model"] == LUNA
    assert sent["reasoning_effort"] == "medium"
    assert "seed" not in sent

    # Exactly one reservation and one settlement: no fallback, no double charge.
    assert service.reserve.await_count == 1
    assert service.settle.await_count == 1
    reserved = service.reserve.await_args
    assert reserved.args[0] == scope.user_id
    assert reserved.kwargs["model"] == LUNA
    assert reserved.kwargs["route_id"] == LUNA
    assert reserved.kwargs["provider"] == "openrouter"
    # Exactly one reservation reached settlement, for the amount that was
    # reserved: the scope's own ledger keys the settled reservation by identity.
    assert list(scope.settled.values()) == [reserved.args[1]]
    assert service.settle.await_args.args[0] in scope.settled

    # Attribution is the route that actually answered, read from live state.
    assert [state.profile for state in states] == [profile]
    assert states[0].selected_model == LUNA
    assert states[0].selected_route_id == LUNA
    assert states[0].explicit is False
    # The coarser scope attribution also carries the dispatched route.
    assert scope.selected_model == LUNA


async def _call_helper(helper: str) -> Any:
    return await {
        "titles": _titles_call,
        "extraction": _extraction_call,
        "summarization": _summarization_call,
        "summary": _summary_call,
        "entities": _entities_call,
    }[helper]()


@pytest.mark.asyncio
async def test_automatic_contradiction_check_keeps_reasoning_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The contradiction check uses a separate reasoning profile and temperature.

    The background Luna compatibility fix must not change this reasoning
    workload. The chosen route declares temperature support.
    """
    states = _record_routing(monkeypatch, "orchestrator.memory.dedup")
    provider = AsyncMock(return_value=TransportResponse(CONTRADICTION_REPLY))

    with dispatch_fixture(
        monkeypatch, [named_route(REASONING_MODEL)], routing=None, provider=provider
    ) as (service, _provider, _scope):
        detected, _explanation = await check_contradiction(
            "User lives in Adelaide", "User lives in Adelaide"
        )

    assert detected is False
    sent = last_kwargs(provider)
    assert sent["temperature"] == CONTRADICTION_TEMPERATURE
    assert "top_p" not in sent
    assert sent["model"] == REASONING_MODEL
    # The automatic verdict uses the approved helper output budget: a
    # reasoning-preset response must not be truncated into a false "NO".
    assert sent["max_tokens"] == AUTOMATIC_CONTRADICTION_MAX_TOKENS
    assert "seed" not in sent
    assert service.reserve.await_count == 1
    assert service.settle.await_count == 1
    assert states[0].profile == "reasoning"
    assert states[0].selected_model == REASONING_MODEL


@pytest.mark.asyncio
async def test_a_seed_only_candidate_still_refuses_legacy_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runtime was not weakened: a seed-only route refuses a temperature.

    If this ever stops raising, the caller change above would be masking a real
    capability gate rather than fixing a request shape, and a helper could
    silently regress without any test noticing. The request is refused on the
    sampling incompatibility alone, so the sharper pinned-model diagnostic below
    is the proof that temperature is the reason.
    """
    provider = AsyncMock(return_value=TransportResponse(TITLE_REPLY))

    with dispatch_fixture(monkeypatch, [named_route(LUNA)], routing=None, provider=provider) as (
        service,
        _provider,
        _scope,
    ):
        assert model_routing.supports_sampling_parameters(LUNA, {"temperature": 0.1}) is False

        with model_routing.routing_context("background"):
            with pytest.raises(runtime.ComputeUnavailable) as refused:
                await runtime.guarded_completion(
                    messages=[{"role": "user", "content": "Make a title"}],
                    max_tokens=24,
                    temperature=TITLE_TEMPERATURE,
                )
        assert refused.value.code == "capacity_unavailable"
        # Refused before spending, not after.
        provider.assert_not_awaited()
        service.reserve.assert_not_awaited()
        service.settle.assert_not_awaited()

        # Pinned to the seed-only model, the refusal names the actual cause. An
        # exact selection is never silently downgraded, so the scope has to
        # honour the pin for this half of the check.
        _scope.auto_route = False
        with model_routing.routing_context("background"):
            with pytest.raises(runtime.ComputeUnavailable) as pinned:
                await runtime.guarded_completion(
                    model=LUNA,
                    messages=[{"role": "user", "content": "Make a title"}],
                    max_tokens=24,
                    temperature=TITLE_TEMPERATURE,
                )
    assert "sampling" in str(pinned.value)
    assert provider.await_count == 0
    assert service.reserve.await_count == 0


# ---------------------------------------------------------------------------
# 2. Explicit model pins keep their historical sampling controls
# ---------------------------------------------------------------------------


def _title_with_model() -> Any:
    return generate_conversation_title(
        [{"role": "user", "content": "Tell me about the daemon project"}],
        model=REASONING_MODEL,
    )


def _extraction_with_model() -> Any:
    return extract_facts_from_text("I live in Adelaide", model=REASONING_MODEL)


def _summarization_with_model() -> Any:
    return generate_summary(
        [{"role": "user", "content": "Tell me about the daemon project"}],
        settings={"summary_model": REASONING_MODEL, "summary_temperature": 0.7},
    )


def _summary_with_model() -> Any:
    return generate_or_update_summary(uuid.uuid4(), _summary_store(), model=REASONING_MODEL)


def _entities_with_model() -> Any:
    return confirm_merge_llm(
        EntityResolution(
            mention=CandidateMention(text="Mike", normalized_key="mike", context="my brother Mike"),
            canonical_name="Michael",
            similarity=0.72,
            merge_decision="ambiguous",
        ),
        model=REASONING_MODEL,
    )


EXPLICIT_PINS: list[tuple[str, Any, str, float | None, float | None]] = [
    ("titles", _title_with_model, "temperature", TITLE_TEMPERATURE, None),
    ("extraction", _extraction_with_model, "temperature", EXTRACTION_TEMPERATURE, EXTRACTION_TOP_P),
    (
        "summarization",
        _summarization_with_model,
        "temperature",
        0.7,
        None,
    ),
    ("summary", _summary_with_model, "temperature", SUMMARY_TEMPERATURE, None),
    ("entities", _entities_with_model, "temperature", 0.1, None),
]


@pytest.mark.parametrize(
    ("helper", "call", "control", "expected", "expected_top_p"),
    EXPLICIT_PINS,
    ids=[case[0] for case in EXPLICIT_PINS],
)
@pytest.mark.asyncio
async def test_explicit_model_pin_preserves_its_sampling_controls(
    monkeypatch: pytest.MonkeyPatch,
    helper: str,
    call: Any,
    control: str,
    expected: float | None,
    expected_top_p: float | None,
) -> None:
    """A test/benchmark pin is a caller-owned decision, and it still ships its params.

    Removing the automatic sampling controls must not become a central strip:
    an explicit model that *does* declare these parameters still receives them,
    so manual compatibility is preserved rather than weakened.
    """
    provider = AsyncMock(return_value=TransportResponse(EXTRACTION_REPLY))

    with dispatch_fixture(
        monkeypatch,
        [named_route(REASONING_MODEL)],
        routing=None,
        provider=provider,
        auto_route=False,
    ) as (service, _provider, _scope):
        await call()

    sent = last_kwargs(provider)
    assert sent["model"] == REASONING_MODEL
    assert sent[control] == expected
    if expected_top_p is None:
        assert "top_p" not in sent
    else:
        assert sent["top_p"] == expected_top_p
    assert "seed" not in sent
    assert service.reserve.await_count == 1
    assert service.settle.await_count == 1


# ---------------------------------------------------------------------------
# 3. Benchmark pins keep their deterministic sampling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_benchmark_extraction_pins_its_deterministic_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Benchmark extraction still sends its snapshot pin, temperature 0.0 and seed."""
    provider = AsyncMock(return_value=TransportResponse(EXTRACTION_REPLY))

    with dispatch_fixture(
        monkeypatch,
        [named_route(BENCHMARK_EXTRACTION_MODEL)],
        routing=None,
        provider=provider,
        auto_route=False,
    ) as (service, _provider, scope):
        outcome = await extract_facts_from_text("I live in Adelaide", benchmark_mode=True)

    sent = last_kwargs(provider)
    assert sent["model"] == BENCHMARK_EXTRACTION_MODEL
    assert sent["temperature"] == 0.0
    assert sent["top_p"] == EXTRACTION_TOP_P
    assert sent["seed"] == BENCHMARK_SEED
    assert sent["max_tokens"] == EXTRACTION_MAX_TOKENS
    # The explicit pin bypasses the automatic shortlist, not profile
    # attribution: the call still binds the workload profile. No preset is
    # applied because the snapshot model is not placed in the profile.
    assert "reasoning_effort" not in sent
    assert service.reserve.await_count == 1
    assert service.settle.await_count == 1
    assert service.reserve.await_args.kwargs["route_id"] == BENCHMARK_EXTRACTION_MODEL
    # Provenance is the route the guard actually dispatched: the exact pin
    # inside the workload profile, not an automatic shortlist pick.
    assert outcome.model_used == BENCHMARK_EXTRACTION_MODEL
    assert scope.selected_model == BENCHMARK_EXTRACTION_MODEL


@pytest.mark.asyncio
async def test_benchmark_contradiction_pins_its_deterministic_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Benchmark contradiction still sends its snapshot pin, temperature 0.0 and seed."""
    from orchestrator.memory.dedup import reset_dedup_benchmark_tracking

    reset_dedup_benchmark_tracking()
    provider = AsyncMock(return_value=TransportResponse("NO"))

    with dispatch_fixture(
        monkeypatch,
        [named_route(BENCHMARK_CONTRADICTION_MODEL)],
        routing=None,
        provider=provider,
        auto_route=False,
    ) as (service, _provider, _scope):
        detected, _explanation = await check_contradiction(
            "User likes Python", "User loves Python", benchmark_mode=True
        )

    sent = last_kwargs(provider)
    assert sent["model"] == BENCHMARK_CONTRADICTION_MODEL
    assert sent["temperature"] == 0.0
    assert sent["seed"] == DEDUP_BENCHMARK_SEED
    assert sent["max_tokens"] == BENCHMARK_CONTRADICTION_MAX_TOKENS
    assert service.reserve.await_count == 1
    assert service.settle.await_count == 1
    assert service.reserve.await_args.kwargs["route_id"] == BENCHMARK_CONTRADICTION_MODEL
