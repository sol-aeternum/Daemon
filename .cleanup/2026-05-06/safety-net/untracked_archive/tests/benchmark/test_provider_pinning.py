"""Tests for benchmark-mode provider pinning and fail-fast behavior.

Validates Task 11: provider routing is pinned and transport/provider exceptions
surface as benchmark failures rather than silent fallback.
"""

import pytest
from unittest.mock import AsyncMock
from typing import Any

from tests.longmemeval.evaluate import (
    BenchmarkProviderError,
    BenchmarkSamplingError,
    _call_llm_with_provider_config,
    reset_benchmark_tracking,
)


# =============================================================================
# Evaluate.py — provider fail-fast tests
# =============================================================================


class TestEvaluateProviderFailFast:
    """Verify _call_llm_with_provider_config fails loudly in benchmark mode on provider errors."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        reset_benchmark_tracking()

    @pytest.mark.asyncio
    async def test_benchmark_mode_raises_on_provider_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Transport/provider exception in benchmark mode raises BenchmarkProviderError."""
        import litellm

        litellm_mock = AsyncMock(
            side_effect=Exception("rate limit exceeded")
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        with pytest.raises(BenchmarkProviderError) as exc_info:
            await _call_llm_with_provider_config(
                model="openrouter/openai/gpt-4o",
                messages=[{"role": "user", "content": "hello"}],
                temperature=0.0,
                max_tokens=10,
                bm_call_key="answer",
            )
        assert "rate limit exceeded" in str(exc_info.value)
        assert "Benchmark-mode" in str(exc_info.value)
        assert "answer" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_benchmark_mode_raises_on_network_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Network error in benchmark mode raises BenchmarkProviderError with diagnostic."""
        import litellm

        litellm_mock = AsyncMock(
            side_effect=Exception("Connection timeout")
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        with pytest.raises(BenchmarkProviderError) as exc_info:
            await _call_llm_with_provider_config(
                model="openrouter/openai/gpt-4o",
                messages=[{"role": "user", "content": "hello"}],
                temperature=0.0,
                max_tokens=10,
                bm_call_key="judge",
            )
        assert "Connection timeout" in str(exc_info.value)
        assert "judge" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_non_benchmark_mode_returns_none_on_provider_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-benchmark mode returns None on provider error (silent fallback preserved)."""
        import litellm

        litellm_mock = AsyncMock(
            side_effect=Exception("rate limit exceeded")
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        result = await _call_llm_with_provider_config(
            model="openrouter/openai/gpt-4o",
            messages=[{"role": "user", "content": "hello"}],
            temperature=0.0,
            max_tokens=10,
            bm_call_key=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_benchmark_mode_preserves_fingerprint_check_before_fail_fast(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fingerprint drift is checked before provider-error fail-fast is evaluated."""
        import litellm

        call_count = 0

        async def fake_completion(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MockResponseWithMetadata(
                    content="hello",
                    model="openrouter/openai/gpt-4o",
                    system_fingerprint="fp_abc",
                )
            else:
                raise Exception("provider outage on second call")

        litellm_mock = AsyncMock(side_effect=fake_completion)
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        await _call_llm_with_provider_config(
            model="openrouter/openai/gpt-4o",
            messages=[{"role": "user", "content": "hello"}],
            temperature=0.0,
            max_tokens=10,
            bm_call_key="answer",
        )

        with pytest.raises(BenchmarkProviderError) as exc_info:
            await _call_llm_with_provider_config(
                model="openrouter/openai/gpt-4o",
                messages=[{"role": "user", "content": "world"}],
                temperature=0.0,
                max_tokens=10,
                bm_call_key="answer",
            )
        assert "provider outage on second call" in str(exc_info.value)


class MockResponseWithMetadata:
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

    def dict(self) -> dict[str, Any]:
        return self.model_dump()


class MockResponse:
    """Mock LLM response without metadata."""

    def __init__(self, content: str) -> None:
        self._content = content

    def model_dump(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": self._content}}]}

    def dict(self) -> dict[str, Any]:
        return self.model_dump()


# =============================================================================
# Extraction — provider fail-fast tests
# =============================================================================


class TestExtractionProviderFailFast:
    """Verify extraction fails loudly in benchmark mode on provider errors."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        from orchestrator.memory.extraction import reset_benchmark_tracking
        reset_benchmark_tracking()

    @pytest.mark.asyncio
    async def test_extraction_benchmark_raises_on_provider_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Transport/provider exception in benchmark mode raises BenchmarkProviderError."""
        from orchestrator.memory.extraction import (
            extract_facts_from_text,
            BenchmarkProviderError,
        )

        litellm_mock = AsyncMock(
            side_effect=Exception("credit limit exceeded")
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        with pytest.raises(BenchmarkProviderError) as exc_info:
            await extract_facts_from_text(
                "I love Python",
                benchmark_mode=True,
            )
        assert "credit limit exceeded" in str(exc_info.value)
        assert "Benchmark-mode" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_extraction_non_benchmark_returns_empty_on_provider_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-benchmark mode returns empty ExtractionOutcome on provider error."""
        from orchestrator.memory.extraction import (
            extract_facts_from_text,
            ExtractionOutcome,
        )

        litellm_mock = AsyncMock(
            side_effect=Exception("connection reset")
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        result = await extract_facts_from_text(
            "I love Python",
            benchmark_mode=False,
        )
        assert isinstance(result, ExtractionOutcome)
        assert result.facts == []
        assert result.raw_count == 0


# =============================================================================
# Dedup contradiction — provider fail-fast tests
# =============================================================================


class TestDedupProviderFailFast:
    """Verify dedup contradiction check fails loudly in benchmark mode on provider errors."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        from orchestrator.memory.dedup import reset_dedup_benchmark_tracking
        reset_dedup_benchmark_tracking()

    @pytest.mark.asyncio
    async def test_contradiction_benchmark_raises_on_provider_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Transport/provider exception in benchmark mode raises DedupBenchmarkProviderError."""
        from orchestrator.memory.dedup import (
            check_contradiction,
            DedupBenchmarkProviderError,
        )

        litellm_mock = AsyncMock(
            side_effect=Exception("rate limit")
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        with pytest.raises(DedupBenchmarkProviderError) as exc_info:
            await check_contradiction(
                "User likes Python",
                "User loves Python",
                benchmark_mode=True,
            )
        assert "rate limit" in str(exc_info.value)
        assert "Benchmark-mode" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_contradiction_non_benchmark_returns_false_on_provider_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-benchmark mode returns (False, '') on provider error (silent fallback preserved)."""
        from orchestrator.memory.dedup import check_contradiction

        litellm_mock = AsyncMock(
            side_effect=Exception("network unreachable")
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        result = await check_contradiction(
            "User likes Python",
            "User loves Python",
            benchmark_mode=False,
        )
        assert result == (False, "")


# =============================================================================
# Non-benchmark mode retains existing behavior
# =============================================================================


class TestNonBenchmarkModeUnchanged:
    """Verify non-benchmark mode retains current routing/fallback behavior."""

    @pytest.mark.asyncio
    async def test_judge_non_benchmark_returns_incorrect_on_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-benchmark judge returns 'incorrect' when LLM call returns None."""
        from tests.longmemeval.evaluate import judge_answer

        async def fake_llm(**kwargs: Any) -> Any:
            return None

        monkeypatch.setattr(
            "tests.longmemeval.evaluate._call_llm_with_provider_config",
            fake_llm,
        )

        result = await judge_answer(
            question_text="What is 2+2?",
            hypothesis="4",
            reference="4",
            benchmark_mode=False,
        )
        assert result == "incorrect"

    @pytest.mark.asyncio
    async def test_answer_non_benchmark_returns_empty_on_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-benchmark answer returns empty string when LLM call returns None."""
        from tests.longmemeval.evaluate import answer_with_llm

        async def fake_llm(**kwargs: Any) -> Any:
            return None

        monkeypatch.setattr(
            "tests.longmemeval.evaluate._call_llm_with_provider_config",
            fake_llm,
        )

        result = await answer_with_llm(
            question="What is 2+2?",
            memories=[{"content": "2+2 equals 4"}],
            benchmark_mode=False,
        )
        assert result == ""


# =============================================================================
# extra_body provider pinning contract tests
# =============================================================================


class TestEvaluateExtraBodyContract:
    """Verify _call_llm_with_provider_config sends extra_body in benchmark mode."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        reset_benchmark_tracking()

    @pytest.mark.asyncio
    async def test_benchmark_answer_includes_extra_body_with_provider_order(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Benchmark-mode answer call includes extra_body.provider.order with provider slug."""
        litellm_mock = AsyncMock(
            return_value=MockResponseWithMetadata(
                content="4",
                model="openrouter/openai/gpt-4o-2024-08-06",
                system_fingerprint="fp_answer",
            )
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        await _call_llm_with_provider_config(
            model="openrouter/openai/gpt-4o",
            messages=[{"role": "user", "content": "What is 2+2?"}],
            temperature=0.0,
            max_tokens=10,
            bm_call_key="answer",
        )

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert "extra_body" in litellm_kwargs
        assert litellm_kwargs["extra_body"]["provider"]["order"] == ["openai"]
        assert litellm_kwargs["extra_body"]["provider"]["allow_fallbacks"] is False

    @pytest.mark.asyncio
    async def test_benchmark_judge_includes_extra_body_with_provider_order(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Benchmark-mode judge call includes extra_body.provider.order with provider slug."""
        litellm_mock = AsyncMock(
            return_value=MockResponseWithMetadata(
                content="CORRECT",
                model="openrouter/openai/gpt-4o-2024-08-06",
                system_fingerprint="fp_judge",
            )
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        await _call_llm_with_provider_config(
            model="openrouter/openai/gpt-4o",
            messages=[{"role": "user", "content": "Is 2+2=4?"}],
            temperature=0.0,
            max_tokens=10,
            bm_call_key="judge",
        )

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert "extra_body" in litellm_kwargs
        assert litellm_kwargs["extra_body"]["provider"]["order"] == ["openai"]
        assert litellm_kwargs["extra_body"]["provider"]["allow_fallbacks"] is False

    @pytest.mark.asyncio
    async def test_non_benchmark_excludes_extra_body(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-benchmark mode does not include extra_body."""
        litellm_mock = AsyncMock(
            return_value=MockResponseWithMetadata(
                content="4",
                model="openrouter/openai/gpt-4o",
                system_fingerprint=None,
            )
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        await _call_llm_with_provider_config(
            model="openrouter/openai/gpt-4o",
            messages=[{"role": "user", "content": "What is 2+2?"}],
            temperature=0.7,
            max_tokens=10,
            bm_call_key=None,
        )

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert "extra_body" not in litellm_kwargs

    @pytest.mark.asyncio
    async def test_benchmark_uses_dated_snapshot_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Benchmark-mode uses dated snapshot model ID, not alias."""
        litellm_mock = AsyncMock(
            return_value=MockResponseWithMetadata(
                content="4",
                model="openrouter/openai/gpt-4o-2024-08-06",
                system_fingerprint="fp_answer",
            )
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        await _call_llm_with_provider_config(
            model="openrouter/openai/gpt-4o",
            messages=[{"role": "user", "content": "What is 2+2?"}],
            temperature=0.0,
            max_tokens=10,
            bm_call_key="answer",
        )

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert litellm_kwargs["model"] == "openrouter/openai/gpt-4o-2024-08-06"


class TestExtractionExtraBodyContract:
    """Verify extraction sends extra_body in benchmark mode."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        from orchestrator.memory.extraction import reset_benchmark_tracking
        reset_benchmark_tracking()

    @pytest.mark.asyncio
    async def test_extraction_benchmark_includes_extra_body(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Extraction benchmark mode includes extra_body.provider.order."""
        litellm_mock = AsyncMock(
            return_value=MockResponseWithMetadata(
                content='{"facts": []}',
                model="openrouter/openai/gpt-4o-mini-2024-07-18",
                system_fingerprint="fp_extraction",
            )
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        from orchestrator.memory.extraction import extract_facts_from_text
        await extract_facts_from_text(
            "I love Python",
            benchmark_mode=True,
        )

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert "extra_body" in litellm_kwargs
        assert litellm_kwargs["extra_body"]["provider"]["order"] == [
            "openrouter/openai/gpt-4o-mini-2024-07-18"
        ]
        assert litellm_kwargs["extra_body"]["provider"]["allow_fallbacks"] is False

    @pytest.mark.asyncio
    async def test_extraction_non_benchmark_excludes_extra_body(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Extraction non-benchmark mode does not include extra_body."""
        litellm_mock = AsyncMock(
            return_value=MockResponse('{"facts": []}')
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        from orchestrator.memory.extraction import extract_facts_from_text
        await extract_facts_from_text(
            "I love Python",
            benchmark_mode=False,
        )

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert "extra_body" not in litellm_kwargs

    @pytest.mark.asyncio
    async def test_extraction_uses_dated_snapshot_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Extraction benchmark mode uses dated snapshot model ID."""
        litellm_mock = AsyncMock(
            return_value=MockResponseWithMetadata(
                content='{"facts": []}',
                model="openrouter/openai/gpt-4o-mini-2024-07-18",
                system_fingerprint="fp_extraction",
            )
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        from orchestrator.memory.extraction import extract_facts_from_text
        await extract_facts_from_text(
            "I love Python",
            benchmark_mode=True,
        )

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert litellm_kwargs["model"] == "openrouter/openai/gpt-4o-mini-2024-07-18"


class TestDedupExtraBodyContract:
    """Verify dedup contradiction check sends extra_body in benchmark mode."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        from orchestrator.memory.dedup import reset_dedup_benchmark_tracking
        reset_dedup_benchmark_tracking()

    @pytest.mark.asyncio
    async def test_contradiction_benchmark_includes_extra_body(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Dedup contradiction benchmark mode includes extra_body.provider.order."""
        litellm_mock = AsyncMock(
            return_value=MockResponseWithMetadata(
                content="NO",
                model="openrouter/deepseek/deepseek-chat-v3-5",
                system_fingerprint="fp_dedup",
            )
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        from orchestrator.memory.dedup import check_contradiction
        await check_contradiction(
            "User likes Python",
            "User loves Python",
            benchmark_mode=True,
        )

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert "extra_body" in litellm_kwargs
        assert litellm_kwargs["extra_body"]["provider"]["order"] == [
            "openrouter/deepseek/deepseek-chat-v3-5"
        ]
        assert litellm_kwargs["extra_body"]["provider"]["allow_fallbacks"] is False

    @pytest.mark.asyncio
    async def test_contradiction_non_benchmark_excludes_extra_body(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Dedup contradiction non-benchmark mode does not include extra_body."""
        litellm_mock = AsyncMock(
            return_value=MockResponse("NO")
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        from orchestrator.memory.dedup import check_contradiction
        await check_contradiction(
            "User likes Python",
            "User loves Python",
            benchmark_mode=False,
        )

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert "extra_body" not in litellm_kwargs

    @pytest.mark.asyncio
    async def test_contradiction_uses_benchmark_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Dedup contradiction benchmark mode uses benchmark model ID."""
        litellm_mock = AsyncMock(
            return_value=MockResponseWithMetadata(
                content="NO",
                model="openrouter/deepseek/deepseek-chat-v3-5",
                system_fingerprint="fp_dedup",
            )
        )
        monkeypatch.setattr("litellm.acompletion", litellm_mock)

        from orchestrator.memory.dedup import check_contradiction
        await check_contradiction(
            "User likes Python",
            "User loves Python",
            benchmark_mode=True,
        )

        litellm_kwargs = litellm_mock.call_args_list[0].kwargs
        assert litellm_kwargs["model"] == "openrouter/deepseek/deepseek-chat-v3-5"
