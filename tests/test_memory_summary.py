"""Inline summary generation under J1c original-generation fencing.

Legacy baseline/continuation regressions (PR #165) are preserved on the new
consolidated guarded input read: missing captures disable summary work, the
guard recheck gates inference, holes retain the pending flag, and stale/conflict
surfaces are unchanged. Store seams are stubbed; the real store transaction is
proven in the PostgreSQL suite.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from orchestrator.memory import summary as summary_module
from orchestrator.memory.store import MemoryStore
from orchestrator.services.account_control.guard import CapturedGeneration, WriteGuardRefusal


def _build_provider() -> SimpleNamespace:
    return SimpleNamespace(
        timeout_s=10,
        base_url=None,
        api_key=None,
        extra_headers=None,
    )


def _build_settings(provider: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        auto_fast_model="openrouter/test-model",
        get_provider_config=lambda _: provider,
    )


def _patch_guarded_completion(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> AsyncMock:
    completion = AsyncMock(
        return_value=SimpleNamespace(choices=[{"message": {"content": content}}])
    )
    monkeypatch.setattr(summary_module, "guarded_completion", completion)
    return completion


def _captured(owner: uuid.UUID | None = None) -> CapturedGeneration:
    return CapturedGeneration(owner or uuid4(), 7)


def _conversation_input(
    summary: str | None,
    *,
    messages: list[dict[str, str]] | None = None,
    validated_baseline: int = 0,
    current_count: int = 0,
    finalized_count: int | None = None,
    contiguous_count: int | None = None,
    has_hole: bool = False,
    summary_updated_at: object | None = None,
) -> dict[str, object]:
    """Build a consolidated ``read_conversation_summary_inputs`` payload.

    Comparison kwargs must reference the identical object, so the returned
    snapshot is filled in post-hoc when ``summary_updated_at`` is None.
    """
    payload: dict[str, object] = {
        "summary": summary,
        "summary_updated_at": summary_updated_at,
        "persisted_baseline": validated_baseline,
        "current_message_count": current_count,
        "finalized_count": finalized_count if finalized_count is not None else current_count,
        "contiguous_count": contiguous_count if contiguous_count is not None else current_count,
        "has_hole": has_hole,
        "messages": messages if messages is not None else [],
        "snapshot_at": SimpleNamespace(),
    }
    return payload


@pytest.mark.asyncio
async def test_missing_capture_disables_summary_without_inputs_or_compute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``captured_generation`` omitted means the inline path is a summary-only
    skip: no guarded input read, no provider call, no store writes."""
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    read_inputs = AsyncMock(return_value=_conversation_input("prior", validated_baseline=3))
    store.read_conversation_summary_inputs = read_inputs
    store.update_conversation_summary = AsyncMock()
    store.clear_finished_summary_continuation = AsyncMock()
    completion = _patch_guarded_completion(monkeypatch, "unused")

    result = await summary_module._generate_or_update_summary_result(conversation_id, store)

    assert result == summary_module.SummaryUpdateResult(summary=None, continuation_needed=False)
    read_inputs.assert_not_awaited()
    store.update_conversation_summary.assert_not_awaited()
    store.clear_finished_summary_continuation.assert_not_awaited()
    completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_extraction_summary_advances_same_persisted_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    capture = CapturedGeneration(uuid4(), 7)
    inputs = _conversation_input(
        "existing",
        messages=[
            {"role": "user", "content": "new question"},
            {"role": "assistant", "content": "new answer"},
        ],
        validated_baseline=3,
        current_count=5,
        finalized_count=5,
        contiguous_count=3,
    )
    store.read_conversation_summary_inputs = AsyncMock(return_value=inputs)
    store.check_conversation_summary_generation = AsyncMock(return_value=True)
    store.update_conversation_summary = AsyncMock(return_value=True)

    provider = _build_provider()
    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(provider))
    _patch_guarded_completion(monkeypatch, "Updated summary.")

    result = await summary_module._generate_or_update_summary_result(
        conversation_id, store, captured_generation=capture
    )

    assert result.summary == "Updated summary."
    read = store.read_conversation_summary_inputs.await_args
    assert read is not None
    assert read.kwargs["captured_generation"] == capture
    assert read.kwargs["limit"] == summary_module.INLINE_SUMMARY_BATCH_LIMIT
    # Pre-persist guard recheck precedes inference.
    check = store.check_conversation_summary_generation.await_args
    assert check is not None
    assert check.kwargs["captured_generation"] == capture
    assert check.kwargs["expected_summary_updated_at"] is None
    update_args = store.update_conversation_summary.await_args
    assert update_args is not None
    update_kwargs = update_args.kwargs
    assert update_kwargs["summary"] == "Updated summary."
    assert update_kwargs["expected_summary_updated_at"] is None
    # The persisted claim is capped at the contiguous-prefix boundary
    # (``min(persisted_baseline + len(messages), contiguous_baseline)``
    # = ``min(3 + 2, 3) = 3``) so rows beyond the contiguous finalized
    # prefix at the snapshot do not push the cursor past the prefix
    # (Codex P2 on PR #165, ``summary.py:147``).
    assert update_kwargs["summarized_message_count"] == 3
    assert update_kwargs["captured_generation"] == capture
    # ``finalized_count`` (5) exceeds the capped claim (3), so the guarded
    # persist flags the tail as pending for a forced follow-up.
    assert update_kwargs["summary_continuation_pending"] is True
    assert "summary_snapshot_at" in update_kwargs


