"""Diagnose the next P presentation boundary; no runtime composition or repair."""

from datetime import datetime
import json
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest
import pytest_asyncio

from orchestrator.resource_store import ResourceError
from tests import test_resource_output_presentations_postgres as p
from tests.test_resource_output_catalog_postgres import SIGNATURES as CATALOG_SIGNATURES

metadata_cluster = p.metadata_cluster
lifecycle_db = p.lifecycle_db
db = p.db
reservation_scenario = p.reservation_scenario
reservation_key = p.reservation_key
resource_scenario = p.resource_scenario
resource_key = p.resource_key
input_scenario = p.input_scenario
catalog_scenario = p.catalog_scenario
base_output = p.output


@pytest_asyncio.fixture
async def output(base_output):
    """Use the full current six-entrypoint P map, not just the 058 subset."""
    database, conn, *_ = base_output
    root = Path(__file__).resolve().parents[1]
    for name in (
        "059_resource_output_catalog.sql",
        "060_resource_output_namespace_inventory.sql",
    ):
        database.sql("BEGIN;" + (root / "migrations" / name).read_text() + "COMMIT;")
    database.sql(
        ";".join(
            "GRANT EXECUTE ON FUNCTION " + signature + " TO output_p"
            for signature in (
                *CATALOG_SIGNATURES,
                "public.resource_output_namespace_inventory(pg_catalog.uuid,pg_catalog.int4)",
            )
        )
    )
    for signature in (
        *p.SIGNATURES,
        *CATALOG_SIGNATURES,
        "public.resource_output_namespace_inventory(pg_catalog.uuid,pg_catalog.int4)",
    ):
        assert await conn.fetchval(
            "SELECT has_function_privilege(current_user,$1,'EXECUTE')", signature
        )
    yield base_output


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["ciphertext", "encrypted_generation", "encrypted_ordinal"])
async def test_p_snapshot_cannot_authenticate_native_pinned_reference(output, damage):
    database, conn, identity, pins, catalog, presentations, _, expected, _ = output
    presentation_id = await presentations.record(identity, pins)
    assert (await presentations.page(expected, identity.task_id))["items"][0]["items"] == pins
    before = await p.snapshot(conn, identity)
    assert set(before) == {"task", "records", "observed_at"}
    assert len(before["records"]) == 1
    assert UUID(before["records"][0]["presentation_id"]) == presentation_id

    # Alter only a synthetic reference's encrypted provenance, retaining every
    # SQL pin/scope field and the original authenticated presentation envelope.
    async with database.connection() as owner:
        reference = await owner.fetchrow(
            "SELECT * FROM resource_references WHERE account_id=$1 AND version_id=$2 AND role='output'",
            expected.account_id,
            UUID(pins[0]["version_id"]),
        )
        assert reference is not None
        if damage == "ciphertext":
            token = "synthetic-invalid-provenance"
        else:
            value = catalog.store._open(reference["provenance_ciphertext"])
            field = "generation" if damage == "encrypted_generation" else "ordinal"
            value[field] += 1
            token = catalog.store._seal(value)
        await owner.execute(
            "UPDATE resource_references SET provenance_ciphertext=$1 WHERE account_id=$2 AND reference_id=$3",
            token,
            expected.account_id,
            reference["reference_id"],
        )

    after = await p.snapshot(conn, identity)
    assert before["task"] == after["task"]
    assert before["records"] == after["records"]

    # Even exact SQL replay only checks the reference's plaintext scope. It
    # cannot substitute for Python authentication of the encrypted provenance.
    row = after["records"][0]
    command = (
        UUID(row["presentation_id"]),
        datetime.fromisoformat(row["created_at"]),
        row["envelope_ciphertext"],
    )
    async with conn.transaction():
        replay = await p.insert(conn, identity, pins, command)
        assert replay == {
            "presentation_id": str(presentation_id),
            "inserted": False,
            "outcome": "pending_commit",
        }

    with pytest.raises(ResourceError):
        await presentations.page(expected, identity.task_id)


@pytest.mark.asyncio
async def test_p_has_no_direct_reference_ciphertext_read(output):
    _, conn, *_ = output
    assert not await conn.fetchval(
        "SELECT has_table_privilege(current_user,'public.resource_references','SELECT')"
    )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with conn.transaction():
            await conn.fetch("SELECT provenance_ciphertext FROM public.resource_references")


@pytest.mark.asyncio
async def test_six_capability_p_can_read_provenance_with_original_intent(output):
    database, conn, identity, pins, catalog, presentations, _, expected, _ = output
    await presentations.record(identity, pins)
    async with database.connection() as owner:
        intent_id = await owner.fetchval(
            "SELECT intent_id FROM resource_publications WHERE account_id=$1 AND version_id=$2",
            expected.account_id,
            UUID(pins[0]["version_id"]),
        )
    assert intent_id is not None and intent_id != UUID(pins[0]["version_id"])
    payload = await conn.fetchval(
        "SELECT public.resource_output_catalog_snapshot($1::pg_catalog.jsonb,$2)",
        identity.wire(),
        intent_id,
    )
    observed = json.loads(payload)
    assert len(observed["output_references"]) == 1
    reference = observed["output_references"][0]
    assert catalog._reference(reference, expected)["source_deleted"] is False
    assert reference["version_id"] == pins[0]["version_id"]
    presentation = await p.snapshot(conn, identity)
    assert set(presentation) == {"task", "records", "observed_at"}
    envelope = catalog.store._open(presentation["records"][0]["envelope_ciphertext"])
    assert envelope["envelope"]["items"] == pins
    assert set(pins[0]) == {"resource_id", "version_id"}


@pytest.mark.asyncio
async def test_version_pin_is_not_a_reservation_lookup_identity(output):
    _, conn, identity, pins, *_ = output
    with pytest.raises(asyncpg.PostgresError):
        async with conn.transaction():
            await conn.fetchval(
                "SELECT public.resource_output_catalog_snapshot($1::pg_catalog.jsonb,$2)",
                identity.wire(),
                UUID(pins[0]["version_id"]),
            )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with conn.transaction():
            await conn.fetch("SELECT intent_id,version_id FROM public.resource_publications")
