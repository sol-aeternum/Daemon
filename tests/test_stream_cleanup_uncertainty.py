"""Exact local cleanup counterexamples; synthetic owners, no provider/DB or #539."""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from orchestrator import compute_runtime as runtime
from orchestrator.services.account_control.transaction import ObservationBlocked
from orchestrator.tasks._invocation import InferenceInvocation, invocation
from tests.test_inference_repair_seams import covered


class Transport:
    def __init__(self):
        self.reads = self.closes = self.read_cancels = self.close_cancels = 0
        self.read_started = asyncio.Event()
        self.close_started = asyncio.Event()
        self.read_cancelled = asyncio.Event()
        self.release_read = asyncio.Event()
        self.release_close = asyncio.Event()
        self.release_read.set()
        self.release_close.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.reads += 1
        self.read_started.set()
        while not self.release_read.is_set():
            try:
                await self.release_read.wait()
            except asyncio.CancelledError:
                self.read_cancels += 1
                self.read_cancelled.set()
        return "late synthetic chunk"

    async def aclose(self):
        self.closes += 1
        self.close_started.set()
        while not self.release_close.is_set():
            try:
                await self.release_close.wait()
            except asyncio.CancelledError:
                self.close_cancels += 1


def owner(source=None, state=None):
    state = state or InferenceInvocation()
    scope = runtime.ComputeScope(
        uuid4(), cast(Any, SimpleNamespace(settle=AsyncMock())), inference_invocation=state
    )
    source = source or Transport()
    control = runtime._StreamControl(scope, source, asyncio.get_running_loop().time() + 60)
    return scope, state, source, control


async def drain(control, source, *extra):
    """Release ONLY synthetic test work; never clear the invocation or re-close it."""
    source.release_read.set()
    source.release_close.set()
    control.timer.cancel()  # test-owned timer, not a runtime uncertainty action
    tasks = {t for t in (*extra, control.reading, control.closer, control.closing) if t is not None}
    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=1)
        assert not pending, "synthetic test owner failed to drain"
        for task in done:
            if not task.cancelled():
                task.exception()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["stop", "expire", "close", "facade"])
async def test_fence_before_each_close_entry_starts_no_task_or_closer(entry):
    scope, state, source, control = owner()

    async def iterator():
        yield "synthetic"

    generator = iterator()
    facade = runtime._MeteredStream(generator, lambda: control)
    state.block("synthetic_unknown_release")
    try:
        if entry == "stop":
            receipt = control.stop()
            assert receipt is control.stopped
        elif entry == "expire":
            control.expire()
        elif entry == "close":
            await control.close()
        else:
            await facade.aclose()
        assert control.closing is None and control.closer is None
        assert facade.closing is None
        assert control.stopped.done() and control.state != "open"
        assert control.cleanup_outcome == "invocation_uncertain"
        assert source.closes == source.reads == 0
        assert control in scope.streams and control.response is source
    finally:
        await drain(control, source)
        await generator.aclose()  # synthetic generator test ownership only


@pytest.mark.asyncio
async def test_cleanup_queued_before_fence_retains_unresolved_owner():
    scope, state, source, control = owner()
    queued = control.stop()
    state.block("synthetic_unknown_commit")
    try:
        await asyncio.shield(queued)
        await asyncio.sleep(0)  # deliver registered observation callback
        assert source.closes == 0 and control.closer is None
        assert control in scope.streams and control.response is source
        assert control.cleanup_outcome == "invocation_uncertain"
    finally:
        await drain(control, source)


@pytest.mark.asyncio
async def test_fence_during_resistant_read_wait_forbids_subsequent_close(monkeypatch):
    monkeypatch.setattr(runtime, "STREAM_CLOSE_TIMEOUT_S", 0.04)
    scope, state, source, control = owner()
    source.release_read.clear()
    pull = asyncio.create_task(control.next())
    try:
        await asyncio.wait_for(source.read_started.wait(), 1)
        cleanup = control.stop()
        await asyncio.wait_for(source.read_cancelled.wait(), 1)
        assert source.read_cancels == 1  # issued BEFORE fence
        state.block("synthetic_unknown_read_release")
        source.release_read.set()
        await asyncio.shield(cleanup)
        done, pending = await asyncio.wait({pull}, timeout=1)
        assert not pending
        with pytest.raises((ObservationBlocked, RuntimeError)):
            done.pop().result()
        assert source.closes == 0 and source.read_cancels == 1
        assert control.reading is not None and control.reading.done()
        assert control in scope.streams
    finally:
        await drain(control, source, pull)