@pytest.mark.asyncio
async def test_inline_summary_signals_continuation_when_batch_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the inline batch fills, the result must signal continuation."""
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_conversation_input(
            "",
            messages=[{"role": "user", "content": f"msg {i}"} for i in range(20)],
            current_count=100,
            finalized_count=100,
            contiguous_count=100,
        )
    )
    store.check_conversation_summary_generation = AsyncMock(return_value=True)
    store.update_conversation_summary = AsyncMock(return_value=True)

    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    _patch_guarded_completion(monkeypatch, "Updated summary.")

    result = await summary_module._generate_or_update_summary_result(
        conversation_id, store, captured_generation=CapturedGeneration(uuid4(), 7)
    )

    assert result.summary == "Updated summary."
    assert result.continuation_needed is True


@pytest.mark.asyncio
async def test_inline_summary_hole_marks_pending_even_when_batch_not_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A streaming hole inside the snapshot retains the persistent recovery
    flag even when the batch itself did not fill (fencing consolidates
    ``has_hole`` into the same flagged persist)."""
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    messages = [{"role": "user", "content": f"msg {i}"} for i in range(5)]
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_conversation_input(
            "",
            messages=messages,
            current_count=100,
            finalized_count=5,
            contiguous_count=5,
            has_hole=True,
        )
    )
    store.check_conversation_summary_generation = AsyncMock(return_value=True)
    store.update_conversation_summary = AsyncMock(return_value=True)

    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    _patch_guarded_completion(monkeypatch, "Updated summary.")

    result = await summary_module._generate_or_update_summary_result(
        conversation_id, store, captured_generation=CapturedGeneration(uuid4(), 7)
    )

    assert result.summary == "Updated summary."
    assert store.update_conversation_summary.await_args is not None
    update_kwargs = store.update_conversation_summary.await_args.kwargs
    assert update_kwargs["summary_continuation_pending"] is True
    assert result.continuation_needed is False


@pytest.mark.asyncio
async def test_inline_summary_no_continuation_when_tail_drained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_conversation_input(
            "",
            messages=[{"role": "user", "content": "only msg"}],
            current_count=20,
            finalized_count=1,
            contiguous_count=1,
        )
    )
    store.check_conversation_summary_generation = AsyncMock(return_value=True)
    store.update_conversation_summary = AsyncMock(return_value=True)

    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    _patch_guarded_completion(monkeypatch, "Summary.")

    result = await summary_module._generate_or_update_summary_result(
        conversation_id, store, captured_generation=CapturedGeneration(uuid4(), 7)
    )

    assert result.summary == "Summary."
    assert result.continuation_needed is False


