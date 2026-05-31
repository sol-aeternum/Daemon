from __future__ import annotations

# pyright: reportAny=false, reportExplicitAny=false

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.config import Settings
from orchestrator.memory.dreaming import dream_on_cluster, run_dreaming
from orchestrator.memory.retrieval import retrieve_memories
from orchestrator.memory.store import MemoryStore
from orchestrator.worker.jobs import run_dreaming_job, _user_matches_dream_schedule_hour


class MockLitellmResponse:
    def __init__(self, content: str):
        self._content = content

    def model_dump(self):
        return {"choices": [{"message": {"content": self._content}}]}


@pytest.mark.asyncio
async def test_dream_on_cluster_uses_background_reasoning_model() -> None:
    settings = SimpleNamespace(
        background_reasoning_model="openrouter/deepseek/deepseek-chat",
        get_provider_config=lambda _provider: SimpleNamespace(
            timeout_s=45,
            base_url="",
            api_key=None,
            extra_headers={},
        ),
    )
    memories = [
        {
            "id": uuid.uuid4(),
            "content": "User bikes to work three times a week.",
            "category": "fact",
            "memory_slot": "fitness.cycling.frequency",
        },
        {
            "id": uuid.uuid4(),
            "content": "User prefers bike commuting over driving.",
            "category": "preference",
            "memory_slot": "fitness.cycling.preference",
        },
    ]

    with patch("orchestrator.memory.dreaming.get_settings", return_value=settings):
        with patch("orchestrator.memory.dreaming.litellm.acompletion") as mock_llm:
            mock_llm.return_value = MockLitellmResponse(
                '{"observations": ['
                '{"content": "keeps cycling as a stable weekly routine.", "confidence": 0.86, "source_memory_ids": ["'
                + str(memories[0]["id"])
                + '", "'
                + str(memories[1]["id"])
                + '"]}, '
                '{"content": "User treats bike commuting as both transportation and preference.", "confidence": 0.73, "source_memory_ids": ["'
                + str(memories[1]["id"])
                + '"]}'
                "]}"
            )

            observations = await dream_on_cluster(memories)

    assert len(observations) == 2
    assert mock_llm.call_args.kwargs["model"] == settings.background_reasoning_model
    assert observations[0]["content"].startswith("User ")
    assert observations[0]["confidence"] == 0.86
    assert observations[0]["source_memory_ids"] == [
        str(memories[0]["id"]),
        str(memories[1]["id"]),
    ]


