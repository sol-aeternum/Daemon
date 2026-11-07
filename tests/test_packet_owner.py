"""Synthetic protocol ownership; no native effects, socket or live authority."""

import asyncio
from dataclasses import replace
import importlib
import json
import os
from pathlib import Path
import socket
import sys
from typing import Any, cast
from uuid import UUID

import pytest

from orchestrator.services.restore_history import packet_owner as m
from orchestrator.services.restore_history.packet_owner import (
    AssignmentAuthorization,
    AuthenticatedWorker,
    Budgets,
    Established,
    PacketOwner,
    PreparedPacket,
)
from orchestrator.services.restore_history.publication_protocol import (
    Action,
    PHASES,
    ProtocolError,
    Request,
    encode_reply,
)
from tests.inference_composition_support import ADMITTED, composition_violations
from tests import account_writer_catalogue as catalogue


class Effects:
    def __init__(self):
        self.blocked = False
        self.calls = []
        self.packets = []
        self.commits = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.failure: BaseException | None = None
        self.commit_before_failure = False
        self.oversized = False

    async def step(self, phase):
        self.calls.append(phase)
        self.entered.set()
        await self.release.wait()
        if self.failure is not None:
            if self.commit_before_failure:
                self.commits.append(phase)
            raise self.failure
        self.commits.append(phase)
        return Established(phase.encode())

    async def prepare(self, assignment, body, *, deadline):
        result = await self.step("prepare")
        return PreparedPacket(
            assignment.binding, b"command", b"x" * 100 if self.oversized else body
        ), result

    async def reserve(self, assignment, packet, *, deadline):
        self.packets.append(packet)
        return await self.step("reserve")

    async def catalog(self, assignment, packet, *, deadline):
        self.packets.append(packet)
        return await self.step("catalog")

    async def present(self, assignment, packet, *, deadline):
        self.packets.append(packet)
        return await self.step("present")

    async def terminal(self, assignment, packet, *, deadline):
        self.packets.append(packet)
        return await self.step("terminal")


def setup(**updates):
    session, worker, assignment, operation, handle = [UUID(int=i) for i in range(1, 6)]
    effects = Effects()
    budgets = replace(Budgets(4, 2, 1, 64, 128, 20.0, 1.0), **updates)
    owner = PacketOwner(session, effects, budgets)
    grant = AssignmentAuthorization(
        session, assignment, worker, operation, b"b" * 32, m._now() + 30
    )
    owner.admit(grant, handle)
    return owner, effects, grant, handle, AuthenticatedWorker(worker)


def request(owner, grant, handle, action: Action = "prepare", body=b"csv"):
    return Request(
        owner.session, handle, grant.operation_id, action, body if action == "prepare" else b""
    )


async def call(s, action: Action = "prepare", body=b"csv", **changes):
    owner, _, grant, handle, peer = s
    return await owner.handle(
        peer, replace(request(owner, grant, handle, action, body), **changes), deadline=m._now() + 5
    )


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_lost_replies_only_replay_established_results_and_retain_one_packet():
    s = setup()
    owner, effects, *_ = s
    for phase in PHASES:
        first = await call(s, phase)
        assert first.status == "established" and first.phase == phase
        assert encode_reply(await call(s, phase)) == encode_reply(first)
        assert effects.calls == list(PHASES[: PHASES.index(phase) + 1])
    assert all(p is effects.packets[0] for p in effects.packets)
    assert (await call(s, "observe")).result == b"terminal"
    assert owner.held_bytes == 64
    await owner.aclose()
    assert owner.held_bytes == 0


@pytest.mark.asyncio
async def test_authentication_is_not_assignment_authorization_or_a_new_request_identity():
    s = setup()
    owner, effects, grant, handle, peer = s
    await call(s)
    for bad_peer, bad_request in (
        (AuthenticatedWorker(UUID(int=90)), request(owner, grant, handle)),
        (peer, request(owner, grant, UUID(int=90))),
        (peer, replace(request(owner, grant, handle), request_id=UUID(int=91))),
        (peer, replace(request(owner, grant, handle), session=UUID(int=92))),
    ):
        with pytest.raises(ProtocolError, match="assignment_refused"):
            await owner.handle(bad_peer, bad_request, deadline=m._now() + 1)
    with pytest.raises(ProtocolError, match="assignment_conflict"):
        await call(s, body=b"changed")
    with pytest.raises(ProtocolError, match="assignment_conflict"):
        owner.admit(replace(grant, operation_id=UUID(int=99)), UUID(int=98))
    assert effects.calls == ["prepare"]
    await owner.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("committed", [False, True])
