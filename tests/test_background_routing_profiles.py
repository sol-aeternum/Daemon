"""Background and nested workloads route by profile, not by a configured model.

These tests assert the two things that must never drift again:

1. Each workload binds the intended inference profile, and non-benchmark calls
   send no model at all, so the compute guard is free to select an approved
   route rather than being pinned to a stale settings default.
2. Persisted provenance is the route the guard *actually* selected for that
   call, read out of the live routing state, never a configured hint and never
   a misleading literal such as ``"auto"``.

The fake provider below populates ``current_routing()`` exactly the way the real
guard does (``record_selection`` after dispatch), so callers that read
``selected_model`` for provenance are exercised against a real selection.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from contextlib import ExitStack, asynccontextmanager, contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.config import Settings
from orchestrator.memory.dedup import (
    AUTOMATIC_CONTRADICTION_MAX_TOKENS,
    BENCHMARK_CONTRADICTION_MODEL,
    BENCHMARK_CONTRADICTION_MAX_TOKENS,
    check_contradiction,
)
from orchestrator.memory.dreaming import DREAM_PROFILE, dream_on_cluster
from orchestrator.memory.entities import CandidateMention, EntityResolution, confirm_merge_llm
from orchestrator.memory.extraction import (
    BENCHMARK_EXTRACTION_MODEL,
    extract_facts_from_text,
)
from orchestrator.memory.store import MemoryStore
from orchestrator.memory.summarization import generate_summary
from orchestrator.memory.titles import generate_conversation_title
from orchestrator.model_routing import RoutingState, current_routing
from orchestrator.worker import jobs

# Selected routes stand in for approved routes; they are deliberately distinct
# from anything a config default could have named, so a stale value cannot
# accidentally satisfy a provenance assertion.
SELECTED_MODEL = "openrouter/z-ai/glm-5.3"
SELECTED_ROUTE_ID = "route-selected-glm"
ROTATED_MODEL = "openrouter/anthropic/claude-sonnet-5"

TEST_OWNER = uuid.UUID("00000000-0000-0000-0000-0000000000a1")


class FakeResponse:
    """Minimal litellm-shaped response covering both access patterns."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]

    def model_dump(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": self._content}}]}


class RecordingGuard:
    """A ``guarded_completion`` stand-in that performs a real route selection.

    It reads the live routing state, records the dispatch the way the real
    guard does, captures the call kwargs, and exposes the state so a test can
    assert which profile was actually bound for that specific call.
    """

    def __init__(
        self,
        content: str,
        *,
        model: str = SELECTED_MODEL,
        route_id: str = SELECTED_ROUTE_ID,
    ) -> None:
        self._content = content
        self._model = model
        self._route_id = route_id
        self.calls: list[dict[str, Any]] = []
        self.states: list[RoutingState] = []

    async def __call__(self, **params: Any) -> FakeResponse:
        state = current_routing()
        self.states.append(state)
        self.calls.append(params)
        state.record_selection(
            model=self._model, route_id=self._route_id, group="cheap", explicit=False
        )
        return FakeResponse(self._content)

    @property
    def profiles(self) -> list[str]:
        return [state.profile for state in self.states]

    @property
    def last_call(self) -> dict[str, Any]:
        assert self.calls, "the workload made no inference call"
        return self.calls[-1]


def _provider_config(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "name": "openrouter",
        "timeout_s": 45,
        "base_url": "",
        "api_key": None,
        "extra_headers": {},
        "requires_auth": False,
        "model": "auto",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@contextmanager
def _patched_settings(**overrides: Any):
    """Patch ``get_settings`` in the workload modules with a provider-only config.

    Notably absent: any legacy model field. If production code reached for a
    stale deployment model default, this stub would raise instead of quietly
    satisfying the call.
    """
    settings = SimpleNamespace(get_provider_config=lambda *_a, **_k: _provider_config(**overrides))
    targets = [
        "orchestrator.memory.titles",
        "orchestrator.memory.summarization",
        "orchestrator.memory.extraction",
        "orchestrator.memory.entities",
        "orchestrator.memory.dreaming",
        "orchestrator.tools.memory_reflect",
    ]
    with ExitStack() as stack:
        for target in targets:
            stack.enter_context(patch(f"{target}.get_settings", return_value=settings, create=True))
        yield settings


# ---------------------------------------------------------------------------
# 1. Profile binding per workload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_title_generation_uses_background_profile_without_pinned_model() -> None:
    guard = RecordingGuard("Understanding The Daemon Project")

    with _patched_settings():
        with patch("orchestrator.memory.titles.guarded_completion", new=guard):
            title = await generate_conversation_title(
                [{"role": "user", "content": "Tell me about the daemon project"}]
            )

    assert title
    assert guard.profiles == ["background"]
    # No model hint at all: the guard, not config, chooses the route.
    assert "model" not in guard.last_call


@pytest.mark.asyncio
async def test_conversation_summary_uses_background_profile() -> None:
    guard = RecordingGuard("Test summary. Open: none")

    with patch("orchestrator.memory.summarization.guarded_completion", new=guard):
        summary = await generate_summary([{"role": "user", "content": "Hello"}])

    assert "Open:" in summary
    assert guard.profiles == ["background"]
    assert "model" not in guard.last_call


@pytest.mark.asyncio
async def test_structured_extraction_uses_background_profile() -> None:
    payload = json.dumps(
        {"facts": [{"content": "User lives in Adelaide", "category": "fact", "confidence": 0.9}]}
    )
    guard = RecordingGuard(payload)

    with _patched_settings():
        with patch("orchestrator.memory.extraction.guarded_completion", new=guard):
            outcome = await extract_facts_from_text("I live in Adelaide")

    assert guard.profiles == ["background"]
    assert "model" not in guard.last_call
    # Provenance is the route actually selected, not "auto".
    assert outcome.model_used == SELECTED_MODEL
    assert outcome.model_used != "auto"


@pytest.mark.asyncio
async def test_entity_merge_confirmation_uses_background_profile() -> None:
    guard = RecordingGuard("UNSURE: could be the same person.")
    resolution = EntityResolution(
        mention=CandidateMention(text="Mike", normalized_key="mike", context="my brother Mike"),
        canonical_name="Michael",
        similarity=0.72,
        merge_decision="ambiguous",
    )

    with _patched_settings():
        with patch("orchestrator.memory.entities.guarded_completion", new=guard):
            confirmed, explanation = await confirm_merge_llm(resolution)

    assert explanation
    assert confirmed is False
    assert guard.profiles == ["background"]
    assert "model" not in guard.last_call


@pytest.mark.asyncio
async def test_contradiction_detection_uses_reasoning_profile() -> None:
    guard = RecordingGuard("NO. The facts are consistent.")

    with patch("orchestrator.memory.dedup.guarded_completion", new=guard):
        detected, _ = await check_contradiction("User drives a Tesla", "User drives a Tesla")

    assert detected is False
    assert guard.profiles == ["reasoning"]
    assert "model" not in guard.last_call
    # The automatic verdict uses the approved helper output budget: a
    # reasoning-preset response must not be truncated into a false "NO".
    assert guard.last_call["max_tokens"] == AUTOMATIC_CONTRADICTION_MAX_TOKENS


@pytest.mark.asyncio
async def test_dreaming_uses_reasoning_profile_and_reports_selected_route() -> None:
    memory_id = uuid.uuid4()
    payload = json.dumps(
        {
            "observations": [
                {
                    "content": "User keeps a stable weekly cycling routine.",
                    "confidence": 0.86,
                    "source_memory_ids": [str(memory_id)],
                }
            ]
        }
    )
    guard = RecordingGuard(payload)
    memories = [
        {
            "id": memory_id,
            "content": "User bikes to work three times a week.",
            "category": "fact",
            "memory_slot": "fitness.cycling.frequency",
        }
    ]

    with _patched_settings():
        with patch("orchestrator.memory.dreaming.guarded_completion", new=guard):
            observations, model_used = await dream_on_cluster(memories)

    assert guard.profiles == [DREAM_PROFILE]
    assert "model" not in guard.last_call
    assert len(observations) == 1
    # The route that actually served this call is reported for provenance.
    assert model_used == SELECTED_MODEL


@pytest.mark.asyncio
async def test_nested_reflection_binds_its_own_reasoning_profile() -> None:
    """A nested helper must not inherit the enclosing turn's model choice."""
    from orchestrator.tools.memory_reflect import MemoryReflectTool

    guard = RecordingGuard("You play guitar.")
    store = MagicMock(spec=MemoryStore)
    user_id = uuid.uuid4()

    with _patched_settings():
        with patch(
            "orchestrator.tools.memory_reflect.retrieve_memories_for_text",
            new=AsyncMock(return_value=[{"id": uuid.uuid4(), "content": "User plays guitar"}]),
        ):
            with patch("orchestrator.tools.memory_reflect.guarded_completion", new=guard):
                result = await MemoryReflectTool(store, user_id).execute(topic="guitar")

    assert result == "You play guitar."
    # The helper's own profile, and still no pinned model inherited from a parent.
    assert guard.profiles == ["reasoning"]
    assert "model" not in guard.last_call


@pytest.mark.asyncio
async def test_explicit_test_injection_still_reaches_the_guard() -> None:
    """An explicit injection is a caller choice, and it is sent as a preference."""
    guard = RecordingGuard("Understanding The Daemon Project")
    pinned = "openrouter/test/explicit-model"

    with _patched_settings():
        with patch("orchestrator.memory.titles.guarded_completion", new=guard):
            await generate_conversation_title(
                [{"role": "user", "content": "Tell me about the daemon project"}],
                model=pinned,
            )

    assert guard.last_call["model"] == pinned
    assert guard.states[0].preferred_model == pinned
    # Still bound to the workload profile, and provenance reflects the dispatch.
    assert guard.profiles == ["background"]
    assert guard.states[0].selected_model == SELECTED_MODEL


# ---------------------------------------------------------------------------
# 2. Provenance reflects rotation across calls
# ---------------------------------------------------------------------------


def _dream_settings() -> SimpleNamespace:
    return SimpleNamespace(
        dreaming_enabled=True,
        dream_min_cluster_size=1,
        embedding_document_model="voyage-4-large",
    )


@pytest.mark.asyncio
async def test_dreaming_provenance_records_every_route_actually_selected() -> None:
    """A run spanning two families records both routes, in first-seen order."""
    from orchestrator.memory.embedding import EmbeddingConfigurationError

    user_id = uuid.uuid4()
    first_id, second_id = uuid.uuid4(), uuid.uuid4()
    store = AsyncMock()
    store.get_dream_candidate_memories.return_value = [
        {
            "id": first_id,
            "content": "User bikes to work.",
            "category": "fact",
            "memory_slot": "fitness.cycling.frequency",
        },
        {
            "id": second_id,
            "content": "User prefers pour-over coffee.",
            "category": "preference",
            "memory_slot": "food.coffee.method",
        },
    ]
    store.get_dream_runs.return_value = []
    store.insert_memory.side_effect = lambda **kw: {"id": uuid.uuid4()}

    served: list[str] = []

    async def _fake_dream(mems: list[dict[str, Any]], **_: Any):
        # The first family lands on one route, the second rotates to another.
        model = SELECTED_MODEL if not served else ROTATED_MODEL
        served.append(model)
        return (
            [
                {
                    "content": "User keeps a stable routine here.",
                    "confidence": 0.8,
                    "source_memory_ids": [str(mems[0]["id"])],
                }
            ],
            model,
        )

    with (
        patch("orchestrator.memory.dreaming.get_settings", return_value=_dream_settings()),
        patch("orchestrator.memory.dreaming.dream_on_cluster", new=_fake_dream),
        patch(
            "orchestrator.memory.dreaming.embed_documents_with_metadata",
            new=AsyncMock(side_effect=EmbeddingConfigurationError("route unavailable")),
        ),
    ):
        result = await jobs.run_dreaming(user_id, store=store)  # type: ignore[arg-type]

    assert len(served) == 2
    assert result["status"] == "completed"
    assert result["families_processed"] == 2
    # Accurate provenance: both routes really served this run, in call order.
    assert store.log_dream_run.await_args.kwargs["model_used"] == (
        f"{SELECTED_MODEL},{ROTATED_MODEL}"
    )


@pytest.mark.asyncio
async def test_dreaming_provenance_is_none_when_no_inference_happened() -> None:
    """A skipped run has no route, so it must not record a placeholder model."""
    user_id = uuid.uuid4()
    store = AsyncMock()
    store.get_dream_candidate_memories.return_value = [
        {
            "id": uuid.uuid4(),
            "content": "User bikes to work.",
            "category": "fact",
            "memory_slot": "fitness.cycling.frequency",
        }
    ]
    store.get_dream_runs.return_value = []
    settings = SimpleNamespace(
        dreaming_enabled=True,
        dream_min_cluster_size=5,  # too small to form a cluster
        embedding_document_model="voyage-4-large",
    )

    with patch("orchestrator.memory.dreaming.get_settings", return_value=settings):
        with patch("orchestrator.memory.dreaming.dream_on_cluster") as dream:
            result = await jobs.run_dreaming(user_id, store=store)  # type: ignore[arg-type]

    assert result["status"] == "skipped"
    dream.assert_not_awaited()
    assert store.log_dream_run.await_args.kwargs["model_used"] is None


@pytest.mark.asyncio
async def test_extraction_log_provenance_is_the_selected_route() -> None:
    """The extraction audit log records the dispatched route, not a placeholder."""
    from orchestrator.memory.extraction import process_extraction

    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    payload = json.dumps(
        {"facts": [{"content": "User lives in Adelaide", "category": "fact", "confidence": 0.9}]}
    )
    guard = RecordingGuard(payload)
    store = AsyncMock()
    store.get_conversation = AsyncMock(return_value={"summary": None})
    store.log_extraction = AsyncMock(return_value={"id": uuid.uuid4()})

    async def _embed_documents_with_metadata(_texts: Any) -> Any:
        return SimpleNamespace(
            embeddings=[[0.1] * 4],
            provider="voyage",
            model="voyage-4-large",
            storage_model="voyage-4-large",
        )

    with _patched_settings():
        with (
            patch("orchestrator.memory.extraction.guarded_completion", new=guard),
            patch(
                "orchestrator.memory.dedup.embed_documents_with_metadata",
                new=_embed_documents_with_metadata,
            ),
            patch("orchestrator.memory.dedup.deduplicate_facts", new=AsyncMock()) as dedup,
            patch(
                "orchestrator.memory.summary._generate_or_update_summary_result",
                new=AsyncMock(
                    return_value=SimpleNamespace(summary=None, continuation_needed=False)
                ),
            ),
        ):
            dedup.return_value = SimpleNamespace(merged=[], superseded=[], new=[])
            await process_extraction(store, user_id, conversation_id, "I live in Adelaide")

    assert guard.profiles == ["background"]
    assert store.log_extraction.await_args.kwargs["model_used"] == SELECTED_MODEL
    assert store.log_extraction.await_args.kwargs["model_used"] != "auto"


# ---------------------------------------------------------------------------
# 3. Benchmark-only pinned models stay pinned
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_benchmark_extraction_stays_pinned_to_snapshot_model() -> None:
    guard = RecordingGuard(json.dumps({"facts": []}))

    with patch("orchestrator.memory.extraction.guarded_completion", new=guard):
        await extract_facts_from_text("I love Python", benchmark_mode=True)

    assert guard.last_call["model"] == BENCHMARK_EXTRACTION_MODEL
    assert guard.last_call["seed"] == 42
    assert guard.last_call["temperature"] == 0.0
    # The explicit pin bypasses the automatic shortlist but still attributes
    # to the workload's own profile.
    assert guard.profiles == ["background"]


@pytest.mark.asyncio
async def test_benchmark_contradiction_stays_pinned_to_snapshot_model() -> None:
    guard = RecordingGuard("NO")

    with patch("orchestrator.memory.dedup.guarded_completion", new=guard):
        await check_contradiction("User likes Python", "User loves Python", benchmark_mode=True)

    assert guard.last_call["model"] == BENCHMARK_CONTRADICTION_MODEL
    assert guard.last_call["seed"] == 42
    assert guard.last_call["temperature"] == 0.0
    assert guard.last_call["max_tokens"] == BENCHMARK_CONTRADICTION_MAX_TOKENS
    assert guard.profiles == ["routine"]


# ---------------------------------------------------------------------------
# 4. Job scopes declare the profile their workload requires
# ---------------------------------------------------------------------------


def _scope_recorder(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace account_compute and record the profile each job requests."""

    @asynccontextmanager
    async def _fake_scope(
        pool: Any,
        user_id: Any,
        *,
        operation: str,
        auto_route: bool,
        background: bool = False,
        profile: str,
        **_kwargs: Any,
    ):
        recorded.append(
            {
                "operation": operation,
                "auto_route": auto_route,
                "background": background,
                "profile": profile,
            }
        )
        yield SimpleNamespace(user_id=user_id, auto_route=auto_route)

    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(jobs, "account_compute", _fake_scope)
    return recorded


def _title_store() -> Any:
    store = MagicMock(spec=MemoryStore)
    store.get_conversation = AsyncMock(
        return_value={"user_id": TEST_OWNER, "title_locked": False, "summary": None}
    )
    store.get_messages = AsyncMock(return_value=[{"role": "user", "content": "hello"}])
    store.update_conversation = AsyncMock(return_value={})
    store.count_messages = AsyncMock(return_value=50)
    return store


def _summary_cursor_store() -> Any:
    """A store whose summary-cursor API satisfies the worker's scheduling path."""
    store = _title_store()
    store.count_summary_messages = AsyncMock(return_value=50)
    store.count_contiguous_finalized_messages_at = AsyncMock(return_value=50)
    store.get_summary_message_batch = AsyncMock(return_value=[{"role": "user", "content": "hello"}])
    store.update_conversation_summary = AsyncMock(return_value=True)
    return store


@pytest.mark.asyncio
async def test_title_jobs_open_background_scoped_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _scope_recorder(monkeypatch)
    ctx: dict[str, Any] = {
        "store": _title_store(),
        "db_pool": object(),
        "settings": Settings(),
    }

    with patch.object(
        jobs, "generate_conversation_title", new=AsyncMock(return_value="A Title")
    ) as title:
        await jobs.generate_title(ctx, uuid.uuid4(), "hello")
        await jobs.generate_conversation_title_job(ctx, uuid.uuid4())

    assert [entry["profile"] for entry in recorded] == ["background", "background"]
    assert all(entry["auto_route"] is True for entry in recorded)
    assert all(entry["background"] is True for entry in recorded)
    # The job no longer reads or forwards a configured title model.
    assert all(
        call.kwargs.get("model") is None and "model" not in call.args
        for call in title.await_args_list
    )


@pytest.mark.asyncio
async def test_summary_job_opens_background_scoped_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import orchestrator.memory.summarization as summarization_module

    recorded = _scope_recorder(monkeypatch)
    ctx: dict[str, Any] = {
        "store": _summary_cursor_store(),
        "db_pool": object(),
        "settings": Settings(),
    }

    with (
        patch.object(
            summarization_module,
            "should_summarize",
            new=AsyncMock(return_value=True),
        ),
        patch.object(
            summarization_module,
            "generate_summary",
            new=AsyncMock(return_value="Summary. Open: none"),
        ),
    ):
        result = await jobs.generate_summary_job(ctx, str(uuid.uuid4()))

    assert result["status"] == "success"
    assert [entry["profile"] for entry in recorded] == ["background"]
    assert all(entry["background"] is True for entry in recorded)


@pytest.mark.asyncio
async def test_dreaming_job_opens_reasoning_scoped_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _scope_recorder(monkeypatch)
    user_id = uuid.uuid4()
    store = MagicMock(spec=MemoryStore)
    store.get_users_with_dream_candidates = AsyncMock(return_value=[user_id])
    ctx: dict[str, Any] = {
        "store": store,
        "db_pool": object(),
        "settings": Settings(dreaming_enabled=True),
    }

    with patch.object(
        jobs,
        "run_dreaming",
        new=AsyncMock(return_value={"status": "skipped", "observations_created": 0}),
    ):
        await jobs.run_dreaming_job(ctx, str(user_id))

    assert [entry["profile"] for entry in recorded] == ["reasoning"]


@pytest.mark.asyncio
async def test_consolidation_job_opens_reasoning_scoped_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import orchestrator.memory.consolidation as consolidation_module

    recorded = _scope_recorder(monkeypatch)
    user_id = uuid.uuid4()
    store = MagicMock(spec=MemoryStore)
    store.list_users_with_eligible_l1_memories = AsyncMock(return_value=[user_id])
    ctx: dict[str, Any] = {
        "store": store,
        "db_pool": object(),
        "settings": Settings(consolidation_enabled=True),
    }
    cluster = consolidation_module.MemoryCluster(
        slot_family="food.coffee",
        members=[
            {"id": uuid.uuid4(), "content": f"User brews coffee {index}", "category": "fact"}
            for index in range(3)
        ],
    )

    with (
        patch.object(
            consolidation_module,
            "find_memory_clusters",
            new=AsyncMock(return_value=[cluster]),
        ),
        patch.object(consolidation_module, "consolidate_cluster", new=AsyncMock(return_value=[])),
    ):
        await jobs.consolidate_memories(ctx, str(uuid.uuid4()))

    assert [entry["profile"] for entry in recorded] == ["reasoning"]
    assert user_id  # the user set is resolved from the store, not hardcoded


@pytest.mark.asyncio
async def test_skill_evaluation_job_opens_reasoning_scoped_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evaluation is a judgement workload, so its account scope is reasoning.

    The assertion is the scope the job opened, which happens before the
    evaluator's own internals run; those are covered by the evaluator's suite.
    """
    import orchestrator.skill_evaluator as evaluator_module

    recorded = _scope_recorder(monkeypatch)
    store = MagicMock(spec=MemoryStore)
    store.get_conversation = AsyncMock(return_value={"user_id": TEST_OWNER})
    ctx: dict[str, Any] = {"store": store, "db_pool": object(), "settings": Settings()}

    with patch.object(
        evaluator_module,
        "SkillEvaluator",
        new=MagicMock(side_effect=RuntimeError("stop after the scope")),
    ):
        with contextlib.suppress(RuntimeError):
            await jobs.run_skill_evaluation_job(
                ctx,
                str(TEST_OWNER),
                str(uuid.uuid4()),
                str(uuid.uuid4()),
                0,
            )

    assert [entry["profile"] for entry in recorded] == ["reasoning"]


@pytest.mark.asyncio
async def test_skill_consolidation_nudge_uses_reasoning_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The nudge prompt is a model judgement, so it must not pin a fast model."""
    recorded = _scope_recorder(monkeypatch)
    user_id = uuid.uuid4()
    store = MagicMock(spec=MemoryStore)
    store.get_user_conversation_count_since_last_nudge = AsyncMock(return_value=5)
    store.get_total_conversation_count = AsyncMock(return_value=5)
    store.get_autonomous_skill_candidates = AsyncMock(
        return_value=[{"skill_id": "skill-1", "name": "A Skill", "description": "does things"}]
    )
    store.get_recent_memories_for_user = AsyncMock(return_value=[])
    store.record_consolidation_nudge_run = AsyncMock(return_value={})

    with patch.object(
        jobs, "_call_consolidation_model", new=AsyncMock(return_value=[])
    ) as call_model:
        await jobs._process_user_consolidation_nudge(
            user_id=user_id,
            store=store,
            interval=1,
            stale_days=30,
            min_skills=1,
            db_pool=object(),
        )

    assert [entry["profile"] for entry in recorded] == ["reasoning"]
    call_model.assert_awaited_once()


@pytest.mark.asyncio
async def test_consolidation_nudge_prompt_pins_no_model() -> None:
    """The nudge call itself must send no model, so the profile can choose."""
    guard = RecordingGuard(json.dumps({"actions": []}))

    with patch("orchestrator.worker.jobs.guarded_completion", new=guard):
        await jobs._call_consolidation_model("consolidate these skills")

    assert "model" not in guard.last_call
    # The nudge prompt's structured contract is preserved for model rotation.
    assert guard.last_call["response_format"] == {"type": "json_object"}


# ---------------------------------------------------------------------------
# 5. No stale deployment model configuration remains
# ---------------------------------------------------------------------------


LEGACY_MODEL_FIELDS = (
    "auto_fast_model",
    "auto_fast_model_grok",
    "auto_reasoning_model",
    "background_reasoning_model",
    "title_model",
    "default_tier",
    "tier_free_orchestrator_model",
    "tier_pro_orchestrator_model",
    "tier_max_orchestrator_model",
    "tier_byok_orchestrator_model",
)


@pytest.mark.parametrize("field", LEGACY_MODEL_FIELDS)
def test_legacy_model_settings_are_gone(field: str) -> None:
    """A deployment default model would silently outrank the profile again."""
    assert not hasattr(Settings, field), f"{field} must not be a deployment default"


def test_provider_config_no_longer_names_an_orchestrator_model() -> None:
    config = Settings().get_provider_config("openrouter")

    assert config.model == "auto"
