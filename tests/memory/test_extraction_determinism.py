"""Tests for extraction and dedup benchmark-mode deterministic sampling."""

import pytest
from unittest.mock import AsyncMock
from typing import Any

from orchestrator.memory.extraction import (
    extract_facts_from_text,
    BenchmarkSamplingError,
    reset_benchmark_tracking,
    get_benchmark_tracking,
    BENCHMARK_SEED,
)
from orchestrator.memory.dedup import (
    check_contradiction,
    DedupBenchmarkSamplingError,
    reset_dedup_benchmark_tracking,
    get_dedup_benchmark_tracking,
    DEDUP_BENCHMARK_SEED,
    CONTRADICTION_TEMPERATURE,
)


class MockResponseWithMetadata:
    """Mock LLM response with system_fingerprint and model fields."""

    def __init__(
        self,
        content: str,
        model: str,
        system_fingerprint: str | None,
    ) -> None:
        self._content = content
        self._model = model
        self._fingerprint = system_fingerprint

    def model_dump(self) -> dict[str, Any]:
        return {
            "choices": [{"message": {"content": self._content}}],
            "model": self._model,
            "system_fingerprint": self._fingerprint,
        }


class MockResponse:
    """Mock LLM response without metadata."""

    def __init__(self, content: str) -> None:
        self._content = content

    def model_dump(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": self._content}}]}


# =============================================================================
# Extraction Benchmark Mode Tests
# =============================================================================


class TestExtractionBenchmarkSampling:
    """Verify extraction sends deterministic sampling params in benchmark mode."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        reset_benchmark_tracking()

    @pytest.mark.asyncio
    async def test_extraction_benchmark_passes_temperature_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Extraction passes temperature=0.0 in benchmark mode."""
        litellm_mock = AsyncMock(return_value=MockResponse('{"facts": []}'))
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        await extract_facts_from_text(
            "I love Python",
            benchmark_mode=True,
        )

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert litellm_kwargs["temperature"] == 0.0

    @pytest.mark.asyncio
    async def test_extraction_benchmark_passes_fixed_seed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Extraction passes seed=BENCHMARK_SEED in benchmark mode."""
        litellm_mock = AsyncMock(return_value=MockResponse('{"facts": []}'))
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        await extract_facts_from_text(
            "I love Python",
            benchmark_mode=True,
        )

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert litellm_kwargs["seed"] == BENCHMARK_SEED

    @pytest.mark.asyncio
    async def test_extraction_benchmark_captures_fingerprint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Extraction benchmark mode captures system_fingerprint and model from response."""
        litellm_mock = AsyncMock(
            return_value=MockResponseWithMetadata(
                content='{"facts": []}',
                model="openrouter/openai/gpt-4o-mini",
                system_fingerprint="fp_extraction_abc",
            )
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        await extract_facts_from_text(
            "I love Python",
            benchmark_mode=True,
        )

        tracking = get_benchmark_tracking()
        assert "extraction" in tracking
        assert tracking["extraction"]["fingerprint"] == "fp_extraction_abc"
        assert tracking["extraction"]["model"] == "openrouter/openai/gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_extraction_benchmark_fingerprint_drift_aborts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Second extraction call with different fingerprint raises BenchmarkSamplingError."""
        call_count = 0

        async def fake_completion(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MockResponseWithMetadata(
                    content='{"facts": []}',
                    model="openrouter/openai/gpt-4o-mini",
                    system_fingerprint="fp_first",
                )
            else:
                return MockResponseWithMetadata(
                    content='{"facts": []}',
                    model="openrouter/openai/gpt-4o-mini",
                    system_fingerprint="fp_second",
                )

        litellm_mock = AsyncMock(side_effect=fake_completion)
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        await extract_facts_from_text(
            "I love Python",
            benchmark_mode=True,
        )

        with pytest.raises(BenchmarkSamplingError) as exc_info:
            await extract_facts_from_text(
                "I also like JavaScript",
                benchmark_mode=True,
            )
        assert "fingerprint drift" in str(exc_info.value)
        assert "fp_first" in str(exc_info.value)
        assert "fp_second" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_extraction_non_benchmark_no_seed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-benchmark extraction does not inject seed."""
        litellm_mock = AsyncMock(return_value=MockResponse('{"facts": []}'))
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        await extract_facts_from_text(
            "I love Python",
            benchmark_mode=False,
        )

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert "seed" not in litellm_kwargs

    @pytest.mark.asyncio
    async def test_extraction_non_benchmark_no_tracking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-benchmark extraction does not track fingerprint metadata."""
        litellm_mock = AsyncMock(
            return_value=MockResponseWithMetadata(
                content='{"facts": []}',
                model="openrouter/openai/gpt-4o-mini",
                system_fingerprint="fp_xyz",
            )
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        await extract_facts_from_text(
            "I love Python",
            benchmark_mode=False,
        )

        tracking = get_benchmark_tracking()
        assert "extraction" not in tracking


# =============================================================================
# Dedup Contradiction Benchmark Mode Tests
# =============================================================================


class TestDedupContradictionBenchmarkSampling:
    """Verify dedup contradiction check sends deterministic sampling params in benchmark mode."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        reset_dedup_benchmark_tracking()

    @pytest.mark.asyncio
    async def test_contradiction_benchmark_temperature_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dedup contradiction uses temperature=0.0 in benchmark mode (overriding CONTRADICTION_TEMPERATURE)."""
        litellm_mock = AsyncMock(return_value=MockResponse("NO"))
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        await check_contradiction(
            "User likes Python",
            "User loves Python",
            benchmark_mode=True,
        )

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert litellm_kwargs["temperature"] == 0.0

    @pytest.mark.asyncio
    async def test_contradiction_non_benchmark_uses_low_temp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dedup contradiction uses CONTRADICTION_TEMPERATURE=0.1 when not in benchmark mode."""
        litellm_mock = AsyncMock(return_value=MockResponse("NO"))
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        await check_contradiction(
            "User likes Python",
            "User loves Python",
            benchmark_mode=False,
        )

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert litellm_kwargs["temperature"] == CONTRADICTION_TEMPERATURE

    @pytest.mark.asyncio
    async def test_contradiction_benchmark_passes_fixed_seed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dedup contradiction passes seed=DEDUP_BENCHMARK_SEED in benchmark mode."""
        litellm_mock = AsyncMock(return_value=MockResponse("NO"))
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        await check_contradiction(
            "User likes Python",
            "User loves Python",
            benchmark_mode=True,
        )

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert litellm_kwargs["seed"] == DEDUP_BENCHMARK_SEED

    @pytest.mark.asyncio
    async def test_contradiction_benchmark_captures_fingerprint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dedup contradiction benchmark mode captures system_fingerprint and model."""
        litellm_mock = AsyncMock(
            return_value=MockResponseWithMetadata(
                content="NO",
                model="openrouter/deepseek/deepseek-chat",
                system_fingerprint="fp_dedup_xyz",
            )
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        await check_contradiction(
            "User likes Python",
            "User loves Python",
            benchmark_mode=True,
        )

        tracking = get_dedup_benchmark_tracking()
        assert "contradiction" in tracking
        assert tracking["contradiction"]["fingerprint"] == "fp_dedup_xyz"
        assert tracking["contradiction"]["model"] == "openrouter/deepseek/deepseek-chat"

    @pytest.mark.asyncio
    async def test_contradiction_benchmark_fingerprint_drift_aborts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Second contradiction call with different fingerprint raises DedupBenchmarkSamplingError."""
        call_count = 0

        async def fake_completion(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MockResponseWithMetadata(
                    content="NO",
                    model="openrouter/deepseek/deepseek-chat",
                    system_fingerprint="fp_dedup_first",
                )
            else:
                return MockResponseWithMetadata(
                    content="NO",
                    model="openrouter/deepseek/deepseek-chat",
                    system_fingerprint="fp_dedup_second",
                )

        litellm_mock = AsyncMock(side_effect=fake_completion)
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        await check_contradiction(
            "User likes Python",
            "User loves Python",
            benchmark_mode=True,
        )

        with pytest.raises(DedupBenchmarkSamplingError) as exc_info:
            await check_contradiction(
                "User lives in Adelaide",
                "User lives in Sydney",
                benchmark_mode=True,
            )
        assert "fingerprint drift" in str(exc_info.value)
        assert "fp_dedup_first" in str(exc_info.value)
        assert "fp_dedup_second" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_contradiction_non_benchmark_no_tracking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-benchmark contradiction does not track fingerprint metadata."""
        litellm_mock = AsyncMock(
            return_value=MockResponseWithMetadata(
                content="NO",
                model="openrouter/deepseek/deepseek-chat",
                system_fingerprint="fp_dedup_abc",
            )
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        await check_contradiction(
            "User likes Python",
            "User loves Python",
            benchmark_mode=False,
        )

        tracking = get_dedup_benchmark_tracking()
        assert "contradiction" not in tracking


# =============================================================================
# Benchmark Mode Activation — Env Var Default Tests
# =============================================================================


class TestExtractionBenchmarkModeActivation:
    """Prove benchmark-mode locking activates via BENCHMARK_MODE env var default."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        reset_benchmark_tracking()

    @pytest.mark.asyncio
    async def test_extract_facts_activates_benchmark_via_env_var_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When BENCHMARK_MODE env var is True, extract_facts_from_text uses benchmark mode by default."""
        import orchestrator.memory.extraction as extraction_module

        litellm_mock = AsyncMock(
            return_value=MockResponseWithMetadata(
                content='{"facts": []}',
                model="openrouter/openai/gpt-4o-mini",
                system_fingerprint="fp_activation_abc",
            )
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)
        monkeypatch.setattr(
            extraction_module,
            "BENCHMARK_MODE",
            True,
        )

        await extract_facts_from_text("I love Python")

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert litellm_kwargs["seed"] == BENCHMARK_SEED
        assert litellm_kwargs["temperature"] == 0.0

        tracking = get_benchmark_tracking()
        assert "extraction" in tracking
        assert tracking["extraction"]["fingerprint"] == "fp_activation_abc"

    @pytest.mark.asyncio
    async def test_extract_facts_no_benchmark_by_default_when_env_var_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When BENCHMARK_MODE env var is False, extract_facts_from_text does not use benchmark mode."""
        import orchestrator.memory.extraction as extraction_module

        litellm_mock = AsyncMock(return_value=MockResponse('{"facts": []}'))
        monkeypatch.setattr("litellm.acompletion", litellm_mock)
        monkeypatch.setattr(
            extraction_module,
            "BENCHMARK_MODE",
            False,
        )

        await extract_facts_from_text("I love Python")

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert "seed" not in litellm_kwargs

        tracking = get_benchmark_tracking()
        assert "extraction" not in tracking


class TestDedupBenchmarkModeActivation:
    """Prove dedup benchmark-mode locking activates via DEDUP_BENCHMARK_MODE env var default."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        reset_dedup_benchmark_tracking()

    @pytest.mark.asyncio
    async def test_check_contradiction_activates_benchmark_via_env_var_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When DEDUP_BENCHMARK_MODE is True, check_contradiction uses benchmark mode by default."""
        import orchestrator.memory.dedup as dedup_module

        litellm_mock = AsyncMock(
            return_value=MockResponseWithMetadata(
                content="NO",
                model="openrouter/deepseek/deepseek-chat",
                system_fingerprint="fp_dedup_activation_xyz",
            )
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)
        monkeypatch.setattr(
            dedup_module,
            "DEDUP_BENCHMARK_MODE",
            True,
        )

        await check_contradiction(
            "User likes Python",
            "User loves Python",
        )

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert litellm_kwargs["seed"] == DEDUP_BENCHMARK_SEED
        assert litellm_kwargs["temperature"] == 0.0

        tracking = get_dedup_benchmark_tracking()
        assert "contradiction" in tracking
        assert tracking["contradiction"]["fingerprint"] == "fp_dedup_activation_xyz"

    @pytest.mark.asyncio
    async def test_check_contradiction_no_benchmark_by_default_when_env_var_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When DEDUP_BENCHMARK_MODE is False, check_contradiction does not use benchmark mode."""
        import orchestrator.memory.dedup as dedup_module

        litellm_mock = AsyncMock(return_value=MockResponse("NO"))
        monkeypatch.setattr("litellm.acompletion", litellm_mock)
        monkeypatch.setattr(
            dedup_module,
            "DEDUP_BENCHMARK_MODE",
            False,
        )

        await check_contradiction(
            "User likes Python",
            "User loves Python",
        )

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert "seed" not in litellm_kwargs
        assert litellm_kwargs["temperature"] == CONTRADICTION_TEMPERATURE

        tracking = get_dedup_benchmark_tracking()
        assert "contradiction" not in tracking
