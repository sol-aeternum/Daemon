"""Qualify the PG16 insertion discriminator on the real disposable worker role."""

from uuid import uuid4

import pytest

from tests.test_inference_recovery_postgres import db as db, metadata_cluster as metadata_cluster


@pytest.mark.asyncio
async def test_source_pin_distinguishes_native_insertion_from_worker_row_update(db):
    async with db.connection() as owner:
        user = await db.account(owner)
        conversation = await owner.fetchval(
            "INSERT INTO conversations(user_id,pipeline) VALUES($1,'cloud') RETURNING id", user
        )
    stats = (
        "SELECT current_setting('track_counts')='on' AS tracked, "
        "pg_stat_get_xact_tuples_inserted('public.web_snapshots'::regclass) AS inserts, "
        "pg_stat_get_xact_tuples_updated('public.web_snapshots'::regclass) AS updates"
    )
    snapshot = uuid4()
    fresh_xid = (
        "SELECT xmin::text::numeric = mod(pg_current_xact_id()::text::numeric,4294967296) "
        "FROM public.web_snapshots WHERE id=$1"
    )
    async with db.connection("md_app") as worker, worker.transaction():
        assert not await worker.fetchval(
            "SELECT has_parameter_privilege(current_user,'track_counts','SET')"
        )
        await worker.execute(
            "INSERT INTO public.web_snapshots(id,user_id,conversation_id,metadata_encrypted,"
            "content_encrypted,identity_fingerprint,content_fingerprint,content_chars,"
            "content_bytes,stored_bytes,retrieved_at,expires_at) VALUES"
            "($1,$2,$3,'fixture','fixture',repeat('0',64),repeat('1',64),1,1,2,now(),now()+interval '1 day')",
            snapshot,
            user,
            conversation,
        )
        assert await worker.fetchval(fresh_xid, snapshot)
        assert dict(await worker.fetchrow(stats)) == {"tracked": True, "inserts": 1, "updates": 0}
    # Use another backend so this tests only the current transaction. A native
    # row lock leaves UPDATE counters unchanged; the actual column UPDATE does not.
    async with db.connection("md_app") as worker, worker.transaction():
        await worker.fetchrow("SELECT id FROM public.web_snapshots WHERE id=$1 FOR SHARE", snapshot)
        assert not await worker.fetchval(fresh_xid, snapshot)
        assert (await worker.fetchrow(stats))["updates"] == 0
        await worker.execute("UPDATE public.web_snapshots SET id=id WHERE id=$1", snapshot)
        assert await worker.fetchval(fresh_xid, snapshot)
        assert (await worker.fetchrow(stats))["updates"] == 1
        async with worker.transaction():
            await worker.execute("UPDATE public.web_snapshots SET id=id WHERE id=$1", snapshot)
            assert (await worker.fetchrow(stats))["updates"] == 2
        assert (await worker.fetchrow(stats))["updates"] == 2
