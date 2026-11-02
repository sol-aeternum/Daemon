"""Historical observation retains C1's original accepted/pinned-input identity."""

import pytest

from orchestrator.memory.store import MemoryStore
from orchestrator.services.restore_history.composition import HistoryAcceptance
from orchestrator.services.restore_history.effects_projection import HistoryProjection
from orchestrator.tasks.store import TaskStore
from tests.restore_history_support import KEY
from tests.test_history_effects_postgres import install
from tests.test_history_composition_postgres import deadline
from tests.test_history_observation_boundary_postgres import client, device
from tests.exact_worker_privileges import grant_exact_worker
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


@pytest.mark.asyncio
async def test_c1_received_input_task_reopens_without_republishing_pinned_inputs(c1_scenario):
    s = c1_scenario
    install(s.db)
    # C1's metadata-only W fixture intentionally has no ordinary auth/session
    # authority. Reuse the already proposed exact W map for the HTTP join;
    # never add protected table access, A capability or owner membership.
    grant_exact_worker(s.db.cluster, s.db.name)
    s.projection = HistoryProjection(s.identity, acceptor="md_acceptor", worker="md_app")
    options = submission(s.expected)
    accepted = await HistoryAcceptance(s.tasks, s.history, s.projection, KEY).accept(
        deadline=deadline(), **options
    )
    expected = {
        table: s.db.sql("SELECT count(*) FROM " + table)
        for table in (
            "tasks",
            "resource_versions",
            "resource_publications",
            "resource_references",
            "task_history_projection",
        )
    }
    async with s.db.pool() as pool:
        s.worker_pool = pool
        s.worker = TaskStore(pool, s.tasks._enc, MemoryStore(pool, s.tasks._enc))
        _, headers = await device(s, s.account)
        async with client(s) as http:
            response = await http.get(
                "/tasks/by-key/" + options["idempotency_key"], headers=headers
            )
        # The original C1 submission has its own stable key; lookup must find
        # its original task and leave every immutable publication unchanged.
        assert response.status_code == 200 and response.json()["id"] == str(accepted.task_id)
    assert {table: s.db.sql("SELECT count(*) FROM " + table) for table in expected} == expected
