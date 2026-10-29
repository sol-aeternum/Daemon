"""Real integer arithmetic, accounting and row locks on owned PostgreSQL only.

Explicit --compute-metadata-postgres opt-in uses the existing cached-image
network-none harness. No application DSN, live credits or provider is accessed.
The synthetic W login gets only the proposed billing subset of ordinary rights.
"""

import asyncio
from types import SimpleNamespace

import asyncpg
import pytest
import pytest_asyncio

from db.video_credits import VideoCreditsDAL
from tests.test_compute_metadata_postgres import db as db, metadata_cluster as metadata_cluster


@pytest_asyncio.fixture(params=["md_app", "md_owner"])
async def ledger(db, request):
    db.sql(
        "GRANT SELECT,INSERT,UPDATE ON public.video_credit_balances TO md_app;"
        "GRANT SELECT,INSERT ON public.video_credit_transactions TO md_app;"
        "GRANT UPDATE(id) ON public.video_credit_transactions TO md_app"
    )
    async with db.connection() as owner:
        uid = await db.account(owner)
    pool = await asyncpg.create_pool(
        host=str(db.host),
        user=request.param,
        password="",  # Owned trust fixture, never a host credential lookup.
        database=db.name,
        ssl=False,
        min_size=0,
        max_size=4,
        timeout=2,
        command_timeout=3,
    )
    try:
        assert await pool.fetchval("SELECT CURRENT_USER=SESSION_USER")
        assert await pool.fetchval("SELECT CURRENT_USER") == request.param
        if request.param == "md_app":
            assert not await pool.fetchval("SELECT pg_has_role('md_app','md_owner','MEMBER')")
            assert not await pool.fetchval(
                "SELECT has_table_privilege(CURRENT_USER,'video_credit_transactions','UPDATE,DELETE')"
            )
            assert not await pool.fetchval(
                "SELECT has_column_privilege(CURRENT_USER,'video_credit_transactions','amount','UPDATE')"
            )
        yield SimpleNamespace(db=db, pool=pool, uid=uid, dal=VideoCreditsDAL(pool))
    finally:
        async with asyncio.timeout(0.7):
            await pool.close()


@pytest.mark.asyncio
async def test_debit_uses_real_integer_arithmetic_and_persists_one_spend(ledger):
    assert (await ledger.dal.credit_credits(ledger.uid, 20, "admin_grant", "Synthetic")).success
    result = await ledger.dal.debit_credits(ledger.uid, 7, "Synthetic debit", "fixture-video")
    assert result.success and result.new_balance == 13 and result.transaction_id is not None
    assert await ledger.dal.get_balance(ledger.uid) == 13
    row = await ledger.pool.fetchrow(
        "SELECT user_id,type,amount,description,reference_id FROM video_credit_transactions WHERE id=$1",
        result.transaction_id,
    )
    assert dict(row) == {
        "user_id": ledger.uid,
        "type": "spend",
        "amount": 7,
        "description": "Synthetic debit",
        "reference_id": "fixture-video",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [0, -1, 2**31])
async def test_rejected_debit_preserves_balance_and_ledger(ledger, amount):
    assert (await ledger.dal.credit_credits(ledger.uid, 10, "admin_grant", "Synthetic")).success
    result = await ledger.dal.debit_credits(ledger.uid, amount, "Rejected")
    assert not result.success and result.transaction_id is None
    assert result.message == ("Amount must be positive" if amount <= 0 else "Insufficient balance")
    assert await ledger.dal.get_balance(ledger.uid) == 10
    assert (
        await ledger.pool.fetchval(
            "SELECT count(*) FROM video_credit_transactions WHERE user_id=$1 AND type='spend'",
            ledger.uid,
        )
        == 0
    )