@pytest.mark.asyncio
async def test_run_dreaming_skips_unchanged_families_and_logs_run() -> None:
    user_id = uuid.uuid4()
    last_run_completed_at = datetime.now(timezone.utc) - timedelta(hours=1)
    older_time = last_run_completed_at - timedelta(days=2)
    newer_time = last_run_completed_at + timedelta(minutes=5)

    settings = SimpleNamespace(
        dreaming_enabled=True,
        dream_min_cluster_size=2,
        background_reasoning_model="openrouter/deepseek/deepseek-chat",
        embedding_document_model="voyage-4-large",
    )
    store = AsyncMock()
    store.get_dream_candidate_memories.return_value = [
        {
            "id": uuid.uuid4(),
            "content": "User likes pour-over coffee.",
            "category": "preference",
            "memory_slot": "food.coffee.method",
            "created_at": newer_time,
        },
        {
            "id": uuid.uuid4(),
            "content": "User buys single-origin beans.",
            "category": "fact",
            "memory_slot": "food.coffee.beans",
            "created_at": newer_time,
        },
        {
            "id": uuid.uuid4(),
            "content": "User plays guitar on weekends.",
            "category": "fact",
            "memory_slot": "hobby.music.instrument",
            "created_at": older_time,
        },
        {
            "id": uuid.uuid4(),
            "content": "User owns a Stratocaster.",
            "category": "fact",
            "memory_slot": "hobby.music.gear",
            "created_at": older_time,
        },
    ]
    store.get_dream_runs.return_value = [
        {
            "status": "completed",
            "run_completed_at": last_run_completed_at,
        }
    ]
    store.insert_memory.return_value = {"id": uuid.uuid4()}
    store.log_dream_run.return_value = {"id": uuid.uuid4()}

    with patch("orchestrator.memory.dreaming.get_settings", return_value=settings):
        with patch(
            "orchestrator.memory.dreaming.dream_on_cluster",
            AsyncMock(
                return_value=[
                    {
                        "content": "User has consistent coffee rituals and quality-focused preferences.",
                        "confidence": 0.88,
                        "source_memory_ids": [
                            str(store.get_dream_candidate_memories.return_value[0]["id"]),
                            str(store.get_dream_candidate_memories.return_value[1]["id"]),
                        ],
                    }
                ]
            ),
        ) as mock_dream:
            with patch(
                "orchestrator.memory.dreaming.embed_documents",
                AsyncMock(return_value=[[0.1, 0.2, 0.3]]),
            ):
                result = await run_dreaming(user_id, store=store)

    assert result["status"] == "completed"
    assert result["families_processed"] == 1
    assert result["observations_created"] == 1
    assert "hobby.music" in result["skipped_families"]
    assert mock_dream.await_count == 1
    insert_call = store.insert_memory.await_args.kwargs
    assert insert_call["category"] == "observation"
    assert insert_call["source_type"] == "dream"
    assert insert_call["content"].startswith("User ")
    assert insert_call["confidence"] == 0.88
    metadata_call = store.update_memory_metadata.await_args.args[1]
    assert metadata_call["dream_family"] == "food.coffee"
    assert len(metadata_call["source_memory_ids"]) == 2


@pytest.mark.asyncio
async def test_run_dreaming_returns_skipped_when_disabled() -> None:
    settings = SimpleNamespace(dreaming_enabled=False)
    with patch("orchestrator.memory.dreaming.get_settings", return_value=settings):
        result = await run_dreaming(uuid.uuid4(), store=AsyncMock())

    assert result["status"] == "skipped"
    assert result["reason"] == "dreaming_disabled"


@pytest.mark.asyncio
async def test_run_dreaming_job_processes_all_candidate_users() -> None:
    user_ids = [uuid.uuid4(), uuid.uuid4()]
    store = object.__new__(MemoryStore)
    store.get_users_with_dream_candidates = AsyncMock(return_value=user_ids)
    settings = Settings(dreaming_enabled=True)

    with patch(
        "orchestrator.worker.jobs.run_dreaming",
        AsyncMock(
            side_effect=[
                {"status": "completed", "observations_created": 2},
                {"status": "skipped", "observations_created": 0},
            ]
        ),
    ):
        result = await run_dreaming_job({"store": store, "settings": settings})

    assert result["status"] == "ok"
    assert result["users_processed"] == 2
    assert result["dream_runs_completed"] == 1
    assert result["dream_runs_skipped"] == 1
    assert result["observations_created"] == 2


@pytest.mark.asyncio
async def test_default_retrieval_excludes_dream_observations() -> None:
    store = AsyncMock()
    store.search_memories.return_value = []
    store.search_memories_bm25.return_value = []

    _ = await retrieve_memories(
        store=store,
        query_embedding=[0.1, 0.2, 0.3],
        query_text="coffee habits",
        user_id=uuid.uuid4(),
    )

    assert store.search_memories.await_args.kwargs["include_dream_observations"] is False
    assert store.search_memories_bm25.await_args.kwargs["include_dream_observations"] is False


@pytest.mark.asyncio
async def test_dreaming_schedule_uses_user_timezone_when_present() -> None:
    store = AsyncMock()
    user_id = uuid.uuid4()
    store.get_user_settings.return_value = {"preferences": {"timezone": "Australia/Adelaide"}}

    should_run = await _user_matches_dream_schedule_hour(
        store,
        user_id,
        3,
        now_utc=datetime(2026, 4, 10, 17, 30, tzinfo=timezone.utc),
    )

    assert should_run is True