async def test_unknown_commit_or_cleanup_error_never_promotes_readback_or_repeats(phase, committed):
    s = setup()
    owner, effects, *_ = s
    for previous in PHASES[: PHASES.index(phase)]:
        await call(s, previous)
    effects.failure = RuntimeError("synthetic unknown COMMIT or cleanup")
    effects.commit_before_failure = committed
    with pytest.raises(RuntimeError, match="synthetic unknown") as caught:
        await call(s, phase)
    assert caught.value is effects.failure and owner._blocked
    calls = effects.calls.copy()
    assert (await call(s, "observe")).status == "needs_attention"
    for candidate in PHASES:
        with pytest.raises(ProtocolError, match="blocked"):
            await call(s, candidate)
    assert effects.calls == calls
    assert (phase in effects.commits) is committed
    await owner.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", PHASES)
async def test_repeated_cancellation_drains_every_phase_before_releasing_ownership(phase):
    s = setup()
    owner, effects, *_ = s
    for previous in PHASES[: PHASES.index(phase)]:
        await call(s, previous)
    effects.entered.clear()
    effects.release.clear()
    task = asyncio.create_task(call(s, phase))
    try:
        await effects.entered.wait()
        task.cancel("original cancellation")
        await until(lambda: owner._blocked)
        task.cancel("second cancellation")
        await asyncio.sleep(0)
        assert not task.done() and owner.held_bytes == 64 and owner._inflight == 1
        assert (await call(s, "observe")).status == "pending"
        assert (await call(s, "retire")).status == "pending"
        assert owner.held_bytes == 64
        effects.release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("original cancellation",)
        assert owner._inflight == 0 and owner.held_bytes == 0
        assert (await call(s, "observe")).status == "needs_attention"
    finally:
        effects.release.set()
        await asyncio.gather(task, return_exceptions=True)
        await owner.aclose()


@pytest.mark.asyncio
async def test_deadline_keeps_physical_work_owned_and_discards_late_prepare():
    s = setup(effect_seconds=0.01)
    owner, effects, *_ = s
    effects.release.clear()
    task = asyncio.create_task(call(s))
    try:
        await effects.entered.wait()
        await until(lambda: owner._blocked)
        assert not task.done() and owner.held_bytes == 64
        effects.release.set()
        with pytest.raises(TimeoutError):
            await task
        assert owner.held_bytes == 0 and owner._inflight == 0
        assert (await call(s, "observe")).status == "needs_attention"
    finally:
        effects.release.set()
        await asyncio.gather(task, return_exceptions=True)
        await owner.aclose()


@pytest.mark.asyncio
async def test_duplicate_while_owned_is_pending_and_other_work_is_not_queued():
    s = setup()
    owner, effects, grant, _, peer = s
    other = replace(grant, assignment_id=UUID(int=30), operation_id=UUID(int=31))
    handle = UUID(int=32)
    owner.admit(other, handle)
    effects.release.clear()
    task = asyncio.create_task(call(s))
    try:
        await effects.entered.wait()
        assert (await call(s)).status == "pending"
        with pytest.raises(ProtocolError, match="packet_busy"):
            await owner.handle(peer, request(owner, other, handle), deadline=m._now() + 1)
        assert effects.calls == ["prepare"] and owner.held_bytes == 64
        effects.release.set()
        assert (await task).status == "established"
    finally:
        effects.release.set()
        await asyncio.gather(task, return_exceptions=True)
        await owner.aclose()


