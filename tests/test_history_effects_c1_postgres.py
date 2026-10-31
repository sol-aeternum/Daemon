"""057 full-parity profile retains C1's original atomic pinned-input boundary."""

import pytest

from orchestrator.resource_store import ResourceError
from orchestrator.services.restore_history.composition import HistoryAcceptance
from orchestrator.services.restore_history.effects_projection import HistoryProjection
from tests.restore_history_support import KEY
from tests.test_history_composition_c1_postgres import (
    c1_scenario as c1_scenario,
    db as db,
    input_scenario as input_scenario,
    lifecycle_db as lifecycle_db,
    metadata_cluster as metadata_cluster,
    reservation_key as reservation_key,
    reservation_scenario as reservation_scenario,
    resource_key as resource_key,
    resource_scenario as resource_scenario,
    submission,
)
from tests.test_history_composition_postgres import deadline, entries
from tests.test_history_effects_postgres import install


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_combined_history_profile_preserves_c1_atomic_pinned_input_commit(
    c1_scenario, monkeypatch, fail
):
    s = c1_scenario
    install(s.db)
    s.projection = HistoryProjection(s.identity, acceptor="md_acceptor", worker="md_app")
    s.accept = HistoryAcceptance(s.tasks, s.history, s.projection, KEY)
    initial = int(s.db.sql("SELECT count(*) FROM tasks"))
    native = s.projection.stage

    async def stage(conn, entry, *, deadline):
        assert await conn.fetchval("SELECT count(*) FROM resource_versions") == 3
        assert await conn.fetchval("SELECT state FROM resource_input_batches") == "accepted"
        result = await native(conn, entry, deadline=deadline)
        if fail:
            raise ConnectionError("synthetic combined-profile C1 rollback")
        return result

    monkeypatch.setattr(s.projection, "stage", stage)
    if fail:
        with pytest.raises(ResourceError):
            await s.accept.accept(deadline=deadline(), **submission(s.expected))
    else:
        assert (await s.accept.accept(deadline=deadline(), **submission(s.expected))).created
    assert len(entries(s)) == 1
    assert int(s.db.sql("SELECT count(*) FROM tasks")) == initial + int(not fail)
    for table, expected in (
        ("resource_versions", 3),
        ("resource_publications", 2),
        ("resource_references", 3),
        ("task_history_acceptance", 1),
        ("task_history_projection", 1),
    ):
        assert int(s.db.sql("SELECT count(*) FROM " + table)) == (0 if fail else expected)
