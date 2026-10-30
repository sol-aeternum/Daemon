"""C1's existing synthetic input fixture after source-only H1 migration 056.

This proves schema coexistence, never restricted C1/H1 runtime composition.
"""

import pytest

from tests.compute_metadata_support import ROOT
from tests.test_history_acceptance_postgres import CANDIDATE
from tests.test_resource_inputs_postgres import (
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
@pytest.mark.parametrize("composed", [False, True])
async def test_c1_atomic_input_acceptance_after_056_does_not_activate_h1(input_scenario, composed):
    database, inputs, tasks, _, expected, *_ = input_scenario
    for path in (ROOT / "migrations/055_resource_presentations.sql", CANDIDATE):
        database.sql("BEGIN;" + path.read_text() + "COMMIT;")
    if composed:
        await inputs.bind()
    accept = tasks.accept if composed else inputs.accept
    args = submission(expected)
    first = await accept(**args)
    assert first.created
    replay = await accept(**submission(expected, pepper="rotated-pepper"))
    assert replay.task_id == first.task_id and not replay.created
    assert database.sql("SELECT count(*) FROM resource_versions") == "3"
    assert database.sql("SELECT count(*) FROM resource_publications") == "2"
    assert database.sql("SELECT count(*) FROM resource_references") == "3"
    assert database.sql("SELECT state FROM resource_input_batches") == "accepted"
    assert database.sql("SELECT count(*) FROM task_history_binding") == "0"
    assert database.sql("SELECT count(*) FROM task_history_acceptance") == "0"
