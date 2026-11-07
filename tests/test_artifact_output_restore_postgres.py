"""Post-catalog v2 restore characterization, never recovery/replay permission.

The actual H/native joins publish two partial checkpoints and a terminal result.
Owned whole-SQL restores show precisely which outputs the catalog fact cannot
protect. All reads and fault sentinels use synthetic data and existing fixtures.
"""

import json

import pytest

from orchestrator.services.account_control.guard import CapturedGeneration
from orchestrator.services.restore_history.model import ArtifactPrepare, HistoryError
from orchestrator.tasks.states import TaskStatus
from tests import test_artifact_history_postgres as base
from tests import test_history_output_phases_postgres as q
from tests.restore_history_support import KEY
from tests.test_artifact_restore_boundary_postgres import (
    checkpoint,
    data_facts,
    file_facts,
    parity,
    restored,
)

metadata_cluster = base.metadata_cluster
db = base.db
projection_db = base.projection_db
scenario = base.scenario
effects = base.effects
reserved = base.reserved
presenter = base.presenter
reservation_owner = base.reservation_owner
preparer = base.preparer
phase_owner = base.phase_owner
artifact_owner = base.artifact_owner

CUTS = ("catalog", "presentation", "partial", "newer_partial", "terminal")
PARTIALS = {
    "partial": (3, "synthetic first partial checkpoint"),
    "newer_partial": (7, "synthetic later partial checkpoint"),
}


async def publish(s, name):
    if name in PARTIALS:
        sequence, content = PARTIALS[name]
        return await s.effects.write_partial(
            s.claim.task_id,
            s.claim.epoch,
            binding=s.claim,
            content=content,
            delta_seq=sequence,
            deadline=q.r.p.r.deadline(),
        )
    return await q.phase(s, name)


async def observe(s):
    task = await s.worker.task_for_key(s.account, "stable-client-key")
    assert task is not None and task.task_id == s.claim.task_id
    loaded = await s.native_store.read(
        CapturedGeneration(s.account, 0),
        s.command.binding.resource_id,
        s.command.binding.version_id,
    )
    assert loaded.content == q.r.p.r.CONTENT
    async with s.db.connection() as conn:
        row = await conn.fetchrow(
            "SELECT t.content_delta_seq,m.content,m.metadata FROM tasks t "
            "JOIN messages m ON m.id=t.result_message_id WHERE t.id=$1",
            s.claim.task_id,
        )
        cards = await conn.fetchval("SELECT count(*) FROM resource_presentations")
    assert row is not None
    metadata = json.loads(row["metadata"])
    return {
        "task_id": task.task_id,
        "status": task.status,
        "delta_seq": row["content_delta_seq"],
        "content": s.tasks._enc.decrypt(row["content"]),
        "terminal_status": metadata.get("terminal_status"),
        "cards": cards,
        "publication": loaded.publication,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("cut", CUTS)
async def test_v2_catalog_parity_cannot_establish_later_output_survival(
    artifact_owner, tmp_path, cut
):
    s = artifact_owner
    for name in CUTS[: CUTS.index(cut) + 1]:
        await publish(s, name)
    earlier = await observe(s)
    saved = await checkpoint(s, tmp_path)
    history = {name: (s.path / name).read_bytes() for name in ("LOG", "HEAD")}
    for name in CUTS[CUTS.index(cut) + 1 :]:
        await publish(s, name)
    latest = await observe(s)
    assert latest["status"] is TaskStatus.COMPLETED
    assert latest["content"] == "synthetic final" and latest["delta_seq"] == 7
    assert latest["terminal_status"] == "complete" and latest["cards"] == 1
    assert history == {name: (s.path / name).read_bytes() for name in history}
    newer_files = file_facts(s.root)
    # The healthy original owner is fully closed by this existing fixture.
    # A fresh read below cannot clear a failed writer or authorize execution.
    async with restored(s, saved, projection=s.projection) as fresh:
        before = data_facts(fresh)
        state = await parity(fresh)
        assert len(state.entries) == 2 and type(state.entries[-1].command.data) is ArtifactPrepare
        observed = await observe(fresh)
        assert observed == earlier
        if cut == "terminal":
            assert observed == latest  # Consistent positive control only.
        else:
            assert observed != latest and observed["status"] is TaskStatus.RUNNING
            assert observed["terminal_status"] != "complete"
            assert observed["cards"] == (0 if cut == "catalog" else 1)
            assert observed["delta_seq"] == (PARTIALS[cut][0] if cut in PARTIALS else 0)
            if cut in PARTIALS:
                assert observed["content"] == PARTIALS[cut][1]
        # Observe the gap without exercising replay, adoption, reconstruction,
        # fresh assignments or application writes against the restored target.
        assert data_facts(fresh) == before and file_facts(fresh.root) == newer_files
        assert history == {name: (fresh.path / name).read_bytes() for name in history}
        assert not fresh.effects._blocked and not fresh.history._blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("independent", ["stop", "send"])
@pytest.mark.parametrize("next_output", ["presentation", "partial", "terminal"])
async def test_v2_restore_keeps_independent_control_ahead_of_all_later_output(
    artifact_owner, tmp_path, monkeypatch, independent, next_output
):
    s = artifact_owner
    await publish(s, "catalog")
    saved = await checkpoint(s, tmp_path)
    if independent == "stop":
        assert (
            await s.effects.request_cancel(
                s.account, s.claim.task_id, key=KEY, deadline=q.r.p.r.deadline()
            )
        ).accepted
    else:
        await s.effects.mark(
            s.attempt,
            s.reservation.id,
            s.invocation,
            effect_kind="completion",
            deadline=q.r.p.r.deadline(),
        )
    async with restored(s, saved, projection=s.projection) as fresh:
        before, files = data_facts(fresh), file_facts(fresh.root)

        async def forbidden(*args, **kwargs):
            pytest.fail("native output ran despite missing independent control projection")

        monkeypatch.setattr(fresh.worker, "write_partial", forbidden)
        monkeypatch.setattr(fresh.worker, "complete", forbidden)
        monkeypatch.setattr(fresh.publisher, "_present_reserved", forbidden)
        with pytest.raises(HistoryError, match="effects_reconciliation_required"):
            await publish(fresh, next_output)
        assert fresh.effects._blocked and fresh.history._blocked
        with pytest.raises(HistoryError, match="blocked"):
            await publish(fresh, next_output)
        assert data_facts(fresh) == before and file_facts(fresh.root) == files