@pytest.mark.asyncio
async def test_expiry_reclaims_payload_but_never_tombstones_or_original_assignment(monkeypatch):
    s = setup(entries=2, live_packets=1, total_packet_bytes=64)
    owner, effects, grant, handle, peer = s
    second = replace(grant, assignment_id=UUID(int=30), operation_id=UUID(int=31))
    second_handle = UUID(int=32)
    owner.admit(second, second_handle)
    await call(s)
    with pytest.raises(ProtocolError, match="capacity"):
        await owner.handle(peer, request(owner, second, second_handle), deadline=m._now() + 1)
    now = m._now()
    monkeypatch.setattr(m, "_now", lambda: now + 21)
    owner.expire()
    assert owner.held_bytes == 0
    assert (await call(s, "observe")).status == "retired"
    with pytest.raises(ProtocolError, match="retired"):
        await call(s)
    with pytest.raises(ProtocolError, match="conflict"):
        owner.admit(replace(grant, expires_at=now + 50), handle)
    third = replace(
        grant, assignment_id=UUID(int=40), operation_id=UUID(int=41), expires_at=now + 50
    )
    with pytest.raises(ProtocolError, match="tombstones_exhausted"):
        owner.admit(third, UUID(int=42))
    assert effects.calls == ["prepare"]
    await owner.aclose()


@pytest.mark.asyncio
async def test_expiry_does_not_evict_active_or_draining_packet(monkeypatch):
    s = setup()
    owner, effects, *_ = s
    effects.release.clear()
    task = asyncio.create_task(call(s))
    try:
        await effects.entered.wait()
        now = m._now()
        monkeypatch.setattr(m, "_now", lambda: now + 30)
        owner.expire()
        assert owner.held_bytes == 64 and owner._inflight == 1
        assert not task.done()
        effects.release.set()
        with pytest.raises(ProtocolError, match="uncertain"):
            await task
        assert owner.held_bytes == 0 and owner._inflight == 0
    finally:
        effects.release.set()
        await asyncio.gather(task, return_exceptions=True)
        await owner.aclose()


@pytest.mark.asyncio
async def test_restart_and_new_ids_cannot_reauthorize_an_old_assignment():
    s = setup()
    owner, effects, grant, handle, peer = s
    await call(s)
    await owner.aclose()
    fresh = PacketOwner(UUID(int=70), effects, owner.budgets)
    with pytest.raises(ProtocolError, match="assignment_refused"):
        fresh.admit(grant, UUID(int=71))
    for session in (owner.session, fresh.session):
        with pytest.raises(ProtocolError, match="assignment_refused"):
            await fresh.handle(
                peer,
                replace(request(owner, grant, handle), session=session, request_id=UUID(int=72)),
                deadline=m._now() + 1,
            )
    assert effects.calls == ["prepare"]
    await fresh.aclose()


@pytest.mark.asyncio
async def test_shutdown_admission_closes_synchronously_and_drains_repeated_cancellation():
    s = setup()
    owner, effects, *_ = s
    effects.release.clear()
    task = asyncio.create_task(call(s))
    closer = None
    try:
        await effects.entered.wait()
        owner.close_admission()
        with pytest.raises(ProtocolError, match="blocked"):
            await call(s, "reserve")
        closer = asyncio.create_task(owner.aclose())
        await asyncio.sleep(0)
        closer.cancel("original shutdown cancellation")
        await asyncio.sleep(0)
        closer.cancel("again")
        await asyncio.sleep(0)
        assert not closer.done() and not task.done() and owner.held_bytes == 64
        effects.release.set()
        with pytest.raises(ProtocolError, match="uncertain"):
            await task
        with pytest.raises(asyncio.CancelledError) as caught:
            await closer
        assert caught.value.args == ("original shutdown cancellation",)
        assert owner._closed and owner.held_bytes == 0
    finally:
        effects.release.set()
        await asyncio.gather(
            *([task, closer] if closer is not None else [task]), return_exceptions=True
        )
        await owner.aclose()


@pytest.mark.asyncio
async def test_existing_effect_latch_is_not_cleared_or_evaded_by_the_protocol():
    s = setup()
    owner, effects, *_ = s
    effects.blocked = True
    with pytest.raises(ProtocolError, match="blocked"):
        await call(s)
    effects.blocked = False  # a fake cannot make the owner healthy again
    with pytest.raises(ProtocolError, match="blocked"):
        await call(s)
    assert not effects.calls
    await owner.aclose()


@pytest.mark.asyncio
async def test_oversized_late_result_never_breaks_the_reserved_byte_budget():
    s = setup()
    owner, effects, *_ = s
    effects.oversized = True
    with pytest.raises(ProtocolError, match="result_refused"):
        await call(s)
    assert owner.held_bytes == 0 and owner._inflight == 0
    assert (await call(s, "observe")).status == "needs_attention"
    await owner.aclose()


