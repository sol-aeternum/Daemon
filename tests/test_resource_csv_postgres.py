"""Concrete deterministic adapter, actual restricted R0 and presentation publication."""

import asyncpg
from typing import Any
import pytest

from orchestrator.artifact_reservations import JobBinding
from orchestrator.resource_csv import ResourceCSV, encode_csv
from orchestrator.resource_store import ResourceError
from tests import test_resource_catalog_postgres as c
from tests.compute_metadata_support import metadata_cluster as metadata_cluster

lifecycle_db = c.lifecycle_db
db = c.db
reservation_scenario = c.reservation_scenario
reservation_key = c.reservation_key
resource_scenario = c.resource_scenario
resource_key = c.resource_key
input_scenario = c.input_scenario
catalog_scenario = c.catalog_scenario


def test_csv_exact_bounded_encoding():
    assert (
        encode_csv([["header", "quoted,field"], ["unicode", "\u00e9"]], byte_limit=100)
        == b'header,"quoted,field"\nunicode,\xc3\xa9\n'
    )
    invalid: Any = ([["x" * 101]], [["x"]] * 10001, [[False]])
    for rows in invalid:
        with pytest.raises(ResourceError):
            encode_csv(rows, byte_limit=100)


@pytest.mark.asyncio
async def test_actual_adapter_replay_and_expired_existing_presentation(catalog_scenario):
    db, catalog, presentations, _, expected, _, scenario = catalog_scenario
    from uuid import uuid4

    ident = scenario[2]
    pool = await asyncpg.create_pool(
        host=str(db.host),
        user="md_app",
        password="",
        database=db.name,
        ssl=False,
        min_size=0,
        max_size=2,
    )
    try:
        adapter = ResourceCSV(pool, presentations, byte_limit=1000)
        job = JobBinding(uuid4(), ident.account_id, ident.generation, ident.task_id, uuid4())
        first = await adapter.publish(ident, job, 0, [["header"], ["value"]], "value.csv")
        assert (
            await catalog.store.read(expected, first.resource_id, first.version_id)
        ).content == b"header\nvalue\n"
        assert (await presentations.page(expected, ident.task_id))["items"][0]["items"][0][
            "version_id"
        ] == str(first.version_id)
        assert await adapter.publish(ident, job, 0, [["header"], ["value"]], "value.csv") == first
        db.sql("UPDATE tasks SET lease_expires_at=clock_timestamp()-interval '1 second'")
        assert await adapter.publish(ident, job, 0, [["header"], ["value"]], "value.csv") == first
        with pytest.raises(ResourceError, match="conflict"):
            await adapter.publish(ident, job, 0, [["changed"]], "value.csv")
        assert db.sql("SELECT count(*) FROM resource_versions") == "1"
    finally:
        await pool.close()
