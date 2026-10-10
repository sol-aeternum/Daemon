from __future__ import annotations

import hashlib
import json
import logging
import re
import traceback
import uuid
from dataclasses import dataclass
from typing import Any, Protocol, cast

from arq.constants import (
    abort_jobs_ss,
    default_queue_name,
    in_progress_key_prefix,
    job_key_prefix,
    result_key_prefix,
    retry_key_prefix,
)
from arq.jobs import Deserializer, JobResult, Serializer, deserialize_result, serialize_result
from arq.utils import to_ms, to_unix_ms
from arq.worker import Worker

from orchestrator.config import Settings, get_settings
from orchestrator.redis_account import validate_redis_account_key
from orchestrator.redis_jobs import completion_key
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

# These are the ORIGINAL persistence/dedup policies, not the positive ARQ
# keep_result values now used solely to obtain terminal audit bytes in memory.
SERIALIZATION_ONLY_JOBS = frozenset(
    {"run_chat_task", "generate_home_suggestions", "extract_memories"}
)
_FAILED_EXTRACTION_COMPLETION_S = 3600

# Shared classification requires a known producer ID, function and argument
# shape. A counts-shaped result alone must never qualify an account job.
_SHARED_COUNTS: dict[str, frozenset[str]] = {
    "consolidate_memories": frozenset(
        {
            "clusters_found",
            "clusters_processed",
            "memories_created",
            "memories_demoted",
            "users_processed",
            "error_count",
        }
    ),
    "run_dreaming_job": frozenset(
        {
            "users_processed",
            "dream_runs_completed",
            "dream_runs_skipped",
            "dream_runs_failed",
            "observations_created",
            "error_count",
        }
    ),
    "run_scheduled_dreaming_job": frozenset(
        {
            "users_processed",
            "dream_runs_completed",
            "dream_runs_skipped",
            "dream_runs_failed",
            "observations_created",
            "error_count",
        }
    ),
    "run_consolidation_nudge_job": frozenset(
        {"skills_reviewed", "duplicates_found", "duplicates_merged", "stale_flagged", "error_count"}
    ),
    "garbage_collect": frozenset({"scanned", "deleted"}),
    "cleanup_web_snapshots": frozenset({"deleted"}),
    "cleanup_generated_files": frozenset({"scanned", "deleted"}),
    "cleanup_generated_images": frozenset({"scanned", "deleted"}),
    "reconcile_settlement_receipts": frozenset(
        {"examined", "reconciled", "refunded_microusd", "pending", "unavailable", "errors"}
    ),
    "sweep_tasks": frozenset(),  # One scalar count, rather than a dict.
}
_SHARED_MANUAL_IDS = {
    "consolidate_memories": re.compile(r"consolidate:all:[0-9a-f]{8}", re.ASCII),
    "run_dreaming_job": re.compile(r"dream:all:[0-9a-f]{8}", re.ASCII),
}
_SHARED_CRON_FUNCTIONS = frozenset(_SHARED_COUNTS) - {"run_dreaming_job"}