@pytest.mark.parametrize(
    "field,value",
    [
        ("entries", True),
        ("entries", 65),
        ("live_packets", 9),
        ("inflight", 0),
        ("packet_bytes", 8193),
        ("total_packet_bytes", 1),
        ("retention_seconds", float("nan")),
        ("effect_seconds", 3),
    ],
)
def test_budgets_are_explicit_small_and_fail_closed(field, value):
    budgets = replace(Budgets(4, 2, 1, 64, 128, 20.0, 1.0), **{field: value})
    with pytest.raises(ProtocolError):
        PacketOwner(UUID(int=1), cast(Any, object()), budgets)


def test_no_io_construction_and_no_runtime_admission(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("I/O or task construction")

    for target, name in (
        (os, "open"),
        (socket, "socket"),
        (Path, "open"),
        (asyncio, "create_task"),
    ):
        monkeypatch.setattr(target, name, forbidden)
    owner = PacketOwner(UUID(int=1), cast(Any, object()), Budgets(4, 2, 1, 64, 128, 20.0, 1.0))
    assert owner.session == UUID(int=1)
    assert ADMITTED == {
        "orchestrator/compute_runtime.py",
        "orchestrator/tasks/store.py",
        "orchestrator/tasks/runner.py",
        "orchestrator/tasks/inference.py",
    }
    for source in (
        "from orchestrator.services.restore_history.packet_owner import PacketOwner\nPacketOwner(a,b,c)",
        "from orchestrator.services.restore_history import packet_owner as p\np.PacketOwner(a,b,c)",
        "PacketOwner(a,b,c)",
        "import importlib\nimportlib.import_module('orchestrator.services.restore_history.packet_owner')",
    ):
        assert composition_violations({"orchestrator/db.py": source})


@pytest.mark.asyncio
async def test_cancel_before_owned_task_starts_prevents_the_effect(monkeypatch):
    s = setup()
    owner, effects, *_ = s
    create = asyncio.create_task
    caller = asyncio.current_task()
    assert caller is not None

    def cancel_before_start(coroutine):
        child = create(coroutine)
        caller.cancel("before physical work")
        return child

    with monkeypatch.context() as patch:
        patch.setattr(asyncio, "create_task", cancel_before_start)
        with pytest.raises(asyncio.CancelledError) as caught:
            await call(s)
    assert caught.value.args == ("before physical work",)
    caller.uncancel()
    assert not effects.calls and not effects.commits
    assert owner._blocked and owner._inflight == 0 and owner.held_bytes == 0
    await owner.aclose()


@pytest.mark.asyncio
async def test_cancel_after_physical_completion_before_publication_is_not_established(monkeypatch):
    s = setup()
    owner, effects, *_ = s
    create = asyncio.create_task
    caller = asyncio.current_task()
    assert caller is not None

    def cancel_on_completion(coroutine):
        child = create(coroutine)
        # Register before shield's wakeup callback, while the caller still owns
        # settlement. The fake COMMIT has happened; no reply is established yet.
        child.add_done_callback(lambda _: caller.cancel("before result publication"))
        return child

    with monkeypatch.context() as patch:
        patch.setattr(asyncio, "create_task", cancel_on_completion)
        with pytest.raises(asyncio.CancelledError) as caught:
            await call(s)
    caller.uncancel()
    assert caught.value.args == ("before result publication",)
    assert effects.commits == ["prepare"]
    assert not next(iter(owner._entries.values())).results
    assert (await call(s, "observe")).status == "needs_attention"
    assert owner._inflight == 0 and owner.held_bytes == 0
    await owner.aclose()


@pytest.mark.asyncio
async def test_scheduling_failure_closes_unstarted_coroutine_and_keeps_tombstone(monkeypatch):
    s = setup()
    owner, effects, *_ = s
    original = RuntimeError("synthetic scheduling failure")

    def refuse(coroutine):
        raise original

    with monkeypatch.context() as patch:
        patch.setattr(asyncio, "create_task", refuse)
        with pytest.raises(RuntimeError) as caught:
            await call(s)
    assert caught.value is original and not effects.calls
    assert owner._blocked and owner._inflight == 0 and owner.held_bytes == 0
    assert (await call(s, "observe")).status == "needs_attention"
    await owner.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), True, -1, 10**1000])
