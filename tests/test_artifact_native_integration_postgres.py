"""Uninstalled native packet profile with explicit v2 artifact history.

Reuse the dependency's actual phase/fault assertions under the new profile;
this is synthetic PostgreSQL/POSIX qualification, not runtime recovery wiring.
"""

import time

import pytest
import pytest_asyncio

from orchestrator.services.restore_history.artifact_projection import ArtifactHistoryProjection
from orchestrator.services.restore_history.coordination import HistoryCoordinator
from orchestrator.services.restore_history.effects import HistoryEffects
from orchestrator.services.restore_history.model import ArtifactPrepare
from orchestrator.services.restore_history.projection import compare_projections
from tests import test_native_profile_postgres as n
from tests.test_artifact_history_postgres import SQL, read_facts

metadata_cluster = n.metadata_cluster
db = n.db
projection_db = n.projection_db
scenario = n.scenario
effects = n.effects
reserved = n.reserved
presenter = n.presenter
reservation_owner = n.reservation_owner
preparer = n.preparer
base_phase_owner = n.phase_owner
native = n.native


@pytest_asyncio.fixture
async def phase_owner(base_phase_owner):
    s = base_phase_owner
    # Healthy, fully drained test-only profile selection before any packet phase.
    await s.history.aclose(deadline=time.monotonic() + 3)
    s.db.sql("BEGIN;" + SQL.read_text() + "COMMIT;")
    s.db.sql(
        "GRANT EXECUTE ON FUNCTION task_history_artifact_project(bytea) TO reserved_p;"
        "GRANT EXECUTE ON FUNCTION task_history_output_page(uuid,bigint,text) TO md_app,md_acceptor;"
    )
    s.history = HistoryCoordinator(s.path, s.identity, s.ownership, profile="artifact-v2")
    await s.history.open(deadline=time.monotonic() + 3)
    try:
        s.projection = ArtifactHistoryProjection(
            s.identity, acceptor="md_acceptor", worker="md_app", publisher="reserved_p"
        )
        s.effects = HistoryEffects(s.worker, s.history, s.projection)
        yield s
    finally:
        await s.history.aclose(deadline=time.monotonic() + 3)


# Preserve all existing assertions, budgets and watchdogs from the reviewed
# native profile. These 39 cases now also run with the explicit artifact profile.
test_v2_native_original_results = (
    n.test_real_native_profile_retains_one_packet_and_reopens_original_results
)
test_v2_native_control_fences = n.test_native_profile_keeps_fences_and_truthful_known_control_status
test_v2_native_unknown_commit = n.test_native_profile_unknown_commit_never_establishes_or_retries
test_v2_native_cleanup = n.test_native_profile_drains_actual_reset_without_late_success
test_v2_native_shutdown = n.test_native_profile_shutdown_drains_before_dropping_context_and_packet


@pytest.mark.asyncio
async def test_native_packet_catalog_projects_the_original_immutable_binding(native):
    b, s = native, native.s
    await n.before(b, "present")
    packet = b.owner._entries[b.handle].packet
    async with s.history.operation(deadline=time.monotonic() + 2) as operation:
        state = await operation.verify()
    data = state.entries[-1].command.data
    assert type(data) is ArtifactPrepare
    assert (
        data.intent_id,
        data.resource_id,
        data.version_id,
        data.job_id,
        data.source_operation_id,
        data.original_attempt_id,
    ) == (
        packet.command.binding.intent_id,
        packet.command.binding.resource_id,
        packet.command.binding.version_id,
        b.context.job.job_id,
        b.context.job.operation_id,
        packet.command.binding.attempt_id,
    )
    facts = await read_facts(s)
    assert len(state.entries) == len(facts) == 2
    report = compare_projections(s.identity.history_id, state, facts, profile="artifact-v2")
    assert not (report.missing or report.extra or report.mismatched or report.restore_required)