@pytest.mark.asyncio
async def test_missing_balance_remains_unfunded(ledger):
    result = await ledger.dal.debit_credits(ledger.uid, 1, "Unfunded")
    assert not result.success and result.message == "Insufficient balance"
    assert (
        await ledger.pool.fetchval(
            "SELECT count(*) FROM video_credit_balances WHERE user_id=$1", ledger.uid
        )
        == 0
    )
    assert (
        await ledger.pool.fetchval(
            "SELECT count(*) FROM video_credit_transactions WHERE user_id=$1", ledger.uid
        )
        == 0
    )


@pytest.mark.asyncio
async def test_largest_stored_integer_balance_can_be_debited_exactly(ledger):
    amount = 2**31 - 1
    assert (await ledger.dal.credit_credits(ledger.uid, amount, "admin_grant", "Synthetic")).success
    result = await ledger.dal.debit_credits(ledger.uid, amount, "Exact balance")
    assert result.success and result.new_balance == 0
    assert await ledger.dal.get_balance(ledger.uid) == 0


@pytest.mark.asyncio
async def test_concurrent_debits_cannot_overspend(ledger):
    assert (await ledger.dal.credit_credits(ledger.uid, 10, "admin_grant", "Synthetic")).success
    results = await asyncio.gather(
        ledger.dal.debit_credits(ledger.uid, 10, "First", "fixture-a"),
        ledger.dal.debit_credits(ledger.uid, 10, "Second", "fixture-b"),
    )
    assert sum(result.success for result in results) == 1
    assert {result.message for result in results} == {
        "Credits debited successfully",
        "Insufficient balance",
    }
    assert await ledger.dal.get_balance(ledger.uid) == 0
    assert (
        await ledger.pool.fetchval(
            "SELECT count(*) FROM video_credit_transactions WHERE user_id=$1 AND type='spend'",
            ledger.uid,
        )
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent", [False, True])
async def test_refund_remains_idempotent(ledger, concurrent):
    assert (await ledger.dal.credit_credits(ledger.uid, 10, "admin_grant", "Synthetic")).success
    debit = await ledger.dal.debit_credits(ledger.uid, 3, "Synthetic debit")
    assert debit.success and debit.transaction_id is not None
    if concurrent:
        refunds = await asyncio.gather(
            ledger.dal.refund_transaction(debit.transaction_id),
            ledger.dal.refund_transaction(debit.transaction_id),
        )
    else:
        refunds = [
            await ledger.dal.refund_transaction(debit.transaction_id),
            await ledger.dal.refund_transaction(debit.transaction_id),
        ]
    assert sum(result.success for result in refunds) == 1
    assert {result.message for result in refunds} == {
        "Transaction refunded successfully",
        "Transaction already refunded",
    }
    assert await ledger.dal.get_balance(ledger.uid) == 10
    assert (
        await ledger.pool.fetchval(
            "SELECT count(*) FROM video_credit_transactions WHERE type='refund' AND reference_id=$1",
            str(debit.transaction_id),
        )
        == 1
    )


@pytest.mark.asyncio
async def test_ledger_insert_failure_rolls_back_balance_debit(ledger):
    assert (await ledger.dal.credit_credits(ledger.uid, 10, "admin_grant", "Synthetic")).success
    ledger.db.sql(
        "CREATE FUNCTION fixture_refuse_spend() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN IF NEW.type='spend' THEN RAISE check_violation USING MESSAGE='fixture refusal'; "
        "END IF; RETURN NEW; END $$;"
        "CREATE TRIGGER fixture_refuse_spend BEFORE INSERT ON video_credit_transactions "
        "FOR EACH ROW EXECUTE FUNCTION fixture_refuse_spend()"
    )
    with pytest.raises(asyncpg.CheckViolationError, match="fixture refusal"):
        await ledger.dal.debit_credits(ledger.uid, 3, "Refused ledger")
    assert await ledger.dal.get_balance(ledger.uid) == 10
    assert (
        await ledger.pool.fetchval(
            "SELECT count(*) FROM video_credit_transactions WHERE user_id=$1 AND type='spend'",
            ledger.uid,
        )
        == 0
    )
