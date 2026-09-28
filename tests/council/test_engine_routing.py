"""Council seats: truthful model attribution and enforced independence.

Every seat runs inside its own routing context, so a capability-first fallback
can substitute a model without the council record ever naming a model that did
not speak, and concurrent seats cannot observe each other's selection. These
tests play the runtime's part by recording a selection on the active routing
state — the same write ``guarded_completion`` performs.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Callable, Iterator

import pytest

from orchestrator import model_routing
from orchestrator.council import engine
from orchestrator.council import tools as council_tools
from orchestrator.council.models import (
    CouncilDiversityError,
    PerspectiveResponse,
    PerspectiveType,
)

ROSTER = {
    "analyst": "openrouter/anthropic/claude-sonnet-5",
    "strategist": "openrouter/openai/gpt-6-sol",
    "skeptic": "openrouter/google/gemini-3.8-flash",
    "contrarian": "openrouter/x-ai/grok-4.7",
    "auditor": "openrouter/z-ai/glm-5.3",
}
DEBATE_ROLES = ("analyst", "strategist", "skeptic", "contrarian")


class SeatProbe:
    """Records each seat's routing context and acts as the runtime for it.

    The real :func:`orchestrator.model_routing.current_routing` resolves the
    seat's own state, so a selection recorded here lands on the seat that made
    the call — which is exactly the property the council depends on.
    """

    def __init__(self) -> None:
        self.contexts: list[dict[str, Any]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch, module: Any = engine) -> "SeatProbe":
        real = module.routing_context

        @contextmanager
        def recording(profile: str, **kwargs: Any) -> Iterator[Any]:
            with real(profile, **kwargs) as state:
                self.contexts.append({"profile": profile, "state": state, **kwargs})
                yield state

        monkeypatch.setattr(module, "routing_context", recording)
        return self

    def select(self, model: str, *, route_id: str | None = None) -> None:
        model_routing.current_routing().record_selection(
            model=model,
            route_id=route_id or model,
            group=None,
            explicit=False,
        )

    def for_preference(self, preferred_model: str) -> dict[str, Any]:
        matches = [
            context for context in self.contexts if context["preferred_model"] == preferred_model
        ]
        assert matches, f"no routing context for {preferred_model}"
        return matches[0]


def completion_response(text: str = "**Position**: proceed\n\n**Confidence**: 7/10") -> Any:
    return SimpleNamespace(
        # An empty echo proves the report comes from routing state, not the
        # provider's own model field.
        model="",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
    )


def make_completion(
    probe: SeatProbe | None,
    selection: Callable[[str], str | None],
    *,
    echo: str = "",
) -> Callable[..., Any]:
    async def _completion(**params: Any) -> Any:
        chosen = selection(params["model"])
        if probe is not None and chosen is not None:
            probe.select(chosen)
        # Yield control so concurrent seats interleave inside their contexts.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        response = completion_response()
        response.model = echo
        return response

    return _completion


def patch_completion(
    monkeypatch: pytest.MonkeyPatch,
    selection: Callable[[str], str | None],
    *,
    completion_module: Any = engine,
    echo: str = "",
) -> tuple[SeatProbe, list[dict[str, Any]]]:
    # The engine owns the routing context; the tool loop's completion lives in the
    # tools module, so the two can be faked independently.
    probe = SeatProbe().install(monkeypatch, engine)
    calls: list[dict[str, Any]] = []

    async def _completion(**params: Any) -> Any:
        calls.append(params)
        return await make_completion(probe, selection, echo=echo)(**params)

    monkeypatch.setattr(completion_module, "guarded_completion", _completion)
    return probe, calls


def same_model(_requested: str) -> str:
    return _requested


def collapse_onto_deepseek(requested: str) -> str:
    """Three seats fall back onto one developer that no seat was planned for."""
    if requested.endswith(("claude-sonnet-5", "gpt-6-sol", "grok-4.7")):
        return "openrouter/deepseek/deepseek-v4.1-flash"
    return requested


class TestModelAttribution:
    @pytest.mark.asyncio
    async def test_reports_the_model_that_actually_served_the_seat(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        fallback = "openrouter/anthropic/claude-opus-4.6"
        _, calls = patch_completion(
            monkeypatch, lambda requested: fallback if requested.endswith("5") else requested
        )
        results = await engine.fan_out("prompt", dict(ROSTER))
        assert calls, "the seat must actually call the runtime"
        assert {role: model for role, _c, _e, _r, _u, model in results} == {
            role: (fallback if role == "analyst" else model)
            for role, model in ROSTER.items()
            if role in DEBATE_ROLES
        }

    @pytest.mark.asyncio
    async def test_selection_beats_a_provider_echo(self, monkeypatch: pytest.MonkeyPatch):
        served = "openrouter/z-ai/glm-5.3"
        patch_completion(
            monkeypatch,
            lambda requested: served,
            echo="openrouter/some-provider/an-alias-the-caller-never-asked-for",
        )
        results = await engine.fan_out("prompt", dict(ROSTER))
        assert {model for *_rest, model in results} == {served}

    @pytest.mark.asyncio
    async def test_without_a_runtime_selection_the_requested_model_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # A mocked seat that never reaches the runtime has no selection, so the
        # request is the only honest thing to report.
        patch_completion(monkeypatch, lambda requested: None)
        results = await engine.fan_out("prompt", dict(ROSTER))
        assert {model for *_rest, model in results} == {ROSTER[role] for role in DEBATE_ROLES}

    @pytest.mark.asyncio
    async def test_concurrent_seats_each_report_their_own_model(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # Distinct deliveries, resolved per seat: a shared mutable account scope
        # would hand several seats the same model here.
        served = {
            "openrouter/anthropic/claude-sonnet-5": "openrouter/anthropic/claude-opus-4.6",
            "openrouter/openai/gpt-6-sol": "openrouter/openai/gpt-6-luna",
            "openrouter/google/gemini-3.8-flash": "openrouter/deepseek/deepseek-v4.1-flash",
            "openrouter/x-ai/grok-4.7": "openrouter/x-ai/grok-4.7",
        }
        patch_completion(monkeypatch, lambda requested: served.get(requested, requested))
        results = await engine.fan_out("prompt", dict(ROSTER))
        assert {role: model for role, _c, _e, _r, _u, model in results} == {
            "analyst": "openrouter/anthropic/claude-opus-4.6",
            "strategist": "openrouter/openai/gpt-6-luna",
            "skeptic": "openrouter/deepseek/deepseek-v4.1-flash",
            "contrarian": "openrouter/x-ai/grok-4.7",
        }
        developers = {model_routing.developer_for_model(model) for model in served.values()}
        assert len(developers) == 4


class TestFallbackDiversityPlanning:
    @pytest.mark.asyncio
    async def test_preferred_model_is_offered_and_other_seats_are_excluded(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        probe, _calls = patch_completion(monkeypatch, same_model)
        await engine.fan_out("prompt", dict(ROSTER))

        assert len(probe.contexts) == len(DEBATE_ROLES)
        for context in probe.contexts:
            preferred = context["preferred_model"]
            assert context["profile"] == "council"
            # The seat's own preference stays eligible; the candidates that belong
            # to another seat are removed outright rather than merely deprioritised.
            assert preferred not in context["excluded_models"]
            assert (
                model_routing.developer_for_model(preferred) not in (context["excluded_developers"])
            )
            assert context["excluded_developers"] == frozenset(
                model_routing.developer_for_model(model)
                for model in ROSTER.values()
                if model != preferred
            )
            assert context["excluded_models"] == frozenset(
                model for model in ROSTER.values() if model != preferred
            )

    @pytest.mark.asyncio
    async def test_a_seat_sharing_a_developer_keeps_its_own_developer_available(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        roster = {
            "analyst": "openrouter/anthropic/claude-sonnet-5",
            "strategist": "openrouter/anthropic/claude-opus-4.6",
            "skeptic": "openrouter/openai/gpt-6-sol",
            "contrarian": "openrouter/google/gemini-3.8-flash",
        }
        probe, _calls = patch_completion(monkeypatch, same_model)
        await engine.fan_out("prompt", roster)
        for context in probe.contexts:
            own = model_routing.developer_for_model(context["preferred_model"])
            # Sharing a developer must not cost a seat its own developer: both
            # Anthropic seats keep Anthropic eligible, and neither may take a
            # developer another seat holds.
            assert own not in context["excluded_developers"]
            reserved = (
                frozenset({"openai", "google"})
                if own == "anthropic"
                else frozenset({"anthropic", "openai", "google"}) - {own}
            )
            assert context["excluded_developers"] == reserved


class TestRoundDiversityGate:
    @pytest.mark.asyncio
    async def test_round_1_succeeds_with_three_or_more_developers(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # Two seats fall back, but onto developers no seat was planned for, so
        # the round is still independent.
        served = {
            "openrouter/x-ai/grok-4.7": "openrouter/deepseek/deepseek-v4.1-flash",
        }
        patch_completion(
            monkeypatch,
            lambda requested: served.get(requested, requested),
            completion_module=council_tools,
        )
        responses = await engine.run_round_1("prompt", dict(ROSTER))
        assert [response.perspective.value for response in responses] == list(DEBATE_ROLES)
        assert all(response.model_id is not None for response in responses)
        developers = {
            model_routing.developer_for_model(response.model_id)
            for response in responses
            if response.model_id is not None
        }
        assert len(developers) == 4
        assert all(not response.content.startswith("Error:") for response in responses)

    @pytest.mark.asyncio
    async def test_round_1_refuses_two_developers_after_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # Both substitutions land on the same unused developer: the failure mode
        # exclusions alone cannot prevent.
        patch_completion(monkeypatch, collapse_onto_deepseek, completion_module=council_tools)
        with pytest.raises(CouncilDiversityError) as excinfo:
            await engine.run_round_1("prompt", dict(ROSTER))
        message = str(excinfo.value)
        assert "round 1" in message
        assert "deepseek" in message
        assert "contrarian=" in message and "strategist=" in message

    @pytest.mark.asyncio
    async def test_round_1_refuses_a_roster_that_plans_too_few_developers(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _probe, calls = patch_completion(monkeypatch, same_model)
        roster = {
            "analyst": "openrouter/anthropic/claude-sonnet-5",
            "strategist": "openrouter/anthropic/claude-opus-4.6",
            "skeptic": "openrouter/openai/gpt-6-sol",
        }
        with pytest.raises(CouncilDiversityError, match="planned"):
            await engine.run_round_1("prompt", roster)
        assert calls == [], "an unplannable council must not spend anything"

    @pytest.mark.asyncio
    async def test_a_seat_whose_model_cannot_be_read_is_not_a_developer(self):
        results: list[engine.SeatResult] = [
            ("analyst", "text", None, None, {}, "openrouter/anthropic/claude-sonnet-5"),
            ("strategist", "text", None, None, {}, "openrouter/openai/gpt-6-sol"),
            ("skeptic", "text", None, None, {}, ""),
        ]
        with pytest.raises(CouncilDiversityError, match="2 model developers"):
            engine._require_served_diversity(results, stage="round 1")

    @pytest.mark.asyncio
    async def test_round_2_applies_the_same_floor_and_planning(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        probe, _calls = patch_completion(monkeypatch, collapse_onto_deepseek)
        round_1 = [
            PerspectiveResponse(
                perspective=PerspectiveType(role),
                content=f"{role} says something",
                confidence=6.0,
                model_id=ROSTER[role],
            )
            for role in DEBATE_ROLES
        ]
        with pytest.raises(CouncilDiversityError, match="round 2"):
            await engine.run_round_2(
                "prompt",
                dict(ROSTER),
                round_1,
                {role: f"Agent-{index}" for index, role in enumerate(DEBATE_ROLES)},
            )
        assert len(probe.contexts) == len(DEBATE_ROLES)
        assert all(context["profile"] == "council" for context in probe.contexts)


class TestToolSeatPath:
    @pytest.mark.asyncio
    async def test_tool_seat_reports_the_routed_model(self, monkeypatch: pytest.MonkeyPatch):
        served = "openrouter/deepseek/deepseek-v4.1-flash"
        probe, calls = patch_completion(
            monkeypatch, lambda requested: served, completion_module=council_tools
        )
        results = await engine.fan_out(
            "prompt",
            dict(ROSTER),
            tools=[{"type": "function", "function": {"name": "noop", "parameters": {}}}],
            tool_executor=SimpleNamespace(execute=None),  # type: ignore[arg-type]
        )
        assert calls
        assert {model for *_rest, model in results} == {served}
        assert len(probe.contexts) == len(DEBATE_ROLES)

    @pytest.mark.asyncio
    async def test_tool_seat_error_is_not_counted_as_a_served_seat(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        async def _failing(**_params: Any) -> Any:
            raise RuntimeError("provider refused the tools")

        patch_completion(monkeypatch, lambda requested: None, completion_module=council_tools)
        monkeypatch.setattr(council_tools, "guarded_completion", _failing)

        results = await engine.fan_out(
            "prompt",
            dict(ROSTER),
            tools=[{"type": "function", "function": {"name": "noop", "parameters": {}}}],
            tool_executor=SimpleNamespace(execute=None),  # type: ignore[arg-type]
        )
        assert len(results) == len(DEBATE_ROLES)
        assert all(error is not None for _r, _c, error, *_rest in results)
        with pytest.raises(CouncilDiversityError, match="round 1"):
            engine._require_served_diversity(results, stage="round 1")


class TestNoModelShapedParameters:
    @pytest.mark.asyncio
    async def test_council_sends_no_reasoning_or_sampling_parameters(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _probe, calls = patch_completion(monkeypatch, same_model)
        await engine.fan_out("prompt", dict(ROSTER))
        assert calls
        for params in calls:
            assert set(params) == {"model", "messages", "timeout"}

    @pytest.mark.asyncio
    async def test_a_rejected_request_is_not_retried_with_stripped_parameters(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        attempts: list[dict[str, Any]] = []

        async def _rejecting(**params: Any) -> Any:
            attempts.append(params)
            raise RuntimeError("unsupported parameter: reasoning_effort")

        probe = SeatProbe().install(monkeypatch, engine)
        monkeypatch.setattr(engine, "guarded_completion", _rejecting)
        results = await engine.fan_out("prompt", dict(ROSTER))
        assert len(attempts) == len(DEBATE_ROLES), "one attempt per seat, no param-stripping retry"
        assert all("reasoning_effort" not in params for params in attempts)
        assert all("include_reasoning" not in params for params in attempts)
        assert all(error for _r, _c, error, *_rest in results)
        assert len(probe.contexts) == len(DEBATE_ROLES)

    @pytest.mark.asyncio
    async def test_tool_seats_send_no_model_shaped_parameters(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _probe, calls = patch_completion(monkeypatch, same_model, completion_module=council_tools)
        await engine.fan_out(
            "prompt",
            dict(ROSTER),
            tools=[{"type": "function", "function": {"name": "noop", "parameters": {}}}],
            tool_executor=SimpleNamespace(execute=None),  # type: ignore[arg-type]
        )
        for params in calls:
            assert set(params) == {"model", "messages", "tools", "timeout"}


class TestAuditSeat:
    """The auditor reviews the debate, so it may not be served by a debater.

    ``run_audit_round`` is exercised through the seat it builds rather than
    end-to-end. The prompt's literal ``{Agent-ID}``/``{Finding}`` placeholders
    are escaped, so ``str.format`` no longer raises ``KeyError`` and the audit
    round can be driven for real.
    """

    def test_auditor_reserves_the_debate_developers(self):
        excluded_developers, excluded_models = engine._seat_exclusions(ROSTER, "auditor")
        assert excluded_developers == frozenset({"anthropic", "openai", "google", "x-ai"})
        assert "z-ai" not in excluded_developers
        assert ROSTER["auditor"] not in excluded_models
        assert excluded_models == frozenset(
            model for role, model in ROSTER.items() if role != "auditor"
        )

    @pytest.mark.asyncio
    async def test_auditor_reports_the_model_that_served_it(self, monkeypatch: pytest.MonkeyPatch):
        served = "openrouter/z-ai/glm-5.3-flash"
        probe, _calls = patch_completion(monkeypatch, lambda _requested: served)
        excluded_developers, excluded_models = engine._seat_exclusions(ROSTER, "auditor")
        role, content, error, _reasoning, _usage, model_id = await engine._call_model(
            role="auditor",
            model=ROSTER["auditor"],
            prompt="review this",
            system_prompt="You are an independent auditor.",
            timeout_s=5,
            excluded_models=excluded_models,
            excluded_developers=excluded_developers,
        )
        assert (role, error) == ("auditor", None)
        assert content
        assert model_id == served
        assert len(probe.contexts) == 1
        assert probe.contexts[0]["preferred_model"] == ROSTER["auditor"]
