"""Account-owned jobs on ARQ's native shared queue and key families.

Completion markers contain control state only, not serialized results. Keep the
enqueue transaction aligned with the locked ARQ enqueue_job implementation.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from arq.connections import ArqRedis
from arq.constants import job_key_prefix, result_key_prefix
from arq.jobs import Job, serialize_job
from arq.utils import timestamp_ms, to_ms, to_unix_ms
from redis.exceptions import WatchError

from orchestrator.config import Settings
from orchestrator.redis_account import account_prefix

_SUFFIX = r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,511}"
_SUFFIX_RE = re.compile(_SUFFIX, re.ASCII)
_JOB_ID_RE = re.compile(r"(account:v1:\{[0-9a-f]{64}\}):job:(" + _SUFFIX + r")", re.ASCII)


def account_job_id(user_id: UUID | str, suffix: str, settings: Settings | None = None) -> str:
    """Build an opaque account ID; suffixes are bounded identifier tokens only."""
    if not isinstance(suffix, str) or _SUFFIX_RE.fullmatch(suffix) is None:
        raise ValueError("Invalid account job suffix")
    if not isinstance(user_id, (UUID, str)):
        raise ValueError("Invalid account job owner")
    try:
        owner = user_id if isinstance(user_id, UUID) else UUID(user_id)
    except ValueError:
        raise ValueError("Invalid account job owner") from None
    return f"{account_prefix(owner, settings)}:job:{suffix}"


def completion_key(job_id: str) -> str:
    """Accept only complete versioned account job IDs, never legacy/shared IDs."""
    match = _JOB_ID_RE.fullmatch(job_id) if isinstance(job_id, str) else None
    if match is None:
        raise ValueError("Invalid account job ID")
    return f"{match[1]}:completed:{match[2]}"


async def clear_account_job_completion(queue: ArqRedis, job_id: str) -> None:
    """Explicitly unblock a terminal job (e.g. failed extraction), not live work."""
    await queue.delete(completion_key(job_id))


async def enqueue_account_job(
    queue: ArqRedis,
    function: str,
    *args: Any,
    user_id: UUID | str,
    job_id: str,
    settings: Settings | None = None,
    _queue_name: str | None = None,
    _defer_until: datetime | None = None,
    _defer_by: int | float | timedelta | None = None,
    _expires: int | float | timedelta | None = None,
    _job_try: int | None = None,
    **kwargs: Any,
) -> Job | None:
    """Native-compatible enqueue with atomic job/result/completion exclusion.

    ``job_id`` is the identifier suffix, not a full ARQ ID. Producers own the
    identifiers-only payload contract; this helper does not infer ownership
    from job arguments. No per-account queue or custom Redis class is needed.
    """
    if "_job_id" in kwargs:
        raise ValueError("Use job_id for the account job suffix")
    full_id = account_job_id(user_id, job_id, settings)
    job_key = job_key_prefix + full_id
    result_key = result_key_prefix + full_id
    marker_key = completion_key(full_id)
    queue_name = queue.default_queue_name if _queue_name is None else _queue_name
    if _defer_until and _defer_by:
        raise RuntimeError("use either 'defer_until' or 'defer_by' or neither, not both")
    defer_by_ms = to_ms(_defer_by)
    expires_ms = to_ms(_expires)

    async with queue.pipeline(transaction=True) as pipe:
        # Watch all exclusion state: completion can be published concurrently
        # with removal of an expired/missing job key by a terminal finalizer.
        await pipe.watch(job_key, result_key, marker_key)
        if await pipe.exists(job_key, result_key, marker_key):
            await pipe.reset()
            return None
        enqueue_time_ms = timestamp_ms()
        if _defer_until is not None:
            score = to_unix_ms(_defer_until)
        elif defer_by_ms:
            score = enqueue_time_ms + defer_by_ms
        else:
            score = enqueue_time_ms
        expires_ms = expires_ms or score - enqueue_time_ms + queue.expires_extra_ms
        payload = serialize_job(
            function, args, kwargs, _job_try, enqueue_time_ms, serializer=queue.job_serializer
        )
        pipe.multi()
        pipe.psetex(job_key, expires_ms, payload)
        pipe.zadd(queue_name, {full_id: score})
        try:
            await pipe.execute()
        except WatchError:
            return None
    return Job(full_id, redis=queue, _queue_name=queue_name, _deserializer=queue.job_deserializer)
