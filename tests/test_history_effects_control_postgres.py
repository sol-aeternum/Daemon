"""Negative Stop evidence preserves existing native control eligibility."""

from dataclasses import replace
from uuid import uuid4

import asyncpg
import pytest

from tests.restore_history_support import KEY
from tests.test_history_effects_postgres import (
    db as db,
    effects as effects,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    running,
    scenario as scenario,
    deadline,
    entries,
)
from orchestrator.services.restore_history.model import Command, StopIntent, encode_entry


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["generation", "suspension"])
async def test_stop_keeps_native_control_eligibility_after_new_work_is_refused(effects, change):
    s = effects
    await running(s)
    if change == "generation":
        s.db.sql("UPDATE entitlement_accounts SET content_generation=content_generation+1")
    else:
        s.db.sql("UPDATE entitlement_accounts SET status='suspended'")
    # Native negative control remains available; this is not fresh content/send
    # permission. The H join must not invent a stricter positive-admission guard.
    assert (await s.worker.request_cancel(s.account, s.accepted.task_id)).accepted
    outcome = await s.effects.request_cancel(
        s.account, s.accepted.task_id, key=KEY, deadline=deadline()
    )
    assert outcome.accepted
    recorded = entries(s)
    assert len(recorded) == 2 and isinstance(recorded[1].command.data, StopIntent)
    assert recorded[1].command.data.account_generation == 0
    async with s.db.connection() as conn:
        assert await conn.fetchval("SELECT count(*) FROM task_history_projection") == 2
    # Current generation must not replace the original acceptance/coverage
    # generation just because negative Stop remains available.
    entry = recorded[1]
    wrong = replace(entry.command.data, account_generation=1)
    frame = encode_entry(entry.history_id, 3, entry.digest, Command(uuid4(), wrong))
    async with s.db.connection("md_app") as conn:
        with pytest.raises(asyncpg.PostgresError, match="task_history_refused"):
            await conn.fetchrow("SELECT * FROM task_history_stop_project($1)", frame)