def _shared_result_data(
    job_id: str,
    result: JobResult | None,
    queue_name: str,
    serializer: Serializer | None,
) -> bytes | None:
    """Rebuild the ENTIRE envelope; never forward args, errors or unknown fields."""
    if result is None or result.job_id != job_id:
        return None
    if result.queue_name != queue_name or queue_name != default_queue_name:
        return None
    function = result.function
    if not isinstance(function, str):
        return None
    name = function.removeprefix("cron:")
    fields = _SHARED_COUNTS.get(name)
    if fields is None:
        return None
    if function.startswith("cron:"):
        if name not in _SHARED_CRON_FUNCTIONS:
            return None
        if re.fullmatch(re.escape(function) + r":[0-9]{13}", job_id, re.ASCII) is None:
            return None
        if result.args or result.kwargs:
            return None
    else:
        producer_pattern = _SHARED_MANUAL_IDS.get(function)
        if producer_pattern is None or producer_pattern.fullmatch(job_id) is None:
            return None
        if result.args not in ((), (None,)) or result.kwargs not in ({}, {"user_id": None}):
            return None
    if type(result.success) is not bool or type(result.job_try) is not int:
        return None
    if result.success:
        if name == "sweep_tasks":
            if type(result.result) is not int or result.result < 0:
                return None
            counts = result.result
        else:
            if not isinstance(result.result, dict):
                return None
            counts = {}
            for key in fields:
                if key in result.result:
                    value = result.result[key]
                    if type(value) is not int or value < 0:
                        return None
                    counts[key] = value
    else:
        # Failures retain only the protocol success flag, never exception text.
        counts = 0 if name == "sweep_tasks" else {}
    return serialize_result(
        function=function,
        args=(),
        kwargs={},
        job_try=result.job_try,
        enqueue_time_ms=to_unix_ms(result.enqueue_time),
        success=result.success,
        result=counts,
        start_ms=to_unix_ms(result.start_time),
        finished_ms=to_unix_ms(result.finish_time),
        ref=function,
        queue_name=default_queue_name,
        job_id=job_id,
        serializer=serializer,
    )


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
    (run_dreaming_job, resolve_entities_job) still trigger the alert path.
    Also treat a non-zero error_count as a semantic failure."""
    if not isinstance(result, dict):
        return None
    status = result.get("status")
    error_count = result.get("error_count") or 0
    if not (isinstance(status, str) and status == "error") and not (
        isinstance(error_count, int) and error_count > 0
    ):
        return None
    reason = result.get("reason") or result.get("error") or result.get("errors") or "unknown"
    if isinstance(reason, list):
        reason = "; ".join(str(item) for item in reason)
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

    sender = get_mail_sender(settings_obj)
    # Fail-closed surface when mail is not actually delivered: console mode
    # only logs and disabled mode silently drops, so an operator expecting
    # SMTP delivery would otherwise miss a critical-job outage.
    if getattr(sender, "sink_kind", None) == "disabled":
        logger.warning(
            "worker_job_failure alert skipped: daemon_mail_sender_mode=disabled job=%s id=%s",
            failure.job_name,
            failure.job_id,
        )
        return
    if getattr(sender, "sink_kind", None) == "console":
        logger.warning(
            "worker_job_failure alert will only log to console (daemon_mail_sender_mode=console) "
            "job=%s id=%s",
            failure.job_name,
            failure.job_id,
        )

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
    await sender.send(message)


async def audit_worker_job_result(
    ctx: WorkerContext, result_data: bytes | None, *, deserializer: Deserializer | None = None
) -> None:
    if result_data is None:
        return

    try:
        job_result = deserialize_result(result_data, deserializer=deserializer)
        failure = worker_job_failure_from_result(job_result)
    except Exception:
        logger.warning("worker_job_failure result decode failed", exc_info=True)
        return

    if failure is None:
        return

    # Persist and alert are independent: a slow / failing insert must not
    # suppress the critical-job alert, and a slow / failing SMTP send must
    # not block the durable audit row.
    await _persist_failure_with_timeout(ctx, failure)
    await _alert_failure_with_timeout(ctx, failure)


async def _persist_failure_with_timeout(ctx: WorkerContext, failure: WorkerJobFailure) -> None:
    try:
        await persist_worker_job_failure(ctx, failure)
    except Exception:
        logger.warning("worker_job_failure audit failed", exc_info=True)


async def _alert_failure_with_timeout(ctx: WorkerContext, failure: WorkerJobFailure) -> None:
    import asyncio

    try:
        await asyncio.wait_for(
            alert_critical_worker_job_failure(ctx, failure),
            timeout=_AUDIT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "worker_job_failure alert timed out after %.1fs job=%s id=%s",
            _AUDIT_TIMEOUT_S,
            failure.job_name,
            failure.job_id,
        )
    except (MailSenderConfigError, MailSenderError) as exc:
        logger.warning(
            "worker_job_failure alert skipped (mail sender unavailable): err=%s job=%s",
            type(exc).__name__,
            failure.job_name,
        )
    except Exception:
        logger.warning("worker_job_failure alert failed", exc_info=True)


class AuditedWorker(Worker):
    async def main(self) -> None:
        # Native main() connects before on_startup. A worker always has an
        # effective Redis configuration, including ARQ's localhost fallback.
        settings = self.ctx.get("settings")
        validate_redis_account_key(
            settings if isinstance(settings, Settings) else get_settings(), redis_configured=True
        )
        await super().main()

    def _decode_terminal_result(self, job_id: str, result_data: bytes | None) -> JobResult | None:
        if result_data is None:
            return None
        try:
            result = deserialize_result(result_data, deserializer=self.job_deserializer)
        except Exception:
            # Persistence fails closed; the audit path still sees the original
            # bytes and retains its existing decode-failure handling.
            return None
        if (
            result.job_id != job_id
            or result.queue_name != self.queue_name
            or not isinstance(result.function, str)
            or type(result.success) is not bool
        ):
            return None
        return result

    def _completion_marker(
        self,
        job_id: str,
        result: JobResult | None,
        timeout_s: float | None,
        forever: bool,
        *,
        preexecution_failure: bool = False,
    ) -> tuple[str, int | None] | None:
        try:
            key = completion_key(job_id)
        except ValueError:
            return None  # Shared/legacy/malformed IDs never get account state.
        function = result.function if result is not None else None
        if function == "extract_memories" and result is not None and not result.success:
            # Explicit approved exception: producers clear this content-free
            # marker when restarting a terminally failed extraction.
            return key, _FAILED_EXTRACTION_COMPLETION_S * 1000
        if not preexecution_failure and function in SERIALIZATION_ONLY_JOBS:
            return None
        # ARQ's preexecution failures used worker-wide retention, even for
        # functions originally registered keep_result=0. Unknown function or
        # decode/expiry failures preserve that lifetime without result bytes.
        if forever or timeout_s is None:
            return key, None
        if timeout_s > 0:
            return key, to_ms(timeout_s)
        return None

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
        # Adapt only locked ARQ's small finalizer, not run_job. The marker and
        # all native terminal cleanup MUST share one MULTI/EXEC. Original
        # result bytes remain local for audit AFTER successful Redis cleanup.
        result = self._decode_terminal_result(job_id, result_data) if finish else None
        stored_result = (
            _shared_result_data(job_id, result, self.queue_name, self.job_serializer)
            if finish
            else None
        )
        marker = (
            self._completion_marker(job_id, result, result_timeout_s, keep_result_forever)
            if finish
            else None
        )
        async with self.pool.pipeline(transaction=True) as tr:
            delete_keys = []
            in_progress_key = in_progress_key_prefix + job_id
            if keep_in_progress is None:
                delete_keys.append(in_progress_key)
            else:
                tr.pexpire(in_progress_key, to_ms(keep_in_progress))
            if finish:
                if stored_result:
                    expire = None if keep_result_forever else result_timeout_s
                    tr.set(result_key_prefix + job_id, stored_result, px=to_ms(expire))
                if marker is not None:
                    key, ttl_ms = marker
                    tr.set(key, b"1", px=ttl_ms)
                delete_keys.extend([retry_key_prefix + job_id, job_key_prefix + job_id])
                tr.zrem(abort_jobs_ss, job_id)
                tr.zrem(self.queue_name, job_id)
            elif incr_score:
                tr.zincrby(self.queue_name, incr_score, job_id)
            if delete_keys:
                tr.delete(*delete_keys)
            await tr.execute()
        if finish:
            await _run_audit_with_timeout(
                cast(WorkerContext, self.ctx), result_data, deserializer=self.job_deserializer
            )

    async def finish_failed_job(self, job_id: str, result_data: bytes | None) -> None:
        result = self._decode_terminal_result(job_id, result_data)
        stored_result = _shared_result_data(job_id, result, self.queue_name, self.job_serializer)
        marker = self._completion_marker(
            job_id,
            result,
            self.keep_result_s,
            self.keep_result_forever,
            preexecution_failure=True,
        )
        async with self.pool.pipeline(transaction=True) as tr:
            tr.delete(
                retry_key_prefix + job_id,
                in_progress_key_prefix + job_id,
                job_key_prefix + job_id,
            )
            tr.zrem(abort_jobs_ss, job_id)
            tr.zrem(self.queue_name, job_id)
            if stored_result is not None and (self.keep_result_forever or self.keep_result_s > 0):
                expire = None if self.keep_result_forever else self.keep_result_s
                tr.set(result_key_prefix + job_id, stored_result, px=to_ms(expire))
            if marker is not None:
                key, ttl_ms = marker
                tr.set(key, b"1", px=ttl_ms)
            await tr.execute()
        await _run_audit_with_timeout(
            cast(WorkerContext, self.ctx), result_data, deserializer=self.job_deserializer
        )


async def _run_audit_with_timeout(
    ctx: WorkerContext, result_data: bytes | None, *, deserializer: Deserializer | None = None
) -> None:
    import asyncio

    try:
        await asyncio.wait_for(
            audit_worker_job_result(ctx, result_data, deserializer=deserializer),
            timeout=_AUDIT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "worker_job_failure audit timed out after %.1fs",
            _AUDIT_TIMEOUT_S,
        )
    except Exception:
        logger.warning("worker_job_failure audit raised", exc_info=True)