@pytest.mark.asyncio
async def test_fence_during_cancellation_resistant_closer_forbids_timeout_cancel(monkeypatch):
    monkeypatch.setattr(runtime, "STREAM_CLOSE_TIMEOUT_S", 0.03)
    scope, state, source, control = owner()
    source.release_close.clear()
    cleanup = control.stop()
    try:
        await asyncio.wait_for(source.close_started.wait(), 1)
        closer = control.closer
        assert closer is not None and not closer.done()
        state.block("synthetic_unknown_while_closing")
        await asyncio.shield(cleanup)
        # Give a wrongly initiated cancellation its chance to reach the closer.
        await asyncio.sleep(0)
        assert source.close_cancels == 0
        assert control.closer is closer and not closer.done()
        assert control in scope.streams
        assert control.cleanup_outcome == "invocation_uncertain"
        source.release_close.set()
        await asyncio.shield(closer)
        await asyncio.sleep(0)
        assert state.uncertainty == "synthetic_unknown_while_closing"
        assert control.cleanup_outcome == "invocation_uncertain"
        assert control in scope.streams  # task return is not a remote-closure receipt
    finally:
        await drain(control, source)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["read", "close_transport"])
async def test_fence_after_task_creation_before_queued_provider_body(monkeypatch, target):
    _, state, source, control = owner()
    create = asyncio.create_task

    def queue(coro, *args, **kwargs):
        task = create(coro, *args, **kwargs)
        if coro.cr_code.co_name == target:
            state.block("synthetic_queued_body")
        return task

    monkeypatch.setattr(runtime.asyncio, "create_task", queue)
    try:
        if target == "read":
            with pytest.raises((ObservationBlocked, RuntimeError)):
                await control.next()
        else:
            await control.close()
        assert source.reads == source.closes == 0
    finally:
        await drain(control, source)


@pytest.mark.asyncio
@pytest.mark.parametrize("hook", ["iterator", "closer_lookup"])
async def test_synchronous_sdk_hook_cannot_authorize_following_effect(hook):
    state = InferenceInvocation()

    class Hooked(Transport):
        def __aiter__(self):
            if hook == "iterator":
                state.block("synthetic_aiter_hook")
            return self

        def __getattribute__(self, name):
            if name == "aclose" and hook == "closer_lookup":
                state.block("synthetic_getattr_hook")
            return super().__getattribute__(name)

    _, _, source, control = owner(Hooked(), state)
    try:
        if hook == "iterator":
            with pytest.raises((ObservationBlocked, RuntimeError)):
                await control.next()
        else:
            await control.close()
        assert source.reads == source.closes == 0
    finally:
        await drain(control, source)


@pytest.mark.asyncio
async def test_late_read_result_after_fence_cannot_publish_or_start_cleanup():
    _, state, source, control = owner()
    source.release_read.clear()
    pull = asyncio.create_task(control.next())
    try:
        await asyncio.wait_for(source.read_started.wait(), 1)
        state.block("synthetic_late_chunk")
        source.release_read.set()
        with pytest.raises((ObservationBlocked, RuntimeError)):
            await pull
        assert source.closes == 0 and source.read_cancels == 0
        assert control.closing is None
    finally:
        await drain(control, source, pull)


@pytest.mark.asyncio
async def test_control_uses_original_bound_invocation_not_mutated_scope_or_context():
    scope, original, source, control = owner()
    original.block("synthetic_original")
    scope.inference_invocation = InferenceInvocation()
    token = invocation.set(InferenceInvocation())
    try:
        await control.close()
        assert source.closes == 0 and control.closing is None
    finally:
        invocation.reset(token)
        await drain(control, source)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_nested_scope_finally_preserves_exact_primary_and_no_effects(monkeypatch, cancel):
    template, state, service, _ = covered()
    monkeypatch.setattr(runtime, "EntitlementService", lambda _: service)
    token = invocation.set(state)
    original = asyncio.CancelledError("original") if cancel else ValueError("original")
    controls = []
    try:
        with pytest.raises(type(original)) as caught:
            async with runtime.account_compute(
                object(),
                template.user_id,
                inference_identity=template.inference_identity,
                inference_recovery=template.inference_recovery,
            ) as outer:
                async with runtime.account_compute(object(), template.user_id) as inner:
                    assert outer.inference_invocation is inner.inference_invocation is state
                    for scope in (outer, inner):
                        source = Transport()
                        control = runtime._StreamControl(
                            scope, source, asyncio.get_running_loop().time() + 60
                        )
                        reservation = SimpleNamespace(id=uuid4())
                        scope.outstanding[reservation.id] = runtime.ReservationHold(
                            reservation, 10, stream=control
                        )
                        controls.append((scope, source, control))
                    state.block("synthetic_nested_unknown")
                    raise original
        assert caught.value is original
        service.settle.assert_not_awaited()
        for scope, source, control in controls:
            assert control.closing is None and source.closes == 0
            assert scope.closed and control in scope.streams and scope.outstanding
    finally:
        invocation.reset(token)
        for _, source, control in controls:
            await drain(control, source)


