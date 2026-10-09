from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from arq.connections import ArqRedis
from arq.constants import (
    abort_jobs_ss,
    in_progress_key_prefix,
    job_key_prefix,
    result_key_prefix,
    retry_key_prefix,
)
from arq.jobs import deserialize_result, serialize_job, serialize_result
from arq.worker import Retry, func

from orchestrator.config import Settings
from orchestrator.redis_jobs import completion_key
from orchestrator.services.identity.mail_sender import MailMessage, MailSendResult
from orchestrator.worker import audit
from orchestrator.worker.audit import (
    AuditedWorker,
    audit_worker_job_result,
    worker_job_failure_from_result,
)
from orchestrator.worker.worker import worker


class FakePool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, *args: object) -> str:
        self.calls.append((query, args))
        return "INSERT 0 1"


class FailingPool:
    async def execute(self, query: str, *args: object) -> str:
        raise RuntimeError("database unavailable")


@dataclass
class FakeSender:
    sent: list[MailMessage] = field(default_factory=list)

    async def send(self, message: MailMessage) -> MailSendResult:
        self.sent.append(message)
        from datetime import datetime, timezone

        return MailSendResult(sent_at=datetime.now(timezone.utc), sink_kind="console")


def _failure_result_data(
    *,
    function: str = "extract_memories",
    result: object = RuntimeError("encryption failed"),
    args: tuple[Any, ...] = ("user-1",),
    kwargs: dict[str, Any] | None = None,
) -> bytes:
    data = serialize_result(
        function=function,
        args=args,
        kwargs=kwargs or {},
        job_try=3,
        enqueue_time_ms=1_700_000_000_000,
        success=False,
        result=result,
        start_ms=1_700_000_000_100,
        finished_ms=1_700_000_000_200,
        ref="job-1:extract_memories",
        queue_name="arq:queue",
        job_id="job-1",
    )
    assert data is not None
    return data


@pytest.mark.asyncio
async def test_worker_failure_audit_persists_failed_job() -> None:
    pool = FakePool()

    await audit_worker_job_result({"db_pool": pool}, _failure_result_data())

    assert len(pool.calls) == 1
    query, args = pool.calls[0]
    assert "INSERT INTO job_failures" in query
    assert args[1] == "job-1"
    assert args[2] == "extract_memories"
    assert args[3] == "arq:queue"
    args_signature = json.loads(str(args[4]))
    assert "signature" in args_signature
    assert len(args_signature["signature"]) == 64
    kwargs_signature = json.loads(str(args[5]))
    assert "signature" in kwargs_signature
    assert args[6] == "RuntimeError"
    assert args[7] == "encryption failed"
    assert args[9] == 3


@pytest.mark.asyncio
async def test_critical_worker_failure_sends_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakePool()
    sender = FakeSender()

    def fake_get_mail_sender(settings: Settings) -> FakeSender:
        assert settings.daemon_worker_failure_alert_email == "ops@example.test"
        return sender

    monkeypatch.setattr(audit, "get_mail_sender", fake_get_mail_sender)

    await audit_worker_job_result(
        {
            "db_pool": pool,
            "settings": Settings(
                daemon_worker_failure_alert_email="ops@example.test",
                daemon_mail_sender_mode="console",
            ),
        },
        _failure_result_data(function="extract_memories"),
    )

    assert len(sender.sent) == 1
    message = sender.sent[0]
    assert message.to_address == "ops@example.test"
    assert "extract_memories" in message.subject
    assert "encryption failed" in message.body_text


@pytest.mark.asyncio
async def test_critical_alert_still_runs_when_failure_insert_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = FakeSender()

    def fake_get_mail_sender(settings: Settings) -> FakeSender:
        return sender

    monkeypatch.setattr(audit, "get_mail_sender", fake_get_mail_sender)

    await audit_worker_job_result(
        {
            "db_pool": FailingPool(),
            "settings": Settings(daemon_worker_failure_alert_email="ops@example.test"),
        },
        _failure_result_data(function="extract_memories"),
    )

    assert len(sender.sent) == 1


