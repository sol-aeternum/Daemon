"""The live provisioning maps against real startup, on every non-H migration.

The shared quarantine fixture keeps its broad synthetic acceptor. Here the
database carries every migration except restore history (H), A and W receive
ONLY the pinned maps from config/task_privileges (granted with the script's
own statement builder), and existing real-PostgreSQL startup, acceptance,
worker and exact-W suites run unchanged against it (their ``db`` resolves to
this module's fixture).

This database's migration history is deliberately incomplete, so the script's
``apply``/``verify`` refuse it; they are exercised on the real full chain in
test_task_role_provisioning_chain_postgres.py. No test-only bypass exists.
"""

import asyncio
import os
from uuid import uuid4

import pytest_asyncio

from scripts import provision_task_roles as provision
from tests.compute_metadata_support import (
    Bridge,
    Database,
    MIGRATION,
    ROOT,
    metadata_cluster as metadata_cluster,
)
from tests.exact_worker_privileges import ACCEPTOR_MAP, MAP

# Existing suites, collected again here against the exact-map fixture below.
from tests.test_exact_worker_privileges_postgres import *  # noqa: F403
from tests.test_inference_recovery_postgres import *  # noqa: F403
from tests.test_task_composition_postgres import *  # noqa: F403
from tests.test_task_startup_postgres import *  # noqa: F403


# Restore-history (H) objects make unselected startup refuse (#731), so the
# startup suites run on every other migration until H execution is decided.
HISTORY = {
    "056_restore_history_acceptance.sql",
    "057_restore_history_effects.sql",
    "064_artifact_restore_prepare.sql",
    "065_terminal_output_prepare.sql",
}
# The composition fixture records these two itself.
RECORDED_BY_SUITE = {"049_attempt_lifecycle.sql", "050_task_inference_recovery.sql"}


def _later_migrations():
    return [
        p
        for p in sorted((ROOT / "migrations").glob("*.sql"))
        if p.name > MIGRATION and p.name not in HISTORY
    ]


@pytest_asyncio.fixture
async def db(metadata_cluster):
    cluster = metadata_cluster
    name = "md_" + uuid4().hex
    cluster.prepare(name)
    for migration in _later_migrations():
        cluster.sql(
            "BEGIN;"
            + migration.read_text()
            + (
                ""
                if migration.name in RECORDED_BY_SUITE
                else f"\nINSERT INTO public._migrations(filename) VALUES('{migration.name}');"
            )
            + " COMMIT;",
            database=name,
            role="md_owner",
        )
    cluster.sql(
        "DO $$ BEGIN CREATE ROLE md_acceptor LOGIN NOSUPERUSER NOCREATEROLE; "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )
    for role, value in (("md_acceptor", ACCEPTOR_MAP), ("md_app", MAP)):
        cluster.sql(
            ";".join(provision.grant_statements(value, role)) + ";",
            database=name,
            role="md_owner",
        )
    cluster.record(
        "provisioning_maps",
        database=name,
        acceptor=ACCEPTOR_MAP["proposal_sha256"],
        worker=MAP["proposal_sha256"],
    )
    resource = cluster.resource / uuid4().hex[:6]
    resource.mkdir(mode=0o700)
    assert len(os.fsencode(resource / ".s.PGSQL.5432")) < 108
    bridge = Bridge(cluster, resource / ".s.PGSQL.5432")
    await bridge.start()
    database = Database(cluster, name, resource, bridge)
    try:
        yield database
    finally:
        await bridge.close()
        async with asyncio.timeout(3):
            while (
                cluster.sql(f"SELECT count(*) FROM pg_stat_activity WHERE datname='{name}'") != "0"
            ):
                await asyncio.sleep(0.01)
        cluster.sql(f'DROP DATABASE "{name}"')
        cluster.sql("CHECKPOINT")
        cluster.databases.remove(name)
