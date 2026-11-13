from __future__ import annotations

import importlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from unittest.mock import AsyncMock, MagicMock, call

from orchestrator.artifacts import user_artifact_directory
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.worker.jobs import _cleanup_expired_artifacts, garbage_collect


def _memory_store(pool: AsyncMock) -> MemoryStore:
    enc = MagicMock(spec=ContentEncryption)
    enc.encrypt = MagicMock(side_effect=lambda value: value)
    enc.decrypt = MagicMock(side_effect=lambda value: value)
    return MemoryStore(db_pool=pool, encryption=enc)


@pytest.mark.asyncio
async def test_memory_store_run_garbage_collect_deletes_expired_non_active_rows() -> None:
    now = datetime(2026, 11, 9, tzinfo=UTC)
    owner = uuid.uuid4()

    def row(
        status: str,
        days: int,
        *,
        purge_after: datetime | None = None,
        tombstone: bool = False,
        valid_to: datetime | None = None,
    ) -> dict[str, Any]:
        return {
            "id": uuid.uuid4(),
            "user_id": owner,
            "status": status,
            "updated_at": now - timedelta(days=days),
            "purge_after": purge_after,
            "tombstone": tombstone,
            "valid_to": valid_to,
        }

    candidates = [
        row("deleted", 30, tombstone=True, purge_after=now),
        row("inactive", 91),
        row("pending", 31),
    ]
    retained = [
        row("deleted", 29, tombstone=True, purge_after=now + timedelta(days=1)),
        # A tombstone's own grace governs, even if the old row timestamp expired.
        row("deleted", 100, tombstone=True, purge_after=now + timedelta(days=1)),
        row("inactive", 90),
        row("pending", 30),
        row("rejected", 30),
        row("deleted", 30),
        # Superseded history is not age-capped: neither explicit nor legacy closed rows.
        row("superseded", 1000),
        row("active", 1000, valid_to=now - timedelta(days=999)),
        row("active", 1000),
    ]

    async def eligible_rows(sql, *args):
        # Pin the real tombstone/clock query, not a permissive SQL substring.
        assert " ".join(sql.split()) == (
            "SELECT m.id,m.user_id FROM memories m LEFT JOIN memory_tombstones t "
            "ON t.user_id=m.user_id AND t.memory_id=m.id WHERE "
            "(t.purge_after IS NOT NULL AND t.purge_after<=clock_timestamp()) "
            "OR (t.memory_id IS NULL AND ((m.status='inactive' AND m.updated_at<clock_timestamp()-interval '90 days') "
            "OR (m.status IN ('pending','rejected','deleted') AND m.updated_at<clock_timestamp()-interval '30 days')))"
        )
        assert args == ()
        eligible = [
            item
            for item in [*candidates, *retained]
            if (item["purge_after"] is not None and item["purge_after"] <= now)
            or (
                not item["tombstone"]
                and (
                    (item["status"] == "inactive" and item["updated_at"] < now - timedelta(days=90))
                    or (
                        item["status"] in {"pending", "rejected", "deleted"}
                        and item["updated_at"] < now - timedelta(days=30)
                    )
                )
            )
        ]
        assert eligible == candidates
        return [{"id": item["id"], "user_id": item["user_id"]} for item in eligible]

    pool = AsyncMock()
    pool.fetch.side_effect = eligible_rows
    store = _memory_store(pool)
    # GC delegates physical lineage purge to the public owner-qualified method,
    # which owns registered mutation/erasure-clock handling (real-PG tested).
    # One selected row disappears before deletion; count only acknowledged purges.
    delete = AsyncMock(side_effect=[True, False, True])
    store.delete_memory = delete

    result = await store.run_garbage_collect()

    assert result == {"scanned": 3, "deleted": 2}
    pool.fetch.assert_awaited_once()
    assert delete.await_args_list == [
        call(item["id"], user_id=owner, soft=False) for item in candidates
    ]
    pool.fetchval.assert_not_awaited()
    pool.execute.assert_not_awaited()  # No unguarded bulk-delete shortcut.


@pytest.mark.asyncio
async def test_garbage_collect_worker_uses_public_store_method() -> None:
    class FakeMemoryStore(MemoryStore):
        def __init__(self) -> None:
            self.calls = 0

        async def run_garbage_collect(self) -> dict[str, int]:
            self.calls += 1
            return {"scanned": 4, "deleted": 4}

    store = FakeMemoryStore()

    result = await garbage_collect({"store": store})

    assert result == {"scanned": 4, "deleted": 4}
    assert store.calls == 1


@pytest.mark.asyncio
async def test_garbage_collect_worker_skips_without_store() -> None:
    result = await garbage_collect({"store": object()})

    assert result == {"scanned": 0, "deleted": 0}


def test_worker_cron_jobs_include_memory_and_artifact_cleanup() -> None:
    worker_module = importlib.import_module("orchestrator.worker.worker")
    scheduled = {
        job.coroutine.__name__: (job.hour, job.minute)
        for job in worker_module.cron_jobs
        if getattr(job, "coroutine", None) is not None
    }

    assert scheduled["garbage_collect"] == (3, 0)
    assert scheduled["cleanup_generated_files"] == (3, 15)
    assert scheduled["cleanup_generated_images"] == (3, 30)


def test_worker_jobs_do_not_access_memory_store_private_pool() -> None:
    source = Path("orchestrator/worker/jobs.py").read_text()

    assert "._pool" not in source


def test_generated_file_cleanup_uses_repo_root_artifact_directory() -> None:
    source = Path("orchestrator/worker/jobs.py").read_text()

    assert 'parent.parent.parent / "data" / "generated_files"' in source


def test_artifact_cleanup_preserves_other_users_live_files_and_skips_symlinks(
    tmp_path: Path,
) -> None:
    user_a_dir = user_artifact_directory(tmp_path, uuid.uuid4(), create=True)
    user_b_dir = user_artifact_directory(tmp_path, uuid.uuid4(), create=True)
    old_user_a_file = user_a_dir / "old.csv"
    live_user_b_file = user_b_dir / "live.csv"
    legacy_file = tmp_path / "legacy.csv"
    outside_file = tmp_path.parent / f"outside-{uuid.uuid4().hex}.csv"
    old_user_a_file.write_text("old")
    live_user_b_file.write_text("live")
    legacy_file.write_text("legacy")
    outside_file.write_text("outside")
    (user_a_dir / "escape.csv").symlink_to(outside_file)

    old_timestamp = old_user_a_file.stat().st_mtime - (25 * 60 * 60)
    os.utime(old_user_a_file, (old_timestamp, old_timestamp))
    os.utime(legacy_file, (old_timestamp, old_timestamp))

    result = _cleanup_expired_artifacts(tmp_path, artifact_kind="file")

    assert result == {"scanned": 3, "deleted": 2}
    assert not old_user_a_file.exists()
    assert not legacy_file.exists()
    assert live_user_b_file.read_text() == "live"
    assert outside_file.read_text() == "outside"


@pytest.mark.asyncio
async def test_list_users_with_eligible_l1_memories_uses_store_pool() -> None:
    user_id = uuid.uuid4()
    pool = AsyncMock()
    pool.fetch.return_value = [{"user_id": user_id}]
    store = _memory_store(pool)

    result = await store.list_users_with_eligible_l1_memories()

    assert result == [user_id]
    sql = pool.fetch.await_args.args[0]
    assert "status = 'active'" in sql
    assert "tier = 'l1'" in sql
    assert "embedding IS NOT NULL" in sql