@pytest.mark.asyncio
async def test_noncritical_worker_failure_does_not_send_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakePool()

    def fail_get_mail_sender(settings: Settings) -> FakeSender:
        raise AssertionError("noncritical jobs must not send alerts")

    monkeypatch.setattr(audit, "get_mail_sender", fail_get_mail_sender)

    await audit_worker_job_result(
        {
            "db_pool": pool,
            "settings": Settings(daemon_worker_failure_alert_email="ops@example.test"),
        },
        _failure_result_data(function="generate_title"),
    )

    assert len(pool.calls) == 1


def test_worker_failure_audit_caps_large_argument_strings() -> None:
    data = _failure_result_data(args=("x" * 600,))
    failure = worker_job_failure_from_result(audit.deserialize_result(data))

    assert failure is not None
    args_json = json.loads(failure.args_json)
    assert "signature" in args_json
    assert len(args_json["signature"]) == 64


def test_worker_uses_audited_worker_for_failure_persistence() -> None:
    assert isinstance(worker, AuditedWorker)


def test_job_failures_migration_defines_durable_audit_table() -> None:
    migration = Path("migrations/037_worker_job_failures.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS job_failures" in migration
    assert "args_json       JSONB" in migration
    assert "kwargs_json     JSONB" in migration
    assert "last_attempt_at TIMESTAMPTZ" in migration


_ACCOUNT_JOB = "account:v1:{" + "a" * 64 + "}:job:extract:identifier"


def _terminal_data(
    job_id: str,
    *,
    function: str = "generate_title",
    success: bool = True,
    result: object = "private generated title",
    args: tuple[Any, ...] = ("private argument",),
    kwargs: dict[str, Any] | None = None,
) -> bytes:
    data = serialize_result(
        function=function,
        args=args,
        kwargs=kwargs or {},
        job_try=1,
        enqueue_time_ms=1_700_000_000_000,
        success=success,
        result=result,
        start_ms=1_700_000_000_100,
        finished_ms=1_700_000_000_200,
        ref=function,
        queue_name="arq:queue",
        job_id=job_id,
    )
    assert data is not None
    return data


class FinalizationPipeline:
    def __init__(self, state: FinalizationState) -> None:
        self.state = state
        self.commands: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def __aenter__(self) -> FinalizationPipeline:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def _record(self, name: str, *args: object, **kwargs: object) -> None:
        self.commands.append((name, args, kwargs))

    def set(self, *args: object, **kwargs: object) -> None:
        self._record("set", *args, **kwargs)

    def delete(self, *args: object) -> None:
        self._record("delete", *args)

    def zrem(self, *args: object) -> None:
        self._record("zrem", *args)

    def zincrby(self, *args: object) -> None:
        self._record("zincrby", *args)

    def pexpire(self, *args: object) -> None:
        self._record("pexpire", *args)

    def get(self, *args: object) -> None:
        self._record("get", *args)

    def incr(self, *args: object) -> None:
        self._record("incr", *args)

    def expire(self, *args: object) -> None:
        self._record("expire", *args)

    async def execute(self) -> list[object]:
        if self.state.fail_execute:
            raise RuntimeError("Redis cleanup unavailable")
        self.state.executed.append(self.commands)
        if self.commands and self.commands[0][0] == "get":
            values: list[object] = [self.state.job_payload, self.state.job_try, True]
            if len(self.commands) == 4:
                values.append(self.state.abort_before_start)
            return values
        return []


class FinalizationState:
    def __init__(self) -> None:
        self.executed: list[list[tuple[str, tuple[Any, ...], dict[str, Any]]]] = []
        self.audited: list[bytes | None] = []
        self.job_payload: bytes | None = None
        self.job_try = 1
        self.abort_before_start = False
        self.fail_execute = False


def _offline_worker(
    monkeypatch: pytest.MonkeyPatch,
    *,
    function_name: str = "generate_title",
    coroutine: Any = None,
) -> tuple[AuditedWorker, FinalizationState]:
    state = FinalizationState()
    queue = ArqRedis()

    def pipeline(*, transaction: bool) -> FinalizationPipeline:
        assert transaction
        return FinalizationPipeline(state)

    async def capture_audit(
        ctx: object, data: bytes | None, *, deserializer: object = None
    ) -> None:
        assert state.executed, "audit must follow successful cleanup"
        assert any(
            name == "zrem" and args[0] == "arq:queue" for name, args, _ in state.executed[-1]
        ), "audit must follow the terminal transaction, not merely the initial job read"
        state.audited.append(data)

    async def noop(ctx: object, *args: object, **kwargs: object) -> str:
        return "private generated result"

    monkeypatch.setattr(queue, "pipeline", pipeline)
    monkeypatch.setattr(audit, "audit_worker_job_result", capture_audit)
    instance = AuditedWorker(
        functions=[func(coroutine or noop, name=function_name, keep_result=3600, max_tries=1)],
        redis_pool=queue,
        handle_signals=False,
        allow_abort_jobs=True,
    )
    return instance, state


def _assert_no_result_sets(state: FinalizationState) -> None:
    for transaction in state.executed:
        for name, args, _ in transaction:
            assert not (name == "set" and str(args[0]).startswith(result_key_prefix))


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_boundary", [False, True])
async def test_account_result_suppressed_marker_and_cleanup_atomic_audit_original(
    monkeypatch: pytest.MonkeyPatch, failed_boundary: bool
) -> None:
    instance, state = _offline_worker(monkeypatch)
    original = _terminal_data(_ACCOUNT_JOB, success=False, result=RuntimeError("private failure"))
    if failed_boundary:
        await instance.finish_failed_job(_ACCOUNT_JOB, original)
    else:
        await instance.finish_job(_ACCOUNT_JOB, True, original, 3600, False, None, None)
    _assert_no_result_sets(state)
    assert len(state.executed) == 1
    transaction = state.executed[0]
    assert ("set", (completion_key(_ACCOUNT_JOB), b"1"), {"px": 3_600_000}) in transaction
    deleted = {key for name, args, _ in transaction if name == "delete" for key in args}
    assert deleted == {
        job_key_prefix + _ACCOUNT_JOB,
        retry_key_prefix + _ACCOUNT_JOB,
        in_progress_key_prefix + _ACCOUNT_JOB,
    }
    assert ("zrem", (abort_jobs_ss, _ACCOUNT_JOB), {}) in transaction
    assert ("zrem", ("arq:queue", _ACCOUNT_JOB), {}) in transaction
    assert state.audited == [original]


@pytest.mark.asyncio
@pytest.mark.parametrize("job_id", ["legacy:user:job", "unknown", "account:v1:{bad}:job:one"])
async def test_unclassified_results_fail_closed_but_still_audit(
    monkeypatch: pytest.MonkeyPatch, job_id: str
) -> None:
    instance, state = _offline_worker(monkeypatch)
    original = _terminal_data(job_id)
    await instance.finish_job(job_id, True, original, 3600, False, None, None)
    _assert_no_result_sets(state)
    assert not any(name == "set" for name, _, _ in state.executed[0])
    assert state.audited == [original]


@pytest.mark.asyncio
async def test_retry_has_no_marker_audit_or_terminal_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, state = _offline_worker(monkeypatch)
    await instance.finish_job(
        _ACCOUNT_JOB, False, _terminal_data(_ACCOUNT_JOB), 3600, False, 700, None
    )
    assert state.executed == [
        [
            ("zincrby", ("arq:queue", 700, _ACCOUNT_JOB), {}),
            ("delete", (in_progress_key_prefix + _ACCOUNT_JOB,), {}),
        ]
    ]
    assert not state.audited


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl,forever", [(17.5, False), (3600, True)])
async def test_completion_preserves_retention_and_cron_in_progress(
    monkeypatch: pytest.MonkeyPatch, ttl: float, forever: bool
) -> None:
    instance, state = _offline_worker(monkeypatch)
    await instance.finish_job(
        _ACCOUNT_JOB, True, _terminal_data(_ACCOUNT_JOB), ttl, forever, None, 60
    )
    transaction = state.executed[0]
    expected_set = (
        "set",
        (completion_key(_ACCOUNT_JOB), b"1"),
        {"px": None if forever else 17_500},
    )
    assert expected_set in transaction
    assert ("pexpire", (in_progress_key_prefix + _ACCOUNT_JOB, 60_000), {}) in transaction
    deleted = {key for name, args, _ in transaction if name == "delete" for key in args}
    assert in_progress_key_prefix + _ACCOUNT_JOB not in deleted


@pytest.mark.asyncio
@pytest.mark.parametrize("function", sorted(audit.SERIALIZATION_ONLY_JOBS))
async def test_zero_retention_success_serializes_for_audit_without_marker(
    monkeypatch: pytest.MonkeyPatch, function: str
) -> None:
    instance, state = _offline_worker(monkeypatch, function_name=function)
    state.job_payload = serialize_job(function, ("private argument",), {}, None, 1_700_000_000_000)
    await instance.run_job(_ACCOUNT_JOB, 1_700_000_000_000)
    _assert_no_result_sets(state)
    assert not any(name == "set" for name, _, _ in state.executed[-1])
    assert len(state.audited) == 1
    assert state.audited[0] is not None
    assert deserialize_result(state.audited[0]).result == "private generated result"


@pytest.mark.asyncio
@pytest.mark.parametrize("function", sorted(audit.SERIALIZATION_ONLY_JOBS))
async def test_zero_retention_execution_failure_audited_with_explicit_marker_policy(
    monkeypatch: pytest.MonkeyPatch, function: str
) -> None:
    async def fail(ctx: object, *args: object) -> None:
        raise RuntimeError("private provider failure")

    instance, state = _offline_worker(monkeypatch, function_name=function, coroutine=fail)
    state.job_payload = serialize_job(function, ("private argument",), {}, None, 1_700_000_000_000)
    await instance.run_job(_ACCOUNT_JOB, 1_700_000_000_000)
    _assert_no_result_sets(state)
    sets = [command for command in state.executed[-1] if command[0] == "set"]
    if function == "extract_memories":
        assert sets == [("set", (completion_key(_ACCOUNT_JOB), b"1"), {"px": 3_600_000})]
    else:
        assert sets == []
    assert state.audited[0] is not None
    result = deserialize_result(state.audited[0])
    assert result.success is False
    assert str(result.result) == "private provider failure"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["expired", "decode", "unknown", "max_try", "abort"])