@pytest.mark.asyncio
async def test_scheduled_dreaming_job_skips_users_outside_local_hour() -> None:
    user_ids = [uuid.uuid4(), uuid.uuid4()]
    store = object.__new__(MemoryStore)
    store.get_users_with_dream_candidates = AsyncMock(return_value=user_ids)
    store.get_user_settings = AsyncMock(
        side_effect=[
            {"preferences": {"timezone": "UTC"}},
            {"preferences": {"timezone": "Australia/Adelaide"}},
        ]
    )
    settings = Settings(dreaming_enabled=True, dream_schedule_hour=3)

    with patch(
        "orchestrator.worker.jobs.run_dreaming",
        AsyncMock(return_value={"status": "completed", "observations_created": 1}),
    ) as mock_run:
        result = await run_dreaming_job(
            {"store": store, "settings": settings},
            scheduled=True,
            now_utc=datetime(2026, 4, 10, 17, 30, tzinfo=timezone.utc),
        )

    assert result["status"] == "ok"
    assert result["users_processed"] == 1
    assert result["dream_runs_completed"] == 1
    assert mock_run.await_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# Dreaming synthesis contract tests
# ─────────────────────────────────────────────────────────────────────────────


class TestNormalizeObservations:
    """Tests for _normalize_observations internal helper."""

    def test_rejects_malformed_json(self) -> None:
        from orchestrator.memory.dreaming import _normalize_observations

        result = _normalize_observations("not json at all", set())
        assert result == []

    def test_rejects_empty_observations_array(self) -> None:
        from orchestrator.memory.dreaming import _normalize_observations

        result = _normalize_observations('{"observations": []}', set())
        assert result == []

    def test_rejects_non_dict_observation_item(self) -> None:
        from orchestrator.memory.dreaming import _normalize_observations

        raw = '{"observations": ["just a string", 123, null]}'
        result = _normalize_observations(raw, set())
        assert result == []

    def test_rejects_observation_with_missing_content(self) -> None:
        from orchestrator.memory.dreaming import _normalize_observations

        raw = '{"observations": [{"confidence": 0.8, "source_memory_ids": []}]}'
        result = _normalize_observations(raw, set())
        assert result == []

    def test_rejects_observation_with_too_short_content(self) -> None:
        from orchestrator.memory.dreaming import _normalize_observations

        raw = '{"observations": [{"content": "Short", "confidence": 0.8, "source_memory_ids": []}]}'
        result = _normalize_observations(raw, set())
        assert result == []

    def test_rejects_observation_with_missing_confidence(self) -> None:
        from orchestrator.memory.dreaming import _normalize_observations

        raw = '{"observations": [{"content": "User bikes to work every day", "source_memory_ids": []}]}'
        result = _normalize_observations(raw, set())
        assert result == []

    def test_rejects_observation_with_invalid_confidence_type(self) -> None:
        from orchestrator.memory.dreaming import _normalize_observations

        raw = '{"observations": [{"content": "User bikes to work every day", "confidence": "high", "source_memory_ids": []}]}'
        result = _normalize_observations(raw, set())
        assert result == []

    def test_rejects_observation_with_out_of_range_confidence(self) -> None:
        from orchestrator.memory.dreaming import _normalize_observations

        raw = '{"observations": [{"content": "User bikes to work every day", "confidence": 1.5, "source_memory_ids": []}]}'
        result = _normalize_observations(raw, set())
        # Out-of-range should be clamped to 1.0
        assert result == []

    def test_rejects_observation_with_missing_source_memory_ids(self) -> None:
        from orchestrator.memory.dreaming import _normalize_observations

        raw = '{"observations": [{"content": "User bikes to work every day", "confidence": 0.8}]}'
        result = _normalize_observations(raw, set())
        assert result == []

    def test_rejects_observation_with_source_ids_not_in_valid_set(self) -> None:
        from orchestrator.memory.dreaming import _normalize_observations

        valid_ids = {"aaa", "bbb"}
        # Only ccc is provided, which is NOT in valid set -> observation rejected
        raw = '{"observations": [{"content": "User bikes to work every day and enjoys it", "confidence": 0.8, "source_memory_ids": ["ccc"]}]}'
        result = _normalize_observations(raw, valid_ids)
        assert result == []

    def test_accepts_valid_observation_with_only_valid_source_ids(self) -> None:
        from orchestrator.memory.dreaming import _normalize_observations

        valid_ids = {"mem-1", "mem-2"}
        raw = '{"observations": [{"content": "User bikes to work every day and enjoys it", "confidence": 0.85, "source_memory_ids": ["mem-1", "mem-2"]}]}'
        result = _normalize_observations(raw, valid_ids)
        assert len(result) == 1
        assert result[0]["content"] == "User bikes to work every day and enjoys it"
        assert result[0]["confidence"] == 0.85
        assert result[0]["source_memory_ids"] == ["mem-1", "mem-2"]

    def test_deduplicates_identical_observation_content(self) -> None:
        from orchestrator.memory.dreaming import _normalize_observations

        valid_ids = {"mem-1", "mem-2"}
        raw = '{"observations": ['
        raw += '{"content": "User bikes to work every day and enjoys it", "confidence": 0.85, "source_memory_ids": ["mem-1"]},'
        raw += '{"content": "User bikes to work every day and enjoys it", "confidence": 0.90, "source_memory_ids": ["mem-2"]}'
        raw += "]}"
        result = _normalize_observations(raw, valid_ids)
        # Only one observation should be kept (deduped by content)
        assert len(result) == 1

    def test_limits_to_max_observations_per_family(self) -> None:
        from orchestrator.memory.dreaming import _normalize_observations

        valid_ids = {f"mem-{i}" for i in range(10)}
        raw = '{"observations": ['
        for i in range(5):
            raw += f'{{"content": "User observation number {i} and this is a longer string to pass length check", "confidence": 0.8, "source_memory_ids": ["mem-{i}"]}},'
        raw = raw.rstrip(",") + "]}"
        result = _normalize_observations(raw, valid_ids)
        assert len(result) <= 2  # MAX_DREAM_OBSERVATIONS_PER_FAMILY = 2

    def test_handles_json_wrapped_in_markdown(self) -> None:
        from orchestrator.memory.dreaming import _normalize_observations

        valid_ids = {"mem-1"}
        raw = '```json\n{"observations": [{"content": "User bikes to work daily", "confidence": 0.9, "source_memory_ids": ["mem-1"]}]}\n```'
        result = _normalize_observations(raw, valid_ids)
        assert len(result) == 1