@pytest.mark.asyncio
async def test_inline_summary_drained_noop_clears_pending_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An up-to-date inline call clears a stale ``pending`` continuation flag
    through the guarded check that pins version and baseline."""
    conversation_id = uuid4()
    summary_time = SimpleNamespace()
    store = object.__new__(MemoryStore)
    capture = CapturedGeneration(uuid4(), 7)
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_conversation_input(
            "prior",
            validated_baseline=20,
            current_count=20,
            finalized_count=0,
            contiguous_count=0,
            summary_updated_at=summary_time,
        )
    )
    clear = AsyncMock(return_value=True)
    store.clear_finished_summary_continuation = clear
    store.update_conversation_summary = AsyncMock()

    provider = _build_provider()
    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(provider))
    completion = _patch_guarded_completion(monkeypatch, "irrelevant")

    result = await summary_module._generate_or_update_summary_result(
        conversation_id, store, captured_generation=capture
    )

    # No messages were fetched (the consolidated read found none within the
    # contiguous prefix), so the result is a no-op and continuation is not
    # needed. The existing summary text is preserved.
    assert result.summary == "prior"
    assert result.continuation_needed is False
    completion.assert_not_awaited()
    store.update_conversation_summary.assert_not_awaited()
    clear.assert_awaited_once_with(
        conversation_id,
        captured_generation=capture,
        expected_summary_updated_at=summary_time,
        expected_baseline=20,
    )


@pytest.mark.asyncio
async def test_inline_summary_hole_keeps_pending_flag_without_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a hole present and no new finalized rows, the drained-state clear
    must NOT run: holes retain pending recovery evidence."""
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_conversation_input(
            "prior",
            validated_baseline=20,
            current_count=25,
            finalized_count=25,
            contiguous_count=20,
            has_hole=True,
        )
    )
    store.clear_finished_summary_continuation = AsyncMock()
    store.update_conversation_summary = AsyncMock()

    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    completion = _patch_guarded_completion(monkeypatch, "irrelevant")

    result = await summary_module._generate_or_update_summary_result(
        conversation_id, store, captured_generation=CapturedGeneration(uuid4(), 7)
    )

    assert result.summary == "prior"
    assert result.continuation_needed is False
    completion.assert_not_awaited()
    store.update_conversation_summary.assert_not_awaited()
    store.clear_finished_summary_continuation.assert_not_awaited()


@pytest.mark.asyncio
async def test_inline_summary_hole_pending_flag_preserved_when_finalized_remain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hole with extra finalized tail rows warns for continuation but the
    fetched ``messages`` list is empty, so only the (blocked) clear path is
    reachable; the result stays a conservative no-op."""
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_conversation_input(
            "prior",
            validated_baseline=20,
            current_count=30,
            finalized_count=30,
            contiguous_count=25,
            has_hole=True,
        )
    )
    store.clear_finished_summary_continuation = AsyncMock()
    store.update_conversation_summary = AsyncMock()

    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    completion = _patch_guarded_completion(monkeypatch, "irrelevant")

    result = await summary_module._generate_or_update_summary_result(
        conversation_id, store, captured_generation=CapturedGeneration(uuid4(), 7)
    )

    assert result.summary == "prior"
    assert result.continuation_needed is False
    completion.assert_not_awaited()
    store.clear_finished_summary_continuation.assert_not_awaited()


@pytest.mark.asyncio
async def test_inline_summary_recheck_refusal_yields_continuation_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recheck that reports the conversation moved on (owner changed /
    version changed) forces continuation so the backlog is not stranded
    even though no summary is produced by this invocation."""
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_conversation_input(
            "",
            messages=[{"role": "user", "content": "msg"}],
            current_count=1,
            finalized_count=1,
            contiguous_count=1,
        )
    )
    store.check_conversation_summary_generation = AsyncMock(return_value=False)
    store.update_conversation_summary = AsyncMock()
    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    completion = _patch_guarded_completion(monkeypatch, "should not be called")

    result = await summary_module._generate_or_update_summary_result(
        conversation_id, store, captured_generation=CapturedGeneration(uuid4(), 7)
    )

    assert result == summary_module.SummaryUpdateResult(summary=None, continuation_needed=True)
    completion.assert_not_awaited()
    store.update_conversation_summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_inline_summary_refusal_after_compute_admission_is_not_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``WriteGuardRefusal`` raised during inference/persist surfaces as a
    plain no-op for the optional inline path — not as a continuation signal."""
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_conversation_input(
            "",
            messages=[{"role": "user", "content": "msg"}],
            current_count=5,
            finalized_count=5,
            contiguous_count=5,
        )
    )
    store.check_conversation_summary_generation = AsyncMock(return_value=True)
    store.update_conversation_summary = AsyncMock(side_effect=WriteGuardRefusal("stale_generation"))
    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    _patch_guarded_completion(monkeypatch, "Updated summary.")

    result = await summary_module._generate_or_update_summary_result(
        conversation_id, store, captured_generation=CapturedGeneration(uuid4(), 7)
    )

    assert result == summary_module.SummaryUpdateResult(summary=None, continuation_needed=False)


@pytest.mark.asyncio
async def test_inline_summary_skips_when_read_inputs_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optional summary failure never discards unrelated extraction work."""
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.read_conversation_summary_inputs = AsyncMock(side_effect=RuntimeError("read failed"))
    store.update_conversation_summary = AsyncMock()
    store.clear_finished_summary_continuation = AsyncMock()
    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    completion = _patch_guarded_completion(monkeypatch, "irrelevant")

    result = await summary_module._generate_or_update_summary_result(
        conversation_id, store, captured_generation=CapturedGeneration(uuid4(), 7)
    )

    assert result == summary_module.SummaryUpdateResult(summary=None, continuation_needed=False)
    completion.assert_not_awaited()
    store.update_conversation_summary.assert_not_awaited()
    store.clear_finished_summary_continuation.assert_not_awaited()


