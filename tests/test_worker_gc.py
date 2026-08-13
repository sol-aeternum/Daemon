from __future__ import annotations

import importlib
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from unittest.mock import AsyncMock, MagicMock

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
    pool = AsyncMock()
    pool.fetchval.return_value = 3
    pool.execute.return_value = "DELETE 2"
    store = _memory_store(pool)

    result = await store.run_garbage_collect()

    assert result == {"scanned": 3, "deleted": 2}
    assert pool.fetchval.await_count == 1
    assert pool.execute.await_count == 1
    count_sql = pool.fetchval.await_args.args[0]
    delete_sql = pool.execute.await_args.args[0]
    for status in ("inactive", "rejected", "pending", "deleted"):
        assert f"status = '{status}'" in count_sql
        assert f"status = '{status}'" in delete_sql


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


@pytest.mark.asyncio
async def test_delete_skill_projection_uses_projection_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = AsyncMock()
    store = _memory_store(pool)
    calls: dict[str, Any] = {}

    class FakeProjectionStore:
        def __init__(self, db_pool: Any) -> None:
            calls["pool"] = db_pool

        async def delete_projection(self, skill_id: str) -> bool:
            calls["skill_id"] = skill_id
            return True

    monkeypatch.setattr(
        "orchestrator.skills_projection.SkillProjectionStore",
        FakeProjectionStore,
    )

    result = await store.delete_skill_projection("skill-delete")

    assert result is True
    assert calls == {"pool": pool, "skill_id": "skill-delete"}