@pytest.mark.asyncio
async def test_secondary_scope_cleanup_failure_never_replaces_original_cancel(monkeypatch):
    template, state, service, _ = covered()
    monkeypatch.setattr(runtime, "EntitlementService", lambda _: service)
    token = invocation.set(state)
    original = asyncio.CancelledError("original identity")
    secondary = ValueError("existing cleanup failure")
    future = asyncio.get_running_loop().create_future()
    future.set_exception(secondary)
    source = Transport()
    control = None
    try:
        with pytest.raises(asyncio.CancelledError) as caught:
            async with runtime.account_compute(
                object(),
                template.user_id,
                inference_identity=template.inference_identity,
                inference_recovery=template.inference_recovery,
            ) as scope:
                control = runtime._StreamControl(
                    scope, source, asyncio.get_running_loop().time() + 60
                )
                monkeypatch.setattr(control, "stop", lambda: future)
                state.block("synthetic_secondary")
                raise original
        assert caught.value is original
        assert any("cleanup" in note.lower() for note in original.__notes__)
    finally:
        invocation.reset(token)
        future.exception()
        if control is not None:
            await drain(control, source)


@pytest.mark.asyncio
async def test_facade_delayed_pull_finally_does_not_schedule_close_after_fence():
    _, state, source, control = owner()
    entered, release = asyncio.Event(), asyncio.Event()
    original = asyncio.CancelledError("pull original")

    async def iterator():
        entered.set()
        await release.wait()
        raise original
        yield  # retain async-generator protocol

    facade = runtime._MeteredStream(iterator(), lambda: control)
    pull = asyncio.create_task(anext(facade))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        state.block("synthetic_pull_finally")
        await facade.aclose()
        release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await pull
        assert caught.value is original
        assert facade.closing is None and control.closing is None and source.closes == 0
    finally:
        release.set()
        await drain(control, source, pull)
        await facade.iterator.aclose()


@pytest.mark.asyncio
async def test_sync_closer_fencing_retains_returned_unstarted_awaitable():
    state = InferenceInvocation()

    class Hooked(Transport):
        def aclose(self):
            state.block("synthetic_sync_close_hook")
            return super().aclose()

    scope, _, source, control = owner(Hooked(), state)
    try:
        await control.close()
        assert source.closes == 0  # returning a coroutine did not start its body
        assert control.close_awaitable is not None
        assert control in scope.streams and control.cleanup_outcome == "invocation_uncertain"
    finally:
        # This unstarted fake coroutine is ONLY synthetic test-owned disposal.
        if control.close_awaitable is not None:
            control.close_awaitable.close()
        await drain(control, source)


@pytest.mark.asyncio
async def test_facade_queued_finalizer_fenced_before_generator_finally(monkeypatch):
    _, state, source, control = owner()
    finalized = []

    async def iterator():
        try:
            yield "synthetic"
        finally:
            finalized.append(True)

    facade = runtime._MeteredStream(iterator(), lambda: control)
    assert await anext(facade) == "synthetic"
    # Schedule the real finalizer owner while healthy, fence before its body runs.
    create = asyncio.create_task

    def queue(coro, *args, **kwargs):
        task = create(coro, *args, **kwargs)
        if coro.cr_code.co_name == "close_iterator":
            state.block("synthetic_queued_generator_close")
        return task

    try:
        monkeypatch.setattr(runtime.asyncio, "create_task", queue)
        await facade._close_iterator()
        assert not finalized and source.closes == 0
        assert facade.closing is not None and facade.closing.done()
    finally:
        await facade.iterator.aclose()
        await drain(control, source)


@pytest.mark.asyncio
async def test_fenced_facade_never_joins_running_resistant_generator_finalizer():
    _, state, source, control = owner()
    entered, release = asyncio.Event(), asyncio.Event()

    async def iterator():
        try:
            yield "synthetic"
        finally:
            entered.set()
            await release.wait()

    facade = runtime._MeteredStream(iterator(), lambda: control)
    assert await anext(facade) == "synthetic"
    healthy_join = asyncio.create_task(facade._close_iterator())
    fenced_join = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        existing = facade.closing
        assert existing is not None and not existing.done()
        state.block("synthetic_resistant_generator_finalizer")
        fenced_join = asyncio.create_task(facade.aclose())
        _, pending = await asyncio.wait({fenced_join}, timeout=0.03)
        assert not pending, "uncertain facade must not join an unresolved finalizer"
        fenced_join.result()
        assert facade.closing is existing and not existing.done()
        assert control.closing is None and source.closes == 0
    finally:
        release.set()
        joins = {healthy_join, facade.closing}
        if fenced_join is not None:
            joins.add(fenced_join)
        done, pending = await asyncio.wait({task for task in joins if task is not None}, timeout=1)
        assert not pending
        for task in done:
            if not task.cancelled():
                task.exception()
        await drain(control, source)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "healthy", "known_interruption"])
async def test_healthy_and_known_interruption_still_close_once_without_leak(mode):
    scope, state, source, control = owner()
    if mode == "legacy":
        # Construct truly legacy control; changing scope later is not a grant.
        await drain(control, source)
        scope.streams.clear()
        scope.inference_invocation = None
        control = runtime._StreamControl(scope, source, asyncio.get_running_loop().time() + 60)
    state.call_failed = mode == "known_interruption"
    try:
        await control.close()
        await control.close()
        await asyncio.sleep(0)
        assert source.closes == 1 and control.cleanup_outcome == "close_returned"
        assert control not in scope.streams and control.timer.cancelled()
        assert control.closer is not None and control.closer.done()
    finally:
        await drain(control, source)