@pytest.mark.asyncio
async def test_inline_summary_transients_after_recheck_preserve_continuation_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2 #3 (PR #165): when the summary was persisted but a
    transient follow-up error surfaces AFTER the guard recheck succeeded,
    the result must still surface continuation so the remaining tail
    isn't stranded. A full batch conservatively requests continuation."""
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_conversation_input(
            "",
            messages=[{"role": "user", "content": f"msg {i}"} for i in range(20)],
            current_count=100,
            finalized_count=100,
            contiguous_count=100,
        )
    )
    store.check_conversation_summary_generation = AsyncMock(return_value=True)
    store.update_conversation_summary = AsyncMock(side_effect=RuntimeError("transient"))
    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    _patch_guarded_completion(monkeypatch, "Updated summary.")

    result = await summary_module._generate_or_update_summary_result(
        conversation_id, store, captured_generation=CapturedGeneration(uuid4(), 7)
    )

    assert result.summary is None
    assert result.continuation_needed is True


@pytest.mark.asyncio
async def test_inline_summary_transient_after_persist_reports_only_batch_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persist-time ``RuntimeError`` (non-guard) triggers the generic
    failure branch preserving ``pre_persist_continuation or batch_was_full``."""
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_conversation_input(
            "",
            messages=[{"role": "user", "content": f"msg {i}"} for i in range(20)],
            current_count=100,
            finalized_count=100,
            contiguous_count=100,
        )
    )
    store.check_conversation_summary_generation = AsyncMock(return_value=True)
    store.update_conversation_summary = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    _patch_guarded_completion(monkeypatch, "Updated summary.")

    result = await summary_module._generate_or_update_summary_result(
        conversation_id, store, captured_generation=CapturedGeneration(uuid4(), 7)
    )

    assert result.summary is None
    assert result.continuation_needed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", "   "])
async def test_inline_summary_empty_response_returns_batch_continuation_state(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> None:
    """An empty provider answer returns ``None`` while preserving the
    pre-persist continuation decision so a forced follow-up can drain."""
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_conversation_input(
            "",
            messages=[{"role": "user", "content": "only"}],
            current_count=1,
            finalized_count=1,
            contiguous_count=1,
        )
    )
    store.check_conversation_summary_generation = AsyncMock(return_value=True)
    store.update_conversation_summary = AsyncMock()
    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    _patch_guarded_completion(monkeypatch, content)

    result = await summary_module._generate_or_update_summary_result(
        conversation_id, store, captured_generation=CapturedGeneration(uuid4(), 7)
    )

    assert result.summary is None
    # No tail remains (finalized_count == claimed count), so no continuation.
    assert result.continuation_needed is False
    store.update_conversation_summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_inline_summary_does_not_exceed_three_sentences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    long_text = "Sentence one. Sentence two. Sentence three. Sentence four."
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_conversation_input(
            "",
            messages=[{"role": "user", "content": "msg"}],
            current_count=1,
            finalized_count=1,
            contiguous_count=1,
        )
    )
    store.check_conversation_summary_generation = AsyncMock(return_value=True)
    store.update_conversation_summary = AsyncMock(return_value=True)
    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    _patch_guarded_completion(monkeypatch, long_text)

    result = await summary_module._generate_or_update_summary_result(
        conversation_id, store, captured_generation=CapturedGeneration(uuid4(), 7)
    )

    assert result.summary == "Sentence one. Sentence two. Sentence three."


@pytest.mark.asyncio
async def test_inline_summary_preserves_unseen_finalized_rows_after_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2 #2 (PR #165): when a prior summary claims 3 but the
    contiguous prefix already includes rows beyond what was committed
    (finalized_count 5 > claimed 3), the inline result must promise a
    continuation so the tail isn't silently dropped."""
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_conversation_input(
            "existing",
            messages=[{"role": "user", "content": "new question"}],
            validated_baseline=3,
            current_count=5,
            finalized_count=5,
            contiguous_count=5,
        )
    )
    store.check_conversation_summary_generation = AsyncMock(return_value=True)
    store.update_conversation_summary = AsyncMock(return_value=True)
    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    _patch_guarded_completion(monkeypatch, "Updated.")

    result = await summary_module._generate_or_update_summary_result(
        conversation_id, store, captured_generation=CapturedGeneration(uuid4(), 7)
    )

    assert result.summary == "Updated."
    assert store.update_conversation_summary.await_args is not None
    update_kwargs = store.update_conversation_summary.await_args.kwargs
    # Claim is min(3 + 1, 5) = 4; rows beyond that remain for continuation.
    assert update_kwargs["summarized_message_count"] == 4
    assert update_kwargs["summary_continuation_pending"] is True
    assert result.continuation_needed is True


