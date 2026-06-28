from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from arq.jobs import serialize_result

from orchestrator.config import Settings
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
