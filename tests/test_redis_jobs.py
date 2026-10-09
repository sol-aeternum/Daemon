from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest
from arq.connections import ArqRedis
from arq.constants import job_key_prefix, result_key_prefix
from arq.jobs import deserialize_job
from redis.exceptions import WatchError

from orchestrator import redis_jobs
from orchestrator.config import Settings
from orchestrator.redis_account import account_prefix
from orchestrator.redis_jobs import (
    account_job_id,
    clear_account_job_completion,
    completion_key,
    enqueue_account_job,
)

OWNER = UUID("348563a5-0407-4d04-a7c4-d39b2a89777b")


def _settings() -> Settings:
    kwargs: dict[str, Any] = {"_env_file": None}
    return Settings(
        **kwargs,
        daemon_redis_account_hash_key=(
            base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
        ),
    )


class EnqueuePipeline:
    """Recording native async pipeline boundary; no Redis/socket is used."""

    def __init__(self) -> None:
        self.watched: tuple[str, ...] = ()
        self.existing = 0
        self.commands: list[tuple[str, tuple[Any, ...]]] = []
        self.multi_called = False
        self.reset_called = False
        self.conflict = False

    async def __aenter__(self) -> EnqueuePipeline:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def watch(self, *keys: str) -> None:
        self.watched = keys

    async def exists(self, *keys: str) -> int:
        assert keys == self.watched
        return self.existing

    async def reset(self) -> None:
        self.reset_called = True

    def multi(self) -> None:
        self.multi_called = True

    def psetex(self, *args: object) -> None:
        assert self.multi_called
        self.commands.append(("psetex", args))

    def zadd(self, *args: object) -> None:
        assert self.multi_called
        self.commands.append(("zadd", args))

    async def execute(self) -> list[object]:
        if self.conflict:
            # Simulates a terminal marker or competing producer changing any
            # watched key between EXISTS and MULTI/EXEC.
            raise WatchError("watched exclusion state changed")
        return []


def _queue(monkeypatch: pytest.MonkeyPatch, pipeline: EnqueuePipeline) -> ArqRedis:
    queue = ArqRedis()

    def fake_pipeline(*, transaction: bool) -> EnqueuePipeline:
        assert transaction
        return pipeline

    monkeypatch.setattr(queue, "pipeline", fake_pipeline)
    return queue


def test_account_job_id_opaque_and_completion_strict() -> None:
    settings = _settings()
    job_id = account_job_id(OWNER, "extract:conversation:3", settings)
    assert job_id == account_prefix(str(OWNER).upper(), settings) + ":job:extract:conversation:3"
    assert str(OWNER) not in job_id
    assert completion_key(job_id) == (
        account_prefix(OWNER, settings) + ":completed:extract:conversation:3"
    )
    assert account_job_id(UUID(int=2), "extract:conversation:3", settings) != job_id


@pytest.mark.parametrize("suffix", ["", "has space", "x{y}", "x*", "x\n", "é", "x" * 513])
def test_account_job_id_rejects_invalid_suffix(suffix: str) -> None:
    with pytest.raises(ValueError, match="Invalid account job suffix"):
        account_job_id(OWNER, suffix, _settings())


@pytest.mark.parametrize("owner", ["not-an-account", "", "348563a5"])
def test_account_job_id_rejects_invalid_owner(owner: str) -> None:
    with pytest.raises(ValueError, match="Invalid account job owner"):
        account_job_id(owner, "extract", _settings())


