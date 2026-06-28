from __future__ import annotations

import hashlib
import json
import logging
import traceback
import uuid
from dataclasses import dataclass
from typing import Any, Protocol, cast

from arq.jobs import JobResult, deserialize_result
from arq.worker import Worker

from orchestrator.config import Settings
from orchestrator.services.identity.mail_sender import (
    MailMessage,
    MailSenderConfigError,
    MailSenderError,
    get_mail_sender,
)

logger = logging.getLogger(__name__)

WorkerContext = dict[str, object]

CRITICAL_WORKER_JOBS = frozenset(
    {
        "extract_memories",
        "consolidate_memories",
        "cron:consolidate_memories",
        "resolve_entities_job",
        "run_dreaming_job",
        "run_scheduled_dreaming_job",
        "generate_summary_job",
    }
)

_MAX_ARGUMENT_STRING_LENGTH = 512
_AUDIT_TIMEOUT_S = 5.0


class JobFailurePool(Protocol):
    async def execute(self, query: str, *args: object) -> object: ...


@dataclass(frozen=True)
class WorkerJobFailure:
    job_id: str
    job_name: str
    queue_name: str
    args_json: str
    kwargs_json: str
    error_type: str
    error_message: str
    traceback_text: str | None
    attempts: int


def _safe_json(value: object) -> str:
    return json.dumps(_redact_large_values(value), ensure_ascii=True, sort_keys=True, default=str)


