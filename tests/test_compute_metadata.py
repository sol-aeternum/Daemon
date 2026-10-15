"""Synthetic phase/fault tests. These are NOT real-driver/server evidence."""

import ast
import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from typing import Any, Literal, cast, overload
from uuid import uuid4

import asyncpg
import pytest

from orchestrator import compute_metadata as m


class FakePool:
    def __init__(self, identity, *, phase=None, error=None, row="valid", hang=False):
        self.identity = identity
        self.phase, self.error, self.hang = phase, error, hang
        self.row: Any = row  # Deliberately malformed records in fault tests.
        self.entered = asyncio.Event()
        self.calls = []
        self.terminated = False

    async def step(self, phase):
        self.calls.append(phase)
        if phase == self.phase:
            self.entered.set()
            if self.hang:
                await asyncio.Event().wait()
            if self.error:
                raise self.error

    async def acquire(self, *, timeout):
        assert timeout == 0.5
        await self.step("acquire")
        return self

    def transaction(self, *, isolation):
        assert isolation == "read_committed"
        return self

    async def start(self):
        await self.step("begin")

    async def execute(self, sql):
        assert "250ms" in sql and "1000ms" in sql

    async def fetchval(self, sql):
        assert sql == m.ROLE_PRECONDITION
        await self.step("preflight")
        return True

    async def fetchrow(self, sql, *args):
        assert sql.startswith("SELECT m.* FROM public.compute_metadata_")
        await self.step("statement")
        if self.row != "valid":
            return self.row
        if isinstance(self.identity, m.ScopeMetadataIdentity):
            return {"id": args[1], "account_id": args[0], "created_at": datetime.now(timezone.utc)}
        return dict(
            zip(
                ("metadata_scope_id", "reservation_id", "runtime_identity", "dispatch_nonce"),
                args[1:],
            ),
            created_at=datetime.now(timezone.utc),
        )

    async def commit(self):
        await self.step("commit")

    async def release(self, conn, *, timeout):
        assert conn is self and timeout == 0.5
        await self.step("release")

    def terminate(self):
        self.terminated = True


@overload
def identity(binding: Literal[False] = False) -> m.ScopeMetadataIdentity: ...


@overload
def identity(binding: Literal[True]) -> m.ReservationMetadataIdentity: ...


@overload
def identity(binding: bool) -> m.MetadataIdentity: ...


def identity(binding=False):
    scope = m.new_scope_metadata_identity(account_id=uuid4())
    return (
        m.new_reservation_metadata_identity(scope=scope, reservation_id=uuid4())
        if binding
        else scope
    )


async def write(store, ident):
    return await (
        store.attach_reservation(ident)
        if isinstance(ident, m.ReservationMetadataIdentity)
        else store.create_scope(ident)
    )


def fake_store(fake):
    return m.ComputeMetadataStore(cast(asyncpg.Pool, fake))


@pytest.mark.parametrize("binding", [False, True])
@pytest.mark.parametrize(
    "row", [None, {"id": None, "reservation_id": None}, {}, {"id": "bad", "reservation_id": "bad"}]
)
@pytest.mark.asyncio
async def test_invalid_write_result_after_ack_never_success(binding, row):
    ident = identity(binding)
    fake = FakePool(ident, row=row)
    with pytest.raises(m.ComputeMetadataCommittedResultUnavailable) as caught:
        await write(fake_store(fake), ident)
    assert caught.value.identity is ident and caught.value.commit_acknowledged
    assert fake.terminated and fake.calls.count("commit") == 1


@pytest.mark.parametrize("binding", [False, True])
@pytest.mark.parametrize("defect", ["timestamp", "identity"])
@pytest.mark.asyncio
async def test_invalid_timestamp_or_mismatched_uuid_after_ack(binding, defect):
    ident = identity(binding)
    fake = FakePool(ident)
    args = (
        (
            ident.scope.account_id,
            ident.scope.metadata_scope_id,
            ident.reservation_id,
            ident.runtime_identity,
            ident.dispatch_nonce,
        )
        if isinstance(ident, m.ReservationMetadataIdentity)
        else (ident.account_id, ident.metadata_scope_id)
    )
    row = cast(
        dict[str, Any],
        await fake.fetchrow(m._STATEMENTS["attach" if binding else "create_scope"], *args),
    )
    row["created_at" if defect == "timestamp" else "reservation_id" if binding else "id"] = (
        None if defect == "timestamp" else uuid4()
    )
    fake.row = row
    fake.calls.clear()
    with pytest.raises(m.ComputeMetadataCommittedResultUnavailable) as caught:
        await write(fake_store(fake), ident)
    assert caught.value.identity is ident and caught.value.commit_acknowledged
    assert fake.calls.count("statement") == fake.calls.count("commit") == 1


@pytest.mark.asyncio
async def test_invalid_typed_identity_shape_before_acquire():
    ident = replace(identity(True), scope=None)
    fake = FakePool(ident)
    with pytest.raises(m.ComputeMetadataIdentityConflict) as caught:
        await fake_store(fake).attach_reservation(ident)
    assert caught.value.identity is ident and not caught.value.commit_acknowledged
    assert not fake.calls


@pytest.mark.parametrize(
    "cls",
    [
        m.MetadataError,
        m.ComputeMetadataIdentityConflict,
        m.ComputeMetadataAccountUnavailable,
        m.ComputeMetadataUnavailable,
        m.ComputeMetadataWriteUncertain,
        m.ComputeMetadataCommittedResultUnavailable,
    ],
)
def test_uniform_error_attributes(cls):
    exc = cls("safe")
    assert exc.identity is None and exc.commit_acknowledged is False
    assert not exc.termination_failed