async def test_every_request_including_observation_has_a_finite_deadline(deadline):
    s = setup()
    owner, effects, grant, handle, peer = s
    with pytest.raises(ProtocolError):
        await owner.handle(peer, request(owner, grant, handle, "observe"), deadline=deadline)
    assert not effects.calls and not owner._blocked
    await owner.aclose()


@pytest.mark.asyncio
async def test_fork_guard_precedes_loop_or_owner_state_access(monkeypatch):
    s = setup()
    owner, effects, *_ = s
    with monkeypatch.context() as patch:
        patch.setattr(m.os, "getpid", lambda: owner._pid + 1)
        patch.setattr(m.asyncio, "get_running_loop", lambda: pytest.fail("inherited loop touched"))
        for operation in (owner.expire, owner.close_admission, lambda: owner.held_bytes):
            with pytest.raises(ProtocolError, match="forked"):
                operation()
        with pytest.raises(ProtocolError, match="forked"):
            await call(s)
        with pytest.raises(ProtocolError, match="forked"):
            await owner.aclose()
    assert not effects.calls
    await owner.aclose()


@pytest.mark.asyncio
async def test_eager_factory_cannot_run_effect_before_ownership_is_captured():
    if not hasattr(asyncio, "eager_task_factory"):
        pytest.skip("eager task factory unavailable on this Python")
    s = setup()
    owner, effects, *_ = s
    loop = asyncio.get_running_loop()
    previous = loop.get_task_factory()

    def factory(loop, coroutine, **kwargs):
        task = asyncio.eager_task_factory(loop, coroutine, **kwargs)
        assert not effects.calls  # the initial gate is still closed
        assert owner.held_bytes == 64 and owner._inflight == 1
        return task

    try:
        loop.set_task_factory(factory)
        assert (await call(s)).status == "established"
    finally:
        loop.set_task_factory(previous)
        await owner.aclose()


@pytest.mark.asyncio
async def test_reentrant_effect_shutdown_fails_instead_of_waiting_for_itself(monkeypatch):
    s = setup()
    owner, effects, *_ = s

    async def reentrant(*args, **kwargs):
        await owner.aclose()
        pytest.fail("self-owned shutdown unexpectedly completed")

    monkeypatch.setattr(effects, "prepare", reentrant)
    with pytest.raises(ProtocolError, match="reentrant"):
        await call(s)
    assert owner._blocked and owner.held_bytes == 0 and owner._inflight == 0
    await owner.aclose()


@pytest.mark.asyncio
async def test_non_exception_abort_is_retained_and_cannot_release_a_success():
    class Abort(BaseException):
        pass

    s = setup()
    owner, effects, *_ = s
    effects.failure = Abort("synthetic physical abort")
    with pytest.raises(Abort) as caught:
        await call(s)
    assert caught.value is effects.failure
    assert owner._blocked and owner._inflight == 0 and owner.held_bytes == 0
    assert (await call(s, "observe")).status == "needs_attention"
    await owner.aclose()


def test_fresh_import_does_not_open_resources_or_construct_runtime(monkeypatch):
    # Loader source reads are intentional; application Path/os/socket/task I/O
    # during execution of either module is forbidden.
    def forbidden(*args, **kwargs):
        raise AssertionError("application I/O at import")

    for target, name in (
        (os, "open"),
        (socket, "socket"),
        (Path, "open"),
        (asyncio, "create_task"),
    ):
        monkeypatch.setattr(target, name, forbidden)
    for name in ("publication_protocol", "packet_owner"):
        full = "orchestrator.services.restore_history." + name
        package = importlib.import_module("orchestrator.services.restore_history")
        previous_attribute = getattr(package, name)
        existing = sys.modules.pop(full)
        try:
            imported = importlib.import_module(full)
            assert imported.__name__ == full
        finally:
            sys.modules[full] = existing
            setattr(package, name, previous_attribute)


