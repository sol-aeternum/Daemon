"""The live provisioning maps on the FULL migration chain, against real startup.

The shared quarantine fixture keeps its broad synthetic acceptor. Here the
database carries every migration, A and W receive ONLY the pinned maps from
config/task_privileges, and existing real-PostgreSQL startup, acceptance,
worker and exact-W suites run unchanged against it (their ``db`` resolves to
this module's fixture). A separate test runs the script's own ``apply`` and
``verify`` as a superuser, the way the live deployment will.
"""

import asyncio
import os
from uuid import uuid4

import pytest
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


PASSWORDS = ("Fixture-acceptor-0001", "Fixture-worker-00002")


class _Undo(Exception):
    """Roll back a deliberate drift after observing the refusal."""


@pytest.mark.asyncio
async def test_apply_creates_exact_roles_and_verify_detects_drift(db):
    acceptor, worker = "rp_" + uuid4().hex[:8], "rp_" + uuid4().hex[:8]
    try:
        async with db.connection("postgres") as owner:
            await provision.apply(
                owner,
                acceptor,
                worker,
                provision.scram_verifier(PASSWORDS[0]),
                provision.scram_verifier(PASSWORDS[1]),
            )
            stored = await owner.fetch(
                "SELECT rolname, left(rolpassword, 19) AS kind FROM pg_authid "
                "WHERE rolname = ANY($1::name[]) ORDER BY 1",
                [acceptor, worker],
            )
            assert {row["kind"] for row in stored} == {"SCRAM-SHA-256$4096:"}
            async with owner.transaction():
                await provision.verify(owner, ACCEPTOR_MAP, MAP, acceptor, worker)
            # A second apply never adopts or alters existing roles.
            with pytest.raises(provision.ProvisionError, match="role_already_exists"):
                await provision.apply(
                    owner,
                    acceptor,
                    worker,
                    provision.scram_verifier(PASSWORDS[0]),
                    provision.scram_verifier(PASSWORDS[1]),
                )
            for drift, code in (
                (f"GRANT DELETE ON public.messages TO {acceptor}", "table_privileges_differ"),
                (f"GRANT SELECT ON public._migrations TO {worker}", "table_privileges_differ"),
                (
                    f"GRANT INSERT(task_id) ON public.task_inference_recovery TO {worker}",
                    "column_privileges_differ",
                ),
                (f"GRANT {worker} TO {acceptor}", "role_attributes_refused"),
            ):
                with pytest.raises(_Undo):
                    async with owner.transaction():
                        await owner.execute(drift)
                        with pytest.raises(provision.ProvisionError, match=code):
                            await provision.verify(owner, ACCEPTOR_MAP, MAP, acceptor, worker)
                        raise _Undo
            async with owner.transaction():
                await provision.verify(owner, ACCEPTOR_MAP, MAP, acceptor, worker)
    finally:
        async with db.connection("postgres") as owner:
            for role in (acceptor, worker):
                await owner.execute(
                    f"DO $$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN "
                    f"EXECUTE 'DROP OWNED BY {role}'; EXECUTE 'DROP ROLE {role}'; END IF; END $$"
                )


@pytest.mark.asyncio
async def test_apply_refuses_partial_schema_without_creating_roles(db):
    acceptor, worker = "rp_" + uuid4().hex[:8], "rp_" + uuid4().hex[:8]
    async with db.connection("postgres") as owner:
        latest = provision.expected_latest_migration()
        await owner.execute("DELETE FROM public._migrations WHERE filename=$1", latest)
        try:
            with pytest.raises(provision.ProvisionError, match="schema_not_at_latest_migration"):
                await provision.apply(
                    owner,
                    acceptor,
                    worker,
                    provision.scram_verifier(PASSWORDS[0]),
                    provision.scram_verifier(PASSWORDS[1]),
                )
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM pg_roles WHERE rolname = ANY($1::name[])",
                    [acceptor, worker],
                )
                == 0
            )
        finally:
            await owner.execute("INSERT INTO public._migrations(filename) VALUES($1)", latest)
