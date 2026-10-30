"""Real TCP-disabled synthetic PostgreSQL attach checks; no benchmark database."""

from typing import Any

import asyncpg
import pytest

from orchestrator.eval import campaign_guard as guard, campaign_retry as retry
from orchestrator.eval import campaign_restore as restore
from orchestrator.eval.bench_db import question_user_id
from tests.test_lme_campaign_launch import pg16 as pg16, prepared


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "foreign", "fingerprint", "catalogue"])
async def test_native_attach_preserves_schema_and_restore_count(
    pg16: Any, tmp_path: Any, monkeypatch: Any, failure: str | None
) -> None:
    # Filesystem lineage/accounting have separate synthetic tests. Here the real
    # PG16 fixture exercises admission, exact PID ownership, catalogue/fingerprint
    # verification and pool cleanup, without weakening the production checks.
    async with prepared(pg16, tmp_path, monkeypatch) as (campaign, _, db, control_dsn):
        monkeypatch.setattr(retry, "enabled", lambda _: True)
        monkeypatch.setattr(retry, "validate_attribution", lambda *_, **__: None)
        campaign.state.update(
            arm="A1",
            arm_index=1,
            restore_attempts=1,
            restores=[{"historical": True}],
            imported_restore={"historical": True},
            receipts=[{}] * 195,
            attach_pending=None,
            pending=None,
        )
        connection = await asyncpg.connect(db)
        control = await asyncpg.connect(control_dsn)
        try:
            before_oid = await connection.fetchval("SELECT 'memories'::regclass::oid")
            if failure == "fingerprint":
                await connection.execute(
                    "UPDATE memories SET user_id=$1",
                    question_user_id(campaign.plan["question_ids"][0]),
                )
            elif failure == "catalogue":
                await connection.execute("CREATE TABLE unexpected_fixture (id integer)")
            if failure != "foreign":
                await connection.close()
            await control.execute("ALTER DATABASE daemon_lme_bench ALLOW_CONNECTIONS false")
            if failure is None:
                async with restore.attached_pool(campaign) as pool:
                    assert await pool.fetchval("SELECT 'memories'::regclass::oid") == before_oid
                    assert await pool.fetchval("SELECT count(*) FROM memories") == 1
                    assert campaign.state["restore_attempts"] == 1
                    assert campaign.state["restores"] == [{"historical": True}]
                    assert campaign.state["attach_pending"] is None
                    assert len(campaign.state["attach_receipt"]["owned_pids"]) == 10
                    assert (await restore.target_identity(control))["datallowconn"] is False
                    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
                        await asyncpg.connect(db, timeout=2)
                    await pool.close()  # Canonical _phase owns this close in production.
            else:
                with pytest.raises(guard.CampaignStopped):
                    async with restore.attached_pool(campaign):
                        pytest.fail("Changed/foreign fixture admitted")
                if failure == "foreign":
                    assert (
                        await connection.fetchval("SELECT 'memories'::regclass::oid") == before_oid
                    )
                    assert await connection.fetchval("SELECT count(*) FROM memories") == 1
                    await connection.close()
            assert campaign.state["restore_attempts"] == 1
            assert (await restore.target_identity(control))["datallowconn"] is False
            await restore.verify_sessions(control, set())
        finally:
            await connection.close()
            await control.close()