@pytest.mark.parametrize(
    "job_id",
    [
        "legacy:user:job",
        "cron:garbage_collect:1700000000000",
        "account:v1:{" + "A" * 64 + "}:job:x",
        "account:v1:{" + "a" * 63 + "}:job:x",
        "account:v1:{" + "a" * 64 + "}:job:",
        "account:v1:{" + "a" * 64 + "}:job:x\n",
        "prefix:account:v1:{" + "a" * 64 + "}:job:x",
    ],
)
def test_completion_key_rejects_noncanonical_ids(job_id: str) -> None:
    with pytest.raises(ValueError, match="Invalid account job ID"):
        completion_key(job_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "score", "expiry"),
    [
        ({}, 1_700_000_000_000, 86_400_000),
        ({"_defer_by": timedelta(seconds=30)}, 1_700_000_030_000, 86_430_000),
        ({"_defer_by": 1.5, "_expires": 20}, 1_700_000_001_500, 20_000),
        ({"_defer_by": 0, "_expires": 0}, 1_700_000_000_000, 86_400_000),
        (
            {"_defer_until": datetime(2023, 11, 14, 22, 14, tzinfo=timezone.utc)},
            1_700_000_040_000,
            86_440_000,
        ),
    ],
)
async def test_enqueue_native_payload_timestamps_expiry_and_queue(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, Any], score: int, expiry: int
) -> None:
    pipeline = EnqueuePipeline()
    queue = _queue(monkeypatch, pipeline)
    monkeypatch.setattr(redis_jobs, "timestamp_ms", lambda: 1_700_000_000_000)
    job = await enqueue_account_job(
        queue,
        "extract_memories",
        "message-identifier",
        user_id=OWNER,
        job_id="extract:conversation",
        settings=_settings(),
        _job_try=4,
        _queue_name="arq:custom",
        fragment_offset=10,
        **options,
    )
    assert job is not None
    assert isinstance(queue, ArqRedis)
    full_id = job.job_id
    assert pipeline.watched == (
        job_key_prefix + full_id,
        result_key_prefix + full_id,
        completion_key(full_id),
    )
    command, args = pipeline.commands[0]
    assert command == "psetex"
    assert args[:2] == (job_key_prefix + full_id, expiry)
    decoded = deserialize_job(args[2])
    assert decoded.function == "extract_memories"
    assert decoded.args == ("message-identifier",)
    assert decoded.kwargs == {"fragment_offset": 10}
    assert decoded.job_try == 4
    assert int(decoded.enqueue_time.timestamp() * 1000) == 1_700_000_000_000
    assert pipeline.commands[1] == ("zadd", ("arq:custom", {full_id: score}))


@pytest.mark.asyncio
async def test_enqueue_existing_or_concurrent_completion_blocks_without_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for existing, conflict in ((1, False), (0, True)):
        pipeline = EnqueuePipeline()
        pipeline.existing = existing
        pipeline.conflict = conflict
        result = await enqueue_account_job(
            _queue(monkeypatch, pipeline),
            "generate_title",
            user_id=OWNER,
            job_id="title",
            settings=_settings(),
        )
        assert result is None
        if existing:
            assert pipeline.reset_called
            assert not pipeline.commands
        else:
            assert pipeline.multi_called
            assert len(pipeline.watched) == 3


@pytest.mark.asyncio
async def test_enqueue_invalid_options_fail_before_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = ArqRedis()

    def fail_pipeline(*args: object, **kwargs: object) -> None:
        raise AssertionError("must reject invalid options before touching Redis")

    monkeypatch.setattr(queue, "pipeline", fail_pipeline)
    with pytest.raises(RuntimeError, match="not both"):
        await enqueue_account_job(
            queue,
            "generate_title",
            user_id=OWNER,
            job_id="title",
            settings=_settings(),
            _defer_by=1,
            _defer_until=datetime.now(timezone.utc),
        )
    with pytest.raises(ValueError, match="Use job_id"):
        await enqueue_account_job(
            queue,
            "generate_title",
            user_id=OWNER,
            job_id="title",
            settings=_settings(),
            _job_id="bypass",
        )


@pytest.mark.asyncio
async def test_clear_completion_only_deletes_strict_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = ArqRedis()
    deleted: list[str] = []

    async def fake_delete(key: str) -> int:
        deleted.append(key)
        return 1

    monkeypatch.setattr(queue, "delete", fake_delete)
    full_id = account_job_id(OWNER, "extract", _settings())
    await clear_account_job_completion(queue, full_id)
    assert deleted == [completion_key(full_id)]
    with pytest.raises(ValueError):
        await clear_account_job_completion(queue, "legacy:extract")
    assert len(deleted) == 1
