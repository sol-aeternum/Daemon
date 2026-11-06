"""Closed native profile, negative source admission and owner invariants."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import importlib
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import asyncpg
from cryptography.fernet import Fernet
import pytest

from orchestrator.artifact_reservations import (
    DescriptorCodec,
    IntentBinding,
    JobBinding,
    OutputReservations,
)
from orchestrator.attempt_lifecycle import AttemptIdentity
from orchestrator.resource_store import Publication, ReservedOutputPublisher
from orchestrator.services.restore_history.effects import HistoryEffects
from orchestrator.services.restore_history.native_publication import (
    NativeContext,
    NativeOutcome,
    NativePacket,
    NativePreparation,
    NativeProfile,
    NativeRequest,
    NativeTerminal,
    native_binding,
)
from orchestrator.services.restore_history.packet_owner import (
    AssignmentAuthorization,
    AuthenticatedWorker,
    Budgets,
    NativeBudgets,
    PacketOwner,
)
from orchestrator.services.restore_history.publication_protocol import (
    PHASES,
    Action,
    ProtocolError,
    Request,
    encode_request,
    encode_reply,
)
from orchestrator.tasks._origin import OriginalTaskOrigin
from orchestrator.tasks.states import TaskStatus
from orchestrator.tasks.store import Claim
from tests.inference_composition_support import ADMITTED, composition_violations
from tests.test_artifact_reservations import reservation_references_allowed
from tests.test_resource_storage import _registry_references_allowed


def budgets(**changes):
    return replace(
        NativeBudgets(4, 2, 1, 8650752, 17301504, 30.0, 1.0, 8388608, 262144, 100000, 1048576),
        **changes,
    )


def data():
    session, worker, assignment, account, task = [uuid4() for _ in range(5)]
    identity = AttemptIdentity(uuid4(), account, 0, task, 1, uuid4(), uuid4(), uuid4())
    claim = Claim(
        task,
        1,
        account,
        uuid4(),
        uuid4(),
        uuid4(),
        0,
        3,
        {"private": "synthetic-private-input"},
        "fixture-worker",
        OriginalTaskOrigin(account, 0),
    )
    job = JobBinding(uuid4(), account, 0, task, uuid4())
    binding = native_binding(identity, job, claim, 0, worker, session, assignment)
    auth = AssignmentAuthorization(
        session,
        assignment,
        worker,
        job.operation_id,
        binding,
        asyncio.get_running_loop().time() + 30,
    )
    context = NativeContext(auth, identity, job, claim, 0)
    effects = cast(Any, object.__new__(HistoryEffects))
    effects._tasks = SimpleNamespace(_pool=object())
    effects._history = SimpleNamespace(_blocked=False)
    effects._blocked = False
    publisher = cast(Any, object.__new__(ReservedOutputPublisher))
    publisher._pool = object()
    publisher._descriptors = DescriptorCodec(Fernet(Fernet.generate_key()))
    reservations = OutputReservations(publisher._descriptors)
    profile = NativeProfile(effects, publisher, reservations, object.__new__(asyncpg.Pool))
    owner = PacketOwner(session, profile, budgets())
    handle = uuid4()
    owner.admit_native(context, handle)
    return SimpleNamespace(
        owner=owner,
        profile=profile,
        context=context,
        peer=AuthenticatedWorker(worker),
        handle=handle,
    )


def request(s, phase: Action = "prepare", payload=None):
    if payload is None:
        payload = (
            NativePreparation((("column",), ("private",)), "fixture.csv")
            if phase == "prepare"
            else NativeTerminal("final")
            if phase == "terminal"
            else None
        )
    return NativeRequest(
        s.owner.session, s.handle, s.context.authorization.operation_id, phase, payload
    )


@pytest.mark.asyncio
async def test_native_context_is_original_and_job_slot_cannot_be_rewrapped():
    s = data()
    try:
        s.owner.admit_native(s.context, s.handle)
        with pytest.raises(ProtocolError, match="conflict"):
            s.owner.admit_native(replace(s.context), s.handle)
        a = replace(s.context.authorization, assignment_id=uuid4())
        a = replace(
            a,
            binding=native_binding(
                s.context.identity,
                s.context.job,
                s.context.claim,
                0,
                a.worker_id,
                a.origin_session,
                a.assignment_id,
            ),
        )
        with pytest.raises(ProtocolError, match="conflict"):
            s.owner.admit_native(replace(s.context, authorization=a), uuid4())
        await s.owner.handle_native(
            s.peer, request(s, "retire"), deadline=asyncio.get_running_loop().time() + 1
        )
        assert s.owner._entries[s.handle].context is None
        with pytest.raises(ProtocolError, match="conflict"):
            s.owner.admit_native(replace(s.context, authorization=a), uuid4())
        s.owner.admit_native(s.context, s.handle)  # exact negative replay, never revives it
        assert s.owner._entries[s.handle].context is None
    finally:
        await s.owner.aclose()


@pytest.mark.asyncio
async def test_profiles_and_wire_frames_never_mix():
    s = data()
    try:
        with pytest.raises(ProtocolError):
            s.owner.admit(s.context.authorization, uuid4())
        with pytest.raises(ProtocolError):
            await s.owner.handle(
                s.peer,
                Request(s.owner.session, s.handle, s.context.authorization.operation_id, "prepare"),
                deadline=asyncio.get_running_loop().time() + 1,
            )
        with pytest.raises(ProtocolError):
            encode_request(cast(Any, request(s)))
        reply = await s.owner.handle_native(
            s.peer, request(s, "observe"), deadline=asyncio.get_running_loop().time() + 1
        )
        with pytest.raises(ProtocolError):
            encode_reply(reply)
        with pytest.raises(ProtocolError):
            PacketOwner(
                s.owner.session, s.profile, Budgets(4, 2, 1, 64, 128, 20.0, 1.0)
            ).admit_native(s.context, uuid4())
    finally:
        await s.owner.aclose()


@pytest.mark.asyncio
async def test_preflight_native_limits_and_private_representation():
    s = data()
    try:
        req = request(s, payload=NativePreparation((("x" * 4096,),), "private.csv"))
        prepared, fingerprint = s.profile.input(req, budgets())
        assert len(prepared.encoded) == 4097 and len(fingerprint) == 32
        for value in (s.context, req, prepared, NativeTerminal("private")):
            assert "private" not in repr(value)
        for payload in (
            NativePreparation(cast(Any, (["mutable"],)), "x"),
            NativePreparation((("x",) * 3,), "x"),
            NativePreparation((("\ud800",),), "x"),
            NativePreparation((("x",),), "\ud800"),
        ):
            with pytest.raises(ProtocolError):
                s.profile.input(request(s, payload=payload), budgets(input_cells=2))
        with pytest.raises(ProtocolError):
            s.profile.input(
                request(s, "terminal", NativeTerminal("x" * 10)), budgets(terminal_bytes=9)
            )
        assert not s.owner._blocked
    finally:
        await s.owner.aclose()


@pytest.mark.parametrize(
    "field,value",
    [
        ("packet_bytes", 8650753),
        ("total_packet_bytes", 67108865),
        ("csv_bytes", 8388609),
        ("command_bytes", 262145),
        ("input_cells", 100001),
        ("terminal_bytes", 1048577),
        ("input_cells", True),
    ],
)
def test_native_budget_caps_are_closed(field, value):
    with pytest.raises(ProtocolError):
        budgets(**{field: value}).validate()


@pytest.mark.parametrize(
    "status",
    [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.NEEDS_ATTENTION],
)
def test_known_native_control_status_is_not_relabelled_unknown_or_completed(status):
    result = NativeOutcome("terminal", status)
    NativeProfile.check_outcome(result)
    assert result.value is status
    with pytest.raises(ProtocolError):
        NativeProfile.check_outcome(NativeOutcome("terminal", TaskStatus.QUEUED))


@pytest.mark.asyncio
async def test_native_duplicate_reply_and_caller_cancellation_use_shared_engine(monkeypatch):
    s = data()
    calls = []
    entered = asyncio.Event()
    release = asyncio.Event()
    release.set()

    async def perform(context, phase, data, packet, limits, *, deadline):
        calls.append(phase)
        entered.set()
        await release.wait()
        if phase == "prepare":
            command = s.profile._publisher._descriptors.encode(
                context.job,
                IntentBinding(
                    uuid4(),
                    0,
                    uuid4(),
                    uuid4(),
                    context.identity.lifecycle_id,
                    uuid4(),
                    1,
                    len(data.encoded),
                ),
                filename=data.payload.filename,
                sha256=hashlib.sha256(data.encoded).hexdigest(),
            )
            packet = NativePacket(context.authorization.binding, command, data.encoded)
        value = (
            Publication(
                packet.command.binding.resource_id,
                packet.command.binding.version_id,
                uuid4(),
                datetime.now(UTC),
            )
            if phase == "catalog"
            else uuid4()
            if phase == "present"
            else TaskStatus.FAILED
            if phase == "terminal"
            else None
        )
        return packet, NativeOutcome(phase, value)

    monkeypatch.setattr(s.profile, "perform", perform)
    reply = None
    try:
        for phase in PHASES:
            req = request(s, phase)
            reply = await s.owner.handle_native(
                s.peer, req, deadline=asyncio.get_running_loop().time() + 1
            )
            assert (
                await s.owner.handle_native(
                    s.peer, req, deadline=asyncio.get_running_loop().time() + 1
                )
                == reply
            )
        assert calls == ["prepare", "reserve", "catalog", "present", "terminal"]
        assert reply is not None and reply.result is not None
        assert reply.result.value is TaskStatus.FAILED and not s.owner._blocked
    finally:
        await s.owner.aclose()
    s = data()
    monkeypatch.setattr(s.profile, "perform", perform)
    entered.clear()
    release.clear()
    task = asyncio.create_task(
        s.owner.handle_native(s.peer, request(s), deadline=asyncio.get_running_loop().time() + 1)
    )
    try:
        await entered.wait()
        task.cancel("original")
        await asyncio.sleep(0.01)
        task.cancel("again")
        await asyncio.sleep(0.01)
        assert not task.done() and s.owner.held_bytes == s.owner.budgets.packet_bytes
        release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ("original",) and s.owner._blocked and not s.profile.blocked
        assert not s.owner._entries[s.handle].results
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await s.owner.aclose()


def test_exact_native_type_imports_and_encoder_do_not_admit_a_host():
    path = "orchestrator/services/restore_history/native_publication.py"
    a = "from orchestrator.artifact_reservations import OutputReservations, IntentCommand, JobBinding, IntentBinding\n"
    r = "from orchestrator.resource_store import ReservedOutputPublisher, Publication\n"
    assert (
        path not in ADMITTED
        and reservation_references_allowed(path, a)
        and _registry_references_allowed(path, r)
    )
    for text in (
        a.replace("JobBinding", "JobBinding as J"),
        a.replace("IntentBinding", "DescriptorCodec"),
        "def f():\n " + a,
    ):
        assert not reservation_references_allowed(path, text)
    for text in (
        r.replace("Publication", "Publication as P"),
        r.replace("Publication", "ResourceStore"),
        "def f():\n " + r,
    ):
        assert not _registry_references_allowed(path, text)
    for name in ("NativeProfile", "NativeContext", "NativeBudgets"):
        assert composition_violations({"orchestrator/main.py": name + "(a,b,c)"})
        assert composition_violations(
            {"orchestrator/services/restore_history/runtime.py": name + "(a,b,c)"}
        )
        assert composition_violations({path: "N=" + name + "\nN(a,b,c)"})
    for method in ("admit_native", "handle_native"):
        assert composition_violations(
            {"orchestrator/services/restore_history/runtime.py": "owner." + method + "(a,b,c)"}
        )
    body = "def input(request,budgets):\n rows=[]\n return encode_csv(rows,byte_limit=budgets.csv_bytes)\n"
    assert not composition_violations({path: body})
    assert composition_violations({"orchestrator/services/restore_history/other.py": body})
    assert composition_violations({path: body.replace("budgets.csv_bytes", "8388608")})
    assert composition_violations({path: body.replace("def input", "async def input")})
    assert composition_violations(
        {
            "orchestrator/resource_csv.py": "def publish(rows):\n return encode_csv(rows,byte_limit=self.byte_limit)\n"
        }
    )


@pytest.mark.asyncio
async def test_native_import_and_construction_have_no_service_io(monkeypatch):
    s = data()

    def forbidden(*args, **kwargs):
        raise AssertionError("native profile caused service I/O")

    try:
        with monkeypatch.context() as patch:
            for target, name in (
                (asyncpg, "connect"),
                (asyncpg, "create_pool"),
                (socket, "socket"),
                (os, "open"),
                (Path, "open"),
            ):
                patch.setattr(target, name, forbidden)
            profile = NativeProfile(
                s.profile._effects,
                s.profile._publisher,
                s.profile._reservations,
                s.profile._reservation_pool,
            )
            other = PacketOwner(s.owner.session, profile, budgets())
            other.admit_native(s.context, uuid4())
            await other.aclose()
            name = "orchestrator.services.restore_history.native_publication"
            package = importlib.import_module("orchestrator.services.restore_history")
            attribute = package.native_publication
            original = sys.modules.pop(name)
            try:
                assert importlib.import_module(name).__name__ == name
            finally:
                sys.modules[name] = original
                setattr(package, "native_publication", attribute)
    finally:
        await s.owner.aclose()


@pytest.mark.asyncio
async def test_wrong_supplied_peer_never_reaches_large_input_preparation(monkeypatch):
    s = data()

    def forbidden(*args):
        pytest.fail("input work before supplied-peer match")

    monkeypatch.setattr(s.profile, "input", forbidden)
    try:
        with pytest.raises(ProtocolError, match="assignment_refused"):
            await s.owner.handle_native(
                AuthenticatedWorker(uuid4()),
                request(s),
                deadline=asyncio.get_running_loop().time() + 1,
            )
        assert not s.owner._blocked
    finally:
        await s.owner.aclose()


@pytest.mark.asyncio
async def test_native_csv_exact_existing_byte_bound_is_measured_before_effects():
    s = data()
    try:
        value = "x" * (8388608 - 1)
        prepared, _ = s.profile.input(
            request(s, payload=NativePreparation(((value,),), "x")), budgets()
        )
        assert len(prepared.encoded) == 8388608
        from orchestrator.resource_errors import ResourceError

        with pytest.raises(ResourceError):
            s.profile.input(
                request(s, payload=NativePreparation(((value + "x",),), "x")), budgets()
            )
        assert not s.owner._blocked
    finally:
        await s.owner.aclose()