class TestNormalizeUserObservation:
    """Tests for _normalize_user_observation helper."""

    def test_passes_through_when_already_user_prefix(self) -> None:
        from orchestrator.memory.dreaming import _normalize_user_observation

        result = _normalize_user_observation("User bikes to work daily")
        assert result == "User bikes to work daily"

    def test_normalizes_the_user_prefix(self) -> None:
        from orchestrator.memory.dreaming import _normalize_user_observation

        result = _normalize_user_observation("the user bikes to work")
        assert result == "User bikes to work"

    def test_normalizes_lowercase_start(self) -> None:
        from orchestrator.memory.dreaming import _normalize_user_observation

        result = _normalize_user_observation("bikes to work daily")
        assert result == "User bikes to work daily"

    def test_preserves_whitespace_normalization(self) -> None:
        from orchestrator.memory.dreaming import _normalize_user_observation

        result = _normalize_user_observation("  User   bikes   to   work  ")
        assert result == "User bikes to work"

    def test_returns_empty_for_blank_input(self) -> None:
        from orchestrator.memory.dreaming import _normalize_user_observation

        result = _normalize_user_observation("   ")
        assert result == ""


class TestClampConfidence:
    """Tests for _clamp_confidence helper."""

    def test_passes_through_valid_float(self) -> None:
        from orchestrator.memory.dreaming import _clamp_confidence

        assert _clamp_confidence(0.75) == 0.75

    def test_passes_through_valid_int(self) -> None:
        from orchestrator.memory.dreaming import _clamp_confidence

        assert _clamp_confidence(1) == 1.0

    def test_passes_through_string_number(self) -> None:
        from orchestrator.memory.dreaming import _clamp_confidence

        assert _clamp_confidence("0.85") == 0.85

    def test_clamps_negative_to_zero(self) -> None:
        from orchestrator.memory.dreaming import _clamp_confidence

        assert _clamp_confidence(-0.5) == 0.0

    def test_clamps_over_one_to_one(self) -> None:
        from orchestrator.memory.dreaming import _clamp_confidence

        assert _clamp_confidence(1.5) == 1.0

    def test_returns_none_for_non_numeric_string(self) -> None:
        from orchestrator.memory.dreaming import _clamp_confidence

        assert _clamp_confidence("high") is None

    def test_returns_none_for_bool(self) -> None:
        from orchestrator.memory.dreaming import _clamp_confidence

        assert _clamp_confidence(True) is None


