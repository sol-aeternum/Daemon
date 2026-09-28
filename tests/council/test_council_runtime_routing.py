"""Council seats under the real runtime: qualified routes, real attribution.

Unlike the mocked routing tests, this exercises the actual account scope and
``guarded_completion`` dispatch: the model a seat is reported as having served it
is only correct if the per-seat routing context survived the real selection
path, and only correct under concurrency if that state is genuinely per call.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator import model_routing
from orchestrator.compute_runtime import account_compute
from orchestrator.council import engine
from orchestrator.council.config import load_roster
from orchestrator.council.models import CouncilDiversityError, read_developer
from tests.qualified_compute import install_qualified_compute

ROSTER = load_roster("default")
DEBATE_ROLES = [role for role in ROSTER if role != "auditor"]


def _served_model(content: str) -> str:
    """Each stub answer names, on its first line, the route that produced it."""
    prefix = "served:"
    first_line = content.splitlines()[0] if content else ""
    assert first_line.startswith(prefix), content
    return first_line[len(prefix) :].strip()


@pytest.fixture
def dispatched_routes(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Qualify every model the council profile could use and record dispatch."""
    profile = model_routing.load_model_routing().profile("council")
    candidates = set(ROSTER.values())
    for group in profile.groups:
        candidates.update(group.models)
    install_qualified_compute(monkeypatch, models=tuple(sorted(candidates)))

    dispatched: list[dict[str, Any]] = []

    async def _acompletion(**params: Any) -> Any:
        dispatched.append({"model": params["model"]})
        # Interleave the seats so a shared selection would be observable.
        await asyncio.sleep(0.01)
        return SimpleNamespace(
            model=params["model"],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=f"served: {params['model']}\n\n**Confidence**: 7/10"
                    )
                )
            ],
        )

    monkeypatch.setattr("litellm.acompletion", _acompletion)
    return dispatched


async def _run_round_1() -> list[Any]:
    async with account_compute(
        object(),
        uuid.UUID("00000000-0000-0000-0000-0000000000c1"),
        operation="agent",
        auto_route=True,
        extended=True,
    ):
        return await engine.run_round_1("Should we ship it?", dict(ROSTER))


class TestRealRuntimeAttribution:
    @pytest.mark.asyncio
    async def test_each_seat_reports_the_route_that_actually_answered(
        self, dispatched_routes: list[dict[str, Any]]
    ):
        responses = await _run_round_1()

        assert [response.perspective.value for response in responses] == DEBATE_ROLES
        for response in responses:
            assert response.model_id == _served_model(response.content), (
                f"{response.perspective.value} reported {response.model_id!r} but was answered by "
                f"{_served_model(response.content)!r}"
            )
        assert {entry["model"] for entry in dispatched_routes} == {
            response.model_id for response in responses
        }

    @pytest.mark.asyncio
    async def test_no_seat_is_served_by_another_seats_developer(
        self, dispatched_routes: list[dict[str, Any]]
    ):
        responses = await _run_round_1()

        served = {
            response.perspective.value: read_developer(response.model_id or "")
            for response in responses
        }
        planned = {role: read_developer(model) for role, model in ROSTER.items()}
        for role, developer in served.items():
            reserved = {
                other_developer
                for other_role, other_developer in planned.items()
                if other_role != role and other_developer != developer
            }
            assert developer not in reserved, (
                f"{role} was served by {developer}, which the roster plans for another seat"
            )
        assert len(set(served.values())) >= 3

    @pytest.mark.asyncio
    async def test_a_seat_keeps_its_preference_when_it_fits_the_first_group(
        self, dispatched_routes: list[dict[str, Any]]
    ):
        responses = await _run_round_1()

        first_group = set(model_routing.load_model_routing().profile("council").groups[0].models)
        eligible_preferences = {
            response.perspective.value: ROSTER[response.perspective.value]
            for response in responses
            if ROSTER[response.perspective.value] in first_group
            and model_routing.is_model_routable(
                "council",
                ROSTER[response.perspective.value],
                excluded_models=frozenset(
                    model for role, model in ROSTER.items() if role != response.perspective.value
                ),
                excluded_developers=frozenset(
                    developer
                    for role, model in ROSTER.items()
                    if role != response.perspective.value
                    and (developer := read_developer(model)) is not None
                    and developer != read_developer(ROSTER[response.perspective.value])
                ),
            )
        }
        assert eligible_preferences, "the shipped roster should place seats in the first group"
        for role, preference in eligible_preferences.items():
            served = next(r for r in responses if r.perspective.value == role)
            assert served.model_id == preference, (
                f"{role} was eligible for {preference!r} but was served by {served.model_id!r}"
            )

    @pytest.mark.asyncio
    async def test_a_single_qualified_developer_cannot_masquerade_as_a_council(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # One developer's models only: every seat is served by the same vendor, so
        # the round must refuse rather than report a unanimous one-voice council.
        install_qualified_compute(
            monkeypatch,
            models=(
                "openrouter/deepseek/deepseek-v4.1-flash",
                "openrouter/deepseek/deepseek-v4.2",
            ),
        )

        async def _acompletion(**params: Any) -> Any:
            await asyncio.sleep(0)
            return SimpleNamespace(
                model=params["model"],
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=f"served: {params['model']}\n\n**Confidence**: 7/10"
                        )
                    )
                ],
            )

        monkeypatch.setattr("litellm.acompletion", _acompletion)
        with pytest.raises(CouncilDiversityError, match="model developers"):
            await _run_round_1()