def _redact_large_values(value: object) -> object:
    if isinstance(value, str):
        if len(value) <= _MAX_ARGUMENT_STRING_LENGTH:
            return value
        return {
            "truncated": True,
            "length": len(value),
            "preview": value[:_MAX_ARGUMENT_STRING_LENGTH],
        }
    if isinstance(value, tuple):
        return [_redact_large_values(item) for item in value]
    if isinstance(value, list):
        return [_redact_large_values(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_large_values(item) for key, item in value.items()}
    return value


def _args_signature(value: object) -> str:
    """Stable SHA256 over redacted args; used in place of plaintext so user
    chat content (e.g. generate_title's first-message arg) is never persisted
    unencrypted in job_failures.args_json.
    """
    canonical = json.dumps(
        _redact_large_values(value), ensure_ascii=True, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _result_status_error(result: Any) -> tuple[str, str] | None:
    """Treat dict results with status='error' as failures for the critical-job
    allowlist so jobs that report failure via return-value rather than raising
    (run_dreaming_job, resolve_entities_job) still trigger the alert path."""
    if not isinstance(result, dict):
        return None
    status = result.get("status")
    if not isinstance(status, str) or status != "error":
        return None
    reason = result.get("reason") or result.get("error") or "unknown"
    return "ErrorStatusResult", str(reason)[:_MAX_ARGUMENT_STRING_LENGTH]


def worker_job_failure_from_result(job_result: JobResult) -> WorkerJobFailure | None:
    if job_result.success:
        # Even on success-tagged results, a status='error' return value is a
        # semantic failure for the critical-job allowlist. Capture it as a
        # failure without raising.
        status_error = _result_status_error(job_result.result)
        if status_error is None:
            return None
        error_type, error_message = status_error
        result = job_result.result
    else:
        result = job_result.result
        error_type = type(result).__name__
        error_message = (str(result) or repr(result))[:_MAX_ARGUMENT_STRING_LENGTH]

    traceback_text: str | None = None
    if isinstance(result, BaseException):
        # arq pickle round-trip discards __traceback__; if we have it, capture.
        tb = result.__traceback__
        if tb is not None:
            traceback_text = "".join(traceback.format_exception(type(result), result, tb))

    return WorkerJobFailure(
        job_id=job_result.job_id or "<unknown>",
        job_name=job_result.function,
        queue_name=job_result.queue_name,
        args_json=json.dumps(
            {"signature": _args_signature(job_result.args)},
            ensure_ascii=True,
            sort_keys=True,
        ),
        kwargs_json=json.dumps(
            {"signature": _args_signature(job_result.kwargs)},
            ensure_ascii=True,
            sort_keys=True,
        ),
        error_type=error_type,
        error_message=error_message,
        traceback_text=traceback_text,
        attempts=int(job_result.job_try or 0),
    )


async def persist_worker_job_failure(ctx: WorkerContext, failure: WorkerJobFailure) -> None:
    pool_obj = ctx.get("db_pool")
    if pool_obj is None:
        logger.warning(
            "worker_job_failure audit skipped: db_pool unavailable job=%s id=%s",
            failure.job_name,
            failure.job_id,
        )
        return

    pool = cast(JobFailurePool, pool_obj)
    await pool.execute(
        """
        INSERT INTO job_failures (
            id,
            job_id,
            job_name,
            queue_name,
            args_json,
            kwargs_json,
            error_type,
            error_message,
            traceback,
            attempts,
            last_attempt_at
        )
        VALUES (
            $1,
            $2,
            $3,
            $4,
            $5::jsonb,
            $6::jsonb,
            $7,
            $8,
            $9,
            $10,
            NOW()
        )
        """,
        uuid.uuid4(),
        failure.job_id,
        failure.job_name,
        failure.queue_name,
        failure.args_json,
        failure.kwargs_json,
        failure.error_type,
        failure.error_message,
        failure.traceback_text,
        failure.attempts,
    )


def _alert_recipient(settings: Settings) -> str:
    return settings.daemon_worker_failure_alert_email.strip()


async def alert_critical_worker_job_failure(ctx: WorkerContext, failure: WorkerJobFailure) -> None:
    if failure.job_name not in CRITICAL_WORKER_JOBS:
        return

    settings_obj = ctx.get("settings")
    if not isinstance(settings_obj, Settings):
        return

    recipient = _alert_recipient(settings_obj)
    if not recipient:
        return

    message = MailMessage(
        to_address=recipient,
        subject=f"Daemon worker job failed: {failure.job_name}",
        body_text=(
            f"Critical worker job failed.\n\n"
            f"Job: {failure.job_name}\n"
            f"Job ID: {failure.job_id}\n"
            f"Queue: {failure.queue_name}\n"
            f"Attempts: {failure.attempts}\n"
            f"Error: {failure.error_type}: {failure.error_message}\n"
        ),
    )
    sender = get_mail_sender(settings_obj)
    await sender.send(message)


async def audit_worker_job_result(ctx: WorkerContext, result_data: bytes | None) -> None:
    if result_data is None:
        return

    try:
        job_result = deserialize_result(result_data)
        failure = worker_job_failure_from_result(job_result)
    except Exception:
        logger.warning("worker_job_failure result decode failed", exc_info=True)
        return

    if failure is None:
        return

    try:
        await persist_worker_job_failure(ctx, failure)
    except Exception:
        logger.warning("worker_job_failure audit failed", exc_info=True)

    try:
        await alert_critical_worker_job_failure(ctx, failure)
    except (MailSenderConfigError, MailSenderError) as exc:
        logger.warning("worker_job_failure alert failed: err=%s", type(exc).__name__)
    except Exception:
        logger.warning("worker_job_failure alert failed", exc_info=True)


class AuditedWorker(Worker):
    async def finish_job(
        self,
        job_id: str,
        finish: bool,
        result_data: bytes | None,
        result_timeout_s: float | None,
        keep_result_forever: bool,
        incr_score: int | None,
        keep_in_progress: float | None,
    ) -> None:
        # Finalize Redis state FIRST so a slow audit/alert path cannot keep
        # the worker slot tied up or block retry/cleanup. Audit work runs
        # afterwards under a short timeout so a hang in mail/DB cannot wedge
        # the worker indefinitely.
        await super().finish_job(
            job_id,
            finish,
            result_data,
            result_timeout_s,
            keep_result_forever,
            incr_score,
            keep_in_progress,
        )
        if finish:
            await _run_audit_with_timeout(cast(WorkerContext, self.ctx), result_data)

    async def finish_failed_job(self, job_id: str, result_data: bytes | None) -> None:
        await super().finish_failed_job(job_id, result_data)
        await _run_audit_with_timeout(cast(WorkerContext, self.ctx), result_data)


async def _run_audit_with_timeout(ctx: WorkerContext, result_data: bytes | None) -> None:
    import asyncio

    try:
        await asyncio.wait_for(
            audit_worker_job_result(ctx, result_data),
            timeout=_AUDIT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "worker_job_failure audit timed out after %.1fs",
            _AUDIT_TIMEOUT_S,
        )
    except Exception:
        logger.warning("worker_job_failure audit raised", exc_info=True)