@pytest.mark.asyncio
async def test_inline_summary_optimistic_conflict_surfaces_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2 #2 (PR #165): a losing CAS persist surfaces continuation."""
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.read_conversation_summary_inputs = AsyncMock(
        return_value=_conversation_input(
            "",
            messages=[{"role": "user", "content": f"msg {i}"} for i in range(20)],
            current_count=100,
            finalized_count=100,
            contiguous_count=100,
        )
    )
    store.check_conversation_summary_generation = AsyncMock(return_value=True)
    store.update_conversation_summary = AsyncMock(return_value=False)
    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    _patch_guarded_completion(monkeypatch, "Updated summary.")

    result = await summary_module._generate_or_update_summary_result(
        conversation_id, store, captured_generation=CapturedGeneration(uuid4(), 7)
    )

    assert result.summary is None
    assert result.continuation_needed is True


@pytest.mark.asyncio
async def test_read_failure_during_consolidated_fetch_returns_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any exception raised by the consolidated read is swallowed into the
    summary-only no-op — including guard-layer refusals."""
    conversation_id = uuid4()
    store = object.__new__(MemoryStore)
    store.read_conversation_summary_inputs = AsyncMock(
        side_effect=WriteGuardRefusal("stale_generation")
    )
    store.clear_finished_summary_continuation = AsyncMock()
    store.update_conversation_summary = AsyncMock()
    monkeypatch.setattr(summary_module, "get_settings", lambda: _build_settings(_build_provider()))
    completion = _patch_guarded_completion(monkeypatch, "irrelevant")

    result = await summary_module._generate_or_update_summary_result(
        conversation_id, store, captured_generation=CapturedGeneration(uuid4(), 7)
    )

    assert result == summary_module.SummaryUpdateResult(summary=None, continuation_needed=False)
    completion.assert_not_awaited()
    store.clear_finished_summary_continuation.assert_not_awaited()