async def test_native_preexecution_failures_suppress_results_and_keep_audit(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    instance, state = _offline_worker(monkeypatch, function_name="run_chat_task")
    state.job_payload = serialize_job(
        "run_chat_task", ("private argument",), {}, None, 1_700_000_000_000
    )
    if failure == "expired":
        state.job_payload = None
    elif failure == "decode":
        state.job_payload = b"invalid serialized job"
    elif failure == "unknown":
        state.job_payload = serialize_job("no_such_function", (), {}, None, 1_700_000_000_000)
    elif failure == "max_try":
        state.job_try = 2
    else:
        state.abort_before_start = True
    await instance.run_job(_ACCOUNT_JOB, 1_700_000_000_000)
    _assert_no_result_sets(state)
    assert ("set", (completion_key(_ACCOUNT_JOB), b"1"), {"px": 3_600_000}) in state.executed[-1]
    assert len(state.audited) == 1
    assert state.audited[0] is not None
    assert not deserialize_result(state.audited[0]).success


@pytest.mark.asyncio
async def test_native_retry_never_publishes_completion_or_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def retry(ctx: object) -> None:
        raise Retry(defer=1)

    instance, state = _offline_worker(monkeypatch, coroutine=retry)
    state.job_payload = serialize_job("generate_title", (), {}, None, 1_700_000_000_000)
    await instance.run_job(_ACCOUNT_JOB, 1_700_000_000_000)
    assert not any(name == "set" for name, _, _ in state.executed[-1])
    assert not state.audited
    assert any(name == "zincrby" for name, _, _ in state.executed[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_boundary", [False, True])
async def test_redis_cleanup_failure_does_not_flip_audit_order(
    monkeypatch: pytest.MonkeyPatch, failed_boundary: bool
) -> None:
    instance, state = _offline_worker(monkeypatch)
    state.fail_execute = True
    with pytest.raises(RuntimeError, match="Redis cleanup unavailable"):
        if failed_boundary:
            await instance.finish_failed_job(_ACCOUNT_JOB, _terminal_data(_ACCOUNT_JOB))
        else:
            await instance.finish_job(
                _ACCOUNT_JOB, True, _terminal_data(_ACCOUNT_JOB), 3600, False, None, None
            )
    assert not state.audited


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "function,job_id,args",
    [
        ("cron:consolidate_memories", "cron:consolidate_memories:1700000000000", ()),
        ("consolidate_memories", "consolidate:all:abcdef01", (None,)),
        ("run_dreaming_job", "dream:all:abcdef01", (None,)),
        ("cron:run_scheduled_dreaming_job", "cron:run_scheduled_dreaming_job:1700000000000", ()),
        ("cron:run_consolidation_nudge_job", "cron:run_consolidation_nudge_job:1700000000000", ()),
    ],
)
async def test_explicit_shared_jobs_persist_counts_only_and_audit_original(
    monkeypatch: pytest.MonkeyPatch, function: str, job_id: str, args: tuple[Any, ...]
) -> None:
    instance, state = _offline_worker(monkeypatch)
    name = function.removeprefix("cron:")
    counts = {key: 2 for key in audit._SHARED_COUNTS[name]}
    original = _terminal_data(
        job_id,
        function=function,
        args=args,
        result={
            **counts,
            "errors": ["private account error"],
            "status": "error",
            "user_id": "private account ID",
            "actions": [{"reason": "private action"}],
            "new_unclassified_field": "private content",
        },
    )
    await instance.finish_job(job_id, True, original, 3600, False, None, 60)
    sets = [command for command in state.executed[0] if command[0] == "set"]
    assert len(sets) == 1
    assert sets[0][1][0] == result_key_prefix + job_id
    stored = sets[0][1][1]
    decoded = deserialize_result(stored)
    assert decoded.result == counts
    assert decoded.args == ()
    assert decoded.kwargs == {}
    assert b"private" not in stored
    assert state.audited == [original]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "function,job_id,args,kwargs,result",
    [
        ("consolidate_memories", _ACCOUNT_JOB, (), {}, {"users_processed": 2}),
        ("cron:consolidate_memories", "unknown:1700000000000", (), {}, {"users_processed": 2}),
        (
            "cron:consolidate_memories",
            "cron:consolidate_memories:1700000000000",
            ("private",),
            {},
            {},
        ),
        ("run_dreaming_job", "dream:all:abcdef01", ("private user ID",), {}, {}),
        ("run_dreaming_job", "dream:all:abcdef01", (), {"user_id": "private user ID"}, {}),
        ("run_dreaming_job", "dream:all:abcdef01", (), {}, {"users_processed": "private"}),
        ("run_dreaming_job", "dream:all:abcdef01", (), {}, {"users_processed": True}),
        ("run_dreaming_job", "dream:all:abcdef01", (), {}, {"users_processed": -1}),
    ],
)
async def test_shared_classification_rejects_unknown_owners_arguments_and_count_types(
    monkeypatch: pytest.MonkeyPatch,
    function: str,
    job_id: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    result: object,
) -> None:
    instance, state = _offline_worker(monkeypatch)
    original = _terminal_data(job_id, function=function, args=args, kwargs=kwargs, result=result)
    await instance.finish_job(job_id, True, original, 3600, False, None, None)
    _assert_no_result_sets(state)
    assert state.audited == [original]


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_boundary", [False, True])
async def test_shared_raised_failures_drop_exception_and_all_envelope_arguments(
    monkeypatch: pytest.MonkeyPatch, failed_boundary: bool
) -> None:
    instance, state = _offline_worker(monkeypatch)
    job_id = "cron:run_scheduled_dreaming_job:1700000000000"
    original = _terminal_data(
        job_id,
        function="cron:run_scheduled_dreaming_job",
        success=False,
        args=(),
        result=RuntimeError("private account exception"),
    )
    if failed_boundary:
        await instance.finish_failed_job(job_id, original)
    else:
        await instance.finish_job(job_id, True, original, 3600, False, None, 60)
    sets = [command for command in state.executed[0] if command[0] == "set"]
    assert len(sets) == 1
    stored = sets[0][1][1]
    decoded = deserialize_result(stored)
    assert decoded.result == {}
    assert decoded.success is False
    assert b"private" not in stored
    assert state.audited == [original]


def test_worker_zero_retention_registrations_now_serialize_terminal_audit_bytes() -> None:
    for function in audit.SERIALIZATION_ONLY_JOBS:
        assert worker.functions[function].keep_result_s == 3600


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_boundary", [False, True])
async def test_undecodable_result_fails_closed_without_blocking_cleanup_or_audit(
    monkeypatch: pytest.MonkeyPatch, failed_boundary: bool
) -> None:
    instance, state = _offline_worker(monkeypatch)
    original = b"not a serialized result"
    if failed_boundary:
        await instance.finish_failed_job(_ACCOUNT_JOB, original)
    else:
        await instance.finish_job(_ACCOUNT_JOB, True, original, 3600, False, None, None)
    _assert_no_result_sets(state)
    assert ("set", (completion_key(_ACCOUNT_JOB), b"1"), {"px": 3_600_000}) in state.executed[0]
    assert state.audited == [original]


@pytest.mark.asyncio
@pytest.mark.parametrize("function", sorted(audit._SHARED_CRON_FUNCTIONS))
async def test_every_registered_shared_cron_has_an_explicit_count_schema(
    monkeypatch: pytest.MonkeyPatch, function: str
) -> None:
    instance, state = _offline_worker(monkeypatch)
    job_id = f"cron:{function}:1700000000000"
    counts = 2 if function == "sweep_tasks" else {key: 2 for key in audit._SHARED_COUNTS[function]}
    original = _terminal_data(job_id, function=f"cron:{function}", args=(), result=counts)
    await instance.finish_job(job_id, True, original, 3600, False, None, 60)
    sets = [command for command in state.executed[0] if command[0] == "set"]
    assert len(sets) == 1
    decoded = deserialize_result(sets[0][1][1])
    assert decoded.result == counts
    assert decoded.args == ()
    assert decoded.kwargs == {}


@pytest.mark.asyncio
async def test_worker_validates_effective_redis_before_native_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, _ = _offline_worker(monkeypatch)
    kwargs: dict[str, Any] = {"_env_file": None}
    instance.ctx["settings"] = Settings(**kwargs, redis_url="", daemon_redis_account_hash_key=None)

    async def fail_main(self: object) -> None:
        raise AssertionError("native main must not run/connect without the ownership key")

    monkeypatch.setattr(audit.Worker, "main", fail_main)
    with pytest.raises(ValueError, match="dedicated 32-byte key"):
        await instance.main()