@pytest.mark.parametrize("binding", [False, True])
@pytest.mark.asyncio
async def test_invalid_identity_before_acquisition(binding):
    ident = identity(binding)
    ident = (
        replace(ident, dispatch_nonce=None) if binding else replace(ident, metadata_scope_id="bad")
    )
    fake = FakePool(ident)
    with pytest.raises(m.ComputeMetadataIdentityConflict) as caught:
        await write(fake_store(fake), ident)
    assert caught.value.identity is ident and not caught.value.commit_acknowledged
    assert fake.calls == []


@pytest.mark.parametrize(
    "phase", ["acquire", "begin", "preflight", "statement", "commit", "release"]
)
@pytest.mark.parametrize(
    "error_type",
    [ConnectionError, asyncpg.StatementCompletionUnknownError, asyncpg.InternalServerError],
)
@pytest.mark.asyncio
async def test_injected_failure_phase_outcomes(phase, error_type):
    ident = identity()
    fake = FakePool(ident, phase=phase, error=error_type("secret detail"))
    wanted = (
        m.ComputeMetadataCommittedResultUnavailable
        if phase == "release"
        else m.ComputeMetadataWriteUncertain
        if phase in ("statement", "commit")
        else m.ComputeMetadataUnavailable
    )
    with pytest.raises(wanted) as caught:
        await write(fake_store(fake), ident)
    exc = caught.value
    assert exc.identity is ident and exc.commit_acknowledged == (phase == "release")
    assert "secret" not in str(exc)
    assert fake.terminated == (phase != "acquire")
    assert fake.calls.count("statement") <= 1 and fake.calls.count("commit") <= 1


@pytest.mark.parametrize(
    "phase", ["acquire", "begin", "preflight", "statement", "commit", "release"]
)
@pytest.mark.asyncio
async def test_original_cancellation_with_identity_and_phase(phase):
    ident = identity()
    fake = FakePool(ident, phase=phase, hang=True)
    job = asyncio.create_task(write(fake_store(fake), ident))
    await asyncio.wait_for(fake.entered.wait(), 0.2)
    job.cancel("original caller cancellation")
    with pytest.raises(asyncio.CancelledError) as caught:
        await asyncio.wait_for(job, 0.2)
    exc = caught.value
    assert exc.args == ("original caller cancellation",)
    assert getattr(exc, "metadata_identity") is ident
    assert getattr(exc, "metadata_outcome") == (
        "committed"
        if phase == "release"
        else "uncertain"
        if phase in ("statement", "commit")
        else "not_issued"
    )
    assert not getattr(exc, "metadata_termination_failed")


@pytest.mark.asyncio
async def test_failed_synchronous_termination_visible():
    class BrokenTerminate(FakePool):
        def terminate(self):
            raise RuntimeError("synthetic termination failure")

    ident = identity()
    store = fake_store(BrokenTerminate(ident, phase="commit", error=ConnectionError()))
    with pytest.raises(m.ComputeMetadataWriteUncertain) as caught:
        await store.create_scope(ident)
    assert caught.value.termination_failed and caught.value.identity is ident
    assert store.cleanup_events == [
        {"phase": "commit", "action": "terminate_failed", "type": "RuntimeError"}
    ]


@pytest.mark.asyncio
async def test_cooperative_deadline_includes_fake_ignored_release_allowance():
    ident = identity()
    fake = FakePool(ident, phase="release", hang=True)
    start = asyncio.get_running_loop().time()
    with pytest.raises(m.ComputeMetadataCommittedResultUnavailable) as caught:
        await fake_store(fake).create_scope(ident)
    assert 1.8 < asyncio.get_running_loop().time() - start < 2.5
    assert caught.value.commit_acknowledged and caught.value.identity is ident and fake.terminated


def test_factories_are_frozen_uuid4_and_constructor_cannot_bypass():
    ident = identity(True)
    assert (
        ident.scope.metadata_scope_id.version
        == ident.runtime_identity.version
        == ident.dispatch_nonce.version
        == 4
    )
    with pytest.raises(FrozenInstanceError):
        setattr(ident, "dispatch_nonce", uuid4())
    with pytest.raises(TypeError):
        cast(Any, m.ComputeMetadataStore)(object(), "SELECT true")
    with pytest.raises(TypeError):
        cast(Any, m.ComputeMetadataStore)(object(), precondition="SELECT true")


def imports_metadata(text):
    return any(
        isinstance(n, ast.Import)
        and any("compute_metadata" in a.name.split(".") for a in n.names)
        or isinstance(n, ast.ImportFrom)
        and (
            "compute_metadata" in (n.module or "").split(".")
            or any(a.name == "compute_metadata" for a in n.names)
        )
        for n in ast.walk(ast.parse(text))
    )


def test_runtime_import_isolation_with_discriminating_controls(monkeypatch):
    for text in [
        "import orchestrator.compute_metadata as cm",
        "from orchestrator import compute_metadata",
        "from orchestrator.compute_metadata import ComputeMetadataStore",
    ]:
        assert imports_metadata(text)
    root = Path(__file__).resolve().parents[1]
    assert not [
        str(p)
        for base in ("orchestrator", "providers", "scripts")
        for p in (root / base).rglob("*.py")
        if p.name != "compute_metadata.py" and imports_metadata(p.read_text())
    ]

    def forbidden(*args, **kwargs):
        pytest.fail("import/construction initiated I/O")

    monkeypatch.setattr(asyncpg, "create_pool", forbidden)
    monkeypatch.setattr(asyncpg, "connect", forbidden)
    spec = importlib.util.spec_from_file_location("orchestrator.compute_metadata", Path(m.__file__))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ComputeMetadataStore(object())