class TestUserMatchesDreamScheduleHour:
    """Tests for _user_matches_dream_schedule_hour scheduler gating."""

    @pytest.mark.asyncio
    async def test_returns_true_when_user_timezone_missing(self) -> None:
        from orchestrator.worker.jobs import _user_matches_dream_schedule_hour

        store = AsyncMock()
        user_id = uuid.uuid4()
        store.get_user_settings.return_value = {}  # No timezone

        # With no timezone, should fall back to server behavior (UTC)
        should_run = await _user_matches_dream_schedule_hour(
            store,
            user_id,
            3,
            now_utc=datetime(2026, 4, 10, 3, 30, tzinfo=timezone.utc),
        )
        # Server hour=3, UTC time=3:30, user has no timezone -> server schedule applies
        assert should_run is True

    @pytest.mark.asyncio
    async def test_returns_false_when_local_hour_mismatch(self) -> None:
        from orchestrator.worker.jobs import _user_matches_dream_schedule_hour

        store = AsyncMock()
        user_id = uuid.uuid4()
        # User is in UTC-5 (New York), it's 3:30 UTC, so local is 22:30 previous day
        store.get_user_settings.return_value = {"preferences": {"timezone": "America/New_York"}}

        should_run = await _user_matches_dream_schedule_hour(
            store,
            user_id,
            3,  # Schedule hour 3
            now_utc=datetime(2026, 4, 10, 3, 30, tzinfo=timezone.utc),
        )
        # Local hour is ~22 (UTC-5), schedule is 3 -> not a match
        assert should_run is False

    @pytest.mark.asyncio
    async def test_returns_true_when_local_hour_matches_schedule(self) -> None:
        from orchestrator.worker.jobs import _user_matches_dream_schedule_hour

        store = AsyncMock()
        user_id = uuid.uuid4()
        # User is in UTC+10 (Brisbane), it's 03:30 UTC, so local is 13:30
        store.get_user_settings.return_value = {"preferences": {"timezone": "Australia/Brisbane"}}

        should_run = await _user_matches_dream_schedule_hour(
            store,
            user_id,
            13,  # Schedule hour 13
            now_utc=datetime(2026, 4, 10, 3, 30, tzinfo=timezone.utc),
        )
        # Local hour is 13 (UTC+10), schedule is 13 -> match
        assert should_run is True

    @pytest.mark.asyncio
    async def test_handles_invalid_timezone_gracefully(self) -> None:
        from orchestrator.worker.jobs import _user_matches_dream_schedule_hour

        store = AsyncMock()
        user_id = uuid.uuid4()
        store.get_user_settings.return_value = {"preferences": {"timezone": "Invalid/Timezone"}}

        # Should not raise, should fall back to server schedule (True when UTC hour matches)
        should_run = await _user_matches_dream_schedule_hour(
            store,
            user_id,
            3,
            now_utc=datetime(2026, 4, 10, 3, 30, tzinfo=timezone.utc),
        )
        # Falls back to server schedule - UTC hour 3 matches schedule 3
        assert should_run is True