def test_ram_start_and_expiry_sites_remain_explicit_deferred_uncovered_candidates():
    manifest = json.loads(catalogue.MANIFEST_PATH.read_text())
    entries = [
        entry
        for entry in manifest["writer_sites"]
        if entry["owner_source"] == "orchestrator.services.restore_history.packet_owner"
    ]
    assert {entry["fingerprint"] for entry in entries} == {
        "2175a10352b53d1a",
        "57f204a304e8ff8a",
    }
    for entry in entries:
        assert entry["qualname"] == "PacketOwner._handle"
        assert entry["kind"] == "store_write" and entry["tables"] == []
        assert entry["unknown"] is True and entry["uncovered"] is True
        assert entry["category"] == "deferred" and "guard_contract" not in entry


@pytest.mark.asyncio
async def test_start_gate_failure_cancels_only_unstarted_logical_work(monkeypatch):
    s = setup()
    owner, effects, *_ = s
    event = asyncio.Event
    count = 0
    original = RuntimeError("synthetic start gate failure")

    def make_event():
        nonlocal count
        count += 1
        result = event()
        if count == 1:

            def fail():
                raise original

            monkeypatch.setattr(result, "set", fail)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(asyncio, "Event", make_event)
        with pytest.raises(RuntimeError) as caught:
            await call(s)
    assert caught.value is original and not effects.calls
    assert owner._blocked and owner._inflight == 0 and owner.held_bytes == 0
    await owner.aclose()


@pytest.mark.asyncio
async def test_event_allocation_failure_precedes_admission_and_byte_reservation(monkeypatch):
    s = setup()
    owner, effects, *_ = s
    original = RuntimeError("synthetic event allocation failure")

    def refuse():
        raise original

    with monkeypatch.context() as patch:
        patch.setattr(asyncio, "Event", refuse)
        with pytest.raises(RuntimeError) as caught:
            await call(s)
    assert caught.value is original and not effects.calls
    assert not owner._blocked and owner._inflight == 0 and owner.held_bytes == 0
    assert next(iter(owner._entries.values())).pending is None
    await owner.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("effect_failure", [False, True])
async def test_retirement_cleanup_failure_cannot_replace_original_or_publish_late_success(
    monkeypatch, effect_failure
):
    s = setup()
    owner, effects, *_ = s
    cleanup = RuntimeError("synthetic settlement failure")
    primary = RuntimeError("synthetic physical failure")
    if effect_failure:
        effects.failure = primary
    effects.release.clear()
    task = asyncio.create_task(call(s))

    def refuse(entry):
        raise cleanup

    try:
        await effects.entered.wait()
        owner.close_admission()
        with monkeypatch.context() as patch:
            patch.setattr(owner, "_drop", refuse)
            effects.release.set()
            with pytest.raises(RuntimeError) as caught:
                await task
        if effect_failure:
            assert caught.value is primary
        else:
            assert isinstance(caught.value, ProtocolError)
            assert caught.value.code == "packet_outcome_uncertain"
        assert "Packet settlement: RuntimeError" in caught.value.__notes__
    finally:
        effects.release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert owner._blocked and owner._inflight == 0
    assert (await call(s, "observe")).status == "needs_attention"
    await owner.aclose()
    assert owner.held_bytes == 0


@pytest.mark.asyncio
async def test_shutdown_cleanup_failure_still_attempts_other_packets(monkeypatch):
    s = setup()
    owner, effects, grant, handle, peer = s
    second = replace(grant, assignment_id=UUID(int=30), operation_id=UUID(int=31))
    other = UUID(int=32)
    owner.admit(second, other)
    await call(s)
    await owner.handle(peer, request(owner, second, other), deadline=m._now() + 1)
    cleanup = RuntimeError("synthetic packet drop failure")
    original = owner._drop
    attempts = []

    def drop(entry):
        attempts.append(entry.handle)
        if entry.handle == handle:
            raise cleanup
        original(entry)

    with monkeypatch.context() as patch:
        patch.setattr(owner, "_drop", drop)
        with pytest.raises(RuntimeError) as caught:
            await owner.aclose()
    assert caught.value is cleanup and attempts == [handle, other]
    assert owner._blocked and owner._closed and owner.held_bytes == 64
    assert (await call(s, "observe")).status == "needs_attention"
    await owner.aclose()
    assert owner.held_bytes == 0
    with pytest.raises(ProtocolError, match="blocked"):
        await call(s)
