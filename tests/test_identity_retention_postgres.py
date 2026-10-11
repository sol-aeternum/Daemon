"""P0.5 persistence/races against real isolated PostgreSQL, never the app DSN."""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from zoneinfo import ZoneInfo

import asyncpg
import pytest

from orchestrator.services.identity.account_service import AccountService
from orchestrator.services.identity.email_challenge import EmailChallengeInvalid, EmailChallengeRow
from orchestrator.services.identity.provenance import (
    attribute_email_challenge,
    lock_consumed_email_challenge,
)
from orchestrator.services.identity.retention import sweep_identity_records
from tests.durable_tasks_support import DSN_ENV, Env, MIGRATIONS, durable_env_fixture

env = durable_env_fixture()
NOW = datetime(2035, 2, 10, 12, tzinfo=timezone.utc)
DAY = timedelta(days=1)


async def clock(conn, now: datetime) -> None:
    # Only disposable schema function implementation; no production override.
    await conn.execute(
        "CREATE OR REPLACE FUNCTION identity_provenance_now() RETURNS TIMESTAMPTZ "
        f"AS $$ SELECT '{now.isoformat()}'::timestamptz $$ LANGUAGE sql VOLATILE"
    )


async def owner(conn, user: uuid.UUID, email: str) -> uuid.UUID:
    await conn.execute("UPDATE users SET normalized_email=$2 WHERE id=$1", user, email)
    return await conn.fetchval(
        "SELECT id FROM account_email_ownership WHERE user_id=$1 AND ended_at IS NULL", user
    )


async def record(
    conn, table: str, anchor: datetime, *, email="shared@example.test", consumed=False
):
    if table == "identity_audit_log":
        return await conn.fetchval(
            "INSERT INTO identity_audit_log(event_type,normalized_email,created_at) "
            "VALUES('email_challenge_issued',$1,$2) RETURNING id",
            email,
            anchor,
        )
    if table == "email_challenges":
        return await conn.fetchval(
            "INSERT INTO email_challenges(normalized_email,code_verifier_hash,expires_at,consumed_at) "
            "VALUES($1,'fictional',$2,$3) RETURNING id",
            email,
            NOW + DAY if consumed else anchor,
            anchor if consumed else None,
        )
    if table == "google_nonce_challenges":
        return await conn.fetchval(
            "INSERT INTO google_nonce_challenges(nonce_verifier_hash,expires_at,consumed_at) "
            "VALUES($1,$2,$3) RETURNING id",
            uuid.uuid4().hex,
            NOW + DAY if consumed else anchor,
            anchor if consumed else None,
        )
    return await conn.fetchval(
        "INSERT INTO signup_invites(normalized_email,token_verifier_hash,status,expires_at,consumed_at) "
        "VALUES($1,$2,$3,$4,$5) RETURNING id",
        email + uuid.uuid4().hex,
        uuid.uuid4().hex,
        "consumed" if consumed else "expired",
        NOW + DAY if consumed else anchor,
        anchor if consumed else None,
    )


async def blocked(observer, waiter: int, blocker: int) -> None:
    async with asyncio.timeout(5):
        while not await observer.fetchval("SELECT $2 = ANY(pg_blocking_pids($1))", waiter, blocker):
            await asyncio.sleep(0)  # poll actual lock evidence, no timing assumption


@pytest.mark.asyncio
async def test_observation_baseline_and_legacy_unknowns_are_repeat_safe() -> None:
    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        pytest.skip(f"requires disposable {DSN_ENV}")
    conn = await asyncpg.connect(dsn)
    schema = "identity_legacy_" + uuid.uuid4().hex
    try:
        for ext in ("pgcrypto", "vector"):
            await conn.execute(f"CREATE EXTENSION IF NOT EXISTS {ext} WITH SCHEMA public")
        await conn.execute(f"CREATE SCHEMA {schema}")
        await conn.execute(f"SET search_path TO {schema},public")
        migration = MIGRATIONS / "048_identity_retention_provenance.sql"
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path != migration:
                async with conn.transaction():
                    await conn.execute(path.read_text())
        user = uuid.uuid4()
        await conn.execute(
            "INSERT INTO users(id,email,normalized_email,name,username,created_at) "
            "VALUES($1,'legacy@example.test','legacy@example.test','legacy','legacy','2001-01-01')",
            user,
        )
        await conn.execute(
            "INSERT INTO identity_providers(user_id,provider,provider_subject,normalized_email_at_link,linked_at) "
            "VALUES($1,'google','fictional-legacy-sub','old@example.test','2002-01-01')",
            user,
        )
        challenge = await record(
            conn, "email_challenges", NOW - 10 * DAY, email="legacy@example.test", consumed=True
        )
        audit = await record(
            conn, "identity_audit_log", NOW - 100 * DAY, email="legacy@example.test"
        )
        nonce = await record(conn, "google_nonce_challenges", NOW - 10 * DAY, consumed=True)
        await conn.execute(
            "UPDATE google_nonce_challenges SET user_id_proposed=$2 WHERE id=$1", nonce, user
        )
        invites = {}
        for status in ("disabled", "consumed", "expired"):
            invites[status] = await conn.fetchval(
                "INSERT INTO signup_invites(normalized_email,token_verifier_hash,status,expires_at) "
                "VALUES($1,$2,$3,'2003-01-01') RETURNING id",
                status + "@example.test",
                status,
                status,
            )
        before = await conn.fetchval("SELECT clock_timestamp()")
        async with conn.transaction():
            await conn.execute(migration.read_text())
        after = await conn.fetchval("SELECT clock_timestamp()")
        span = await conn.fetchrow("SELECT * FROM account_email_ownership WHERE user_id=$1", user)
        assert span is not None and before <= span["started_at"] <= after
        assert (
            span["source"] == "migration_baseline"
            and span["normalized_email"] == "legacy@example.test"
        )
        for table, rid in (("email_challenges", challenge), ("identity_audit_log", audit)):
            assert (
                await conn.fetchval(f"SELECT email_ownership_id FROM {table} WHERE id=$1", rid)
                is None
            )
        assert (
            await conn.fetchval(
                "SELECT claimed_user_id FROM google_nonce_challenges WHERE id=$1", nonce
            )
            is None
        )
        assert (
            await conn.fetchval(
                "SELECT claimed_user_id FROM email_challenges WHERE id=$1", challenge
            )
            is None
        )
        for status, rid in invites.items():
            terminal = await conn.fetchval(
                "SELECT terminal_at FROM signup_invites WHERE id=$1", rid
            )
            if status == "expired":
                assert terminal == datetime(2003, 1, 1, tzinfo=timezone.utc)
            else:
                assert before <= terminal <= after
        async with conn.transaction():
            await conn.execute(migration.read_text())
        assert (
            await conn.fetchrow("SELECT * FROM account_email_ownership WHERE id=$1", span["id"])
            == span
        )
        counts = await sweep_identity_records(conn, now=NOW)
        assert counts == {
            "identity_audit_log": 1,
            "email_challenges": 1,
            "google_nonce_challenges": 1,
            "signup_invites": 3,
        }
        assert (
            await conn.fetchval("SELECT id FROM account_email_ownership WHERE id=$1", span["id"])
            == span["id"]
        )
    finally:
        await conn.execute("SET search_path TO public")
        await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await conn.close()


@pytest.mark.asyncio
async def test_interval_reacquisition_backward_clock_and_losing_insert(env: Env) -> None:
    async with env.pool.acquire() as conn:
        await clock(conn, NOW)
        a = await owner(conn, env.alice, "shared@example.test")
        await owner(conn, env.alice, "shared@example.test")
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM account_email_ownership WHERE user_id=$1", env.alice
            )
            == 1
        )
        await clock(conn, NOW + DAY)
        await owner(conn, env.alice, "next@example.test")
        await clock(conn, NOW - DAY)
        b = await owner(conn, env.bob, "shared@example.test")
        assert (
            await conn.fetchval("SELECT started_at FROM account_email_ownership WHERE id=$1", b)
            == NOW + DAY
        )
        assert (
            await conn.fetchval("SELECT ended_at FROM account_email_ownership WHERE id=$1", a)
            == NOW + DAY
        )
        await owner(conn, env.bob, "bob-next@example.test")
        reacquired = await owner(conn, env.alice, "shared@example.test")
        assert reacquired != a
        assert (
            await conn.fetchval(
                "SELECT started_at FROM account_email_ownership WHERE id=$1", reacquired
            )
            == NOW + DAY
        )
        candidate = uuid.uuid4()
        await conn.execute(
            "INSERT INTO users(id,email,normalized_email,name,username) VALUES($1,'loser@example.test','shared@example.test','loser','loser') "
            "ON CONFLICT DO NOTHING",
            candidate,
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM account_email_ownership WHERE user_id=$1", candidate
            )
            == 0
        )
        with pytest.raises(RuntimeError, match="rollback"):
            async with conn.transaction():
                await owner(conn, env.alice, "rollback@example.test")
                raise RuntimeError("rollback")
        assert (
            await conn.fetchval(
                "SELECT id FROM account_email_ownership WHERE user_id=$1 AND ended_at IS NULL",
                env.alice,
            )
            == reacquired
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM account_email_ownership WHERE normalized_email='rollback@example.test'"
            )
            == 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_first", [True, False])
async def test_capture_and_change_use_observed_owner_under_locks(
    env: Env, capture_first: bool
) -> None:
    async with (
        env.pool.acquire() as first,
        env.pool.acquire() as second,
        env.pool.acquire() as observer,
    ):
        await clock(first, NOW)
        old = await owner(first, env.alice, "shared@example.test")
        pid1 = await first.fetchval("SELECT pg_backend_pid()")
        pid2 = await second.fetchval("SELECT pg_backend_pid()")
        rid = None
        new = None
        async with first.transaction():
            if capture_first:
                rid = await record(first, "email_challenges", NOW + DAY)
                task = asyncio.create_task(owner(second, env.alice, "next@example.test"))
            else:
                await owner(first, env.alice, "next@example.test")
                new = await owner(first, env.bob, "shared@example.test")
                task = asyncio.create_task(record(second, "email_challenges", NOW + DAY))
            try:
                await blocked(observer, pid2, pid1)
                assert not task.done()
                if capture_first:
                    await clock(observer, NOW + DAY)
            except BaseException:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
        result = await asyncio.wait_for(task, 5)
        if not capture_first:
            rid = result
        assert rid is not None
        observed = await observer.fetchval(
            "SELECT email_ownership_id FROM email_challenges WHERE id=$1", rid
        )
        assert observed == (old if capture_first else new)
        if capture_first:
            assert (
                await observer.fetchval(
                    "SELECT ended_at FROM account_email_ownership WHERE id=$1", old
                )
                == NOW + DAY
            )


@pytest.mark.asyncio
async def test_missing_history_fails_atomically_without_fabrication(env: Env) -> None:
    async with env.pool.acquire() as conn:
        prior = await owner(conn, env.alice, "missing-history@example.test")
        await conn.execute("DELETE FROM account_email_ownership WHERE id=$1", prior)
        with pytest.raises(asyncpg.RaiseError, match="identity ownership invariant violated"):
            await owner(conn, env.alice, "never-owned@example.test")
        assert (
            await conn.fetchval("SELECT normalized_email FROM users WHERE id=$1", env.alice)
            == "missing-history@example.test"
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM account_email_ownership WHERE user_id=$1", env.alice
            )
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM pg_class c, LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a "
                "WHERE c.oid='account_email_ownership'::regclass AND a.grantee=0"
            )
            == 0
        )


@pytest.mark.asyncio
async def test_email_update_waiting_on_capture_allows_user_fk_key_share(env: Env) -> None:
    async with (
        env.pool.acquire() as capture,
        env.pool.acquire() as writer,
        env.pool.acquire() as checker,
    ):
        await owner(capture, env.alice, "shared@example.test")
        pid1 = await capture.fetchval("SELECT pg_backend_pid()")
        pid2 = await writer.fetchval("SELECT pg_backend_pid()")
        async with capture.transaction():
            await capture.execute(
                "INSERT INTO identity_audit_log(user_id,normalized_email,event_type) "
                "VALUES($1,'shared@example.test','email_challenge_issued')",
                env.alice,
            )
            task = asyncio.create_task(owner(writer, env.alice, "moved@example.test"))
            try:
                await blocked(checker, pid2, pid1)
                async with checker.transaction():
                    await checker.execute("SET LOCAL lock_timeout='250ms'")
                    assert (
                        await checker.fetchval(
                            "SELECT id FROM users WHERE id=$1 FOR KEY SHARE", env.alice
                        )
                        == env.alice
                    )
            except BaseException:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
        await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["email_challenges", "signup_invites", "identity_audit_log"])
async def test_creation_capture_for_each_table_is_immutable_and_unknown_stays_unknown(
    env: Env, table: str
) -> None:
    async with env.pool.acquire() as conn:
        address = "capture-each@example.test"

        async def insert():
            if table != "signup_invites":
                return await record(conn, table, NOW + DAY, email=address)
            return await conn.fetchval(
                "INSERT INTO signup_invites(normalized_email,token_verifier_hash,expires_at,status) "
                "VALUES($1,$2,$3,'expired') RETURNING id",
                address,
                uuid.uuid4().hex,
                NOW - DAY,
            )

        unknown = await insert()
        span = await owner(conn, env.alice, address)
        known = await insert()
        assert (
            await conn.fetchval(f"SELECT email_ownership_id FROM {table} WHERE id=$1", known)
            == span
        )
        await owner(conn, env.alice, "capture-released@example.test")
        await owner(conn, env.bob, address)
        await conn.execute(
            f"UPDATE {table} SET normalized_email=normalized_email WHERE id IN ($1,$2)",
            unknown,
            known,
        )
        assert (
            await conn.fetchval(f"SELECT email_ownership_id FROM {table} WHERE id=$1", known)
            == span
        )
        assert (
            await conn.fetchval(f"SELECT email_ownership_id FROM {table} WHERE id=$1", unknown)
            is None
        )


@pytest.mark.asyncio
async def test_claimed_account_overrides_issuance_and_audit_expiry_not_history(env: Env) -> None:
    async with env.pool.acquire() as conn:
        await clock(conn, NOW - 100 * DAY)
        a = await owner(conn, env.alice, "shared@example.test")
        rid = await record(conn, "email_challenges", NOW - DAY, consumed=True)
        audit = await record(conn, "identity_audit_log", NOW - 90 * DAY)
        await clock(conn, NOW - DAY)
        await owner(conn, env.alice, "next@example.test")
        b = await owner(conn, env.bob, "shared@example.test")
        proof = EmailChallengeRow(rid, "shared@example.test", 5, NOW + DAY, NOW - DAY, None, NOW)
        async with conn.transaction():
            await lock_consumed_email_challenge(cast(Any, conn), proof)
            await attribute_email_challenge(cast(Any, conn), proof, env.bob)
        # This is a selection-rule/FK regression, NOT a shipped purge helper.
        selected = await conn.fetchval(
            "SELECT count(*) FROM email_challenges c LEFT JOIN account_email_ownership h ON h.id=c.email_ownership_id "
            "WHERE c.claimed_user_id=$1 OR (c.claimed_user_id IS NULL AND h.user_id=$1)",
            env.alice,
        )
        assert selected == 0
        counts = await sweep_identity_records(conn, now=NOW)
        assert counts["identity_audit_log"] == 1
        assert await conn.fetchval("SELECT id FROM identity_audit_log WHERE id=$1", audit) is None
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM account_email_ownership WHERE id IN ($1,$2)", a, b
            )
            == 2
        )
        await conn.execute("DELETE FROM account_email_ownership WHERE id=$1", a)
        remaining = await conn.fetchrow("SELECT * FROM email_challenges WHERE id=$1", rid)
        assert remaining["email_ownership_id"] is None and remaining["claimed_user_id"] == env.bob
        await conn.execute("DELETE FROM users WHERE id=$1", env.alice)
        assert (
            await conn.fetchval("SELECT claimed_user_id FROM email_challenges WHERE id=$1", rid)
            == env.bob
        )
        await conn.execute("DELETE FROM users WHERE id=$1", env.bob)
        assert await conn.fetchval("SELECT id FROM email_challenges WHERE id=$1", rid) is None


@pytest.mark.asyncio
async def test_exact_consumed_proof_guards_and_sweep_lock(env: Env) -> None:
    async with env.pool.acquire() as conn, env.pool.acquire() as sweeper:
        rid = await record(conn, "email_challenges", NOW - 8 * DAY, consumed=True)
        proof = EmailChallengeRow(
            rid, "shared@example.test", 5, NOW + DAY, NOW - 8 * DAY, None, NOW
        )
        for bad in (
            replace(proof, id=uuid.uuid4()),
            replace(proof, consumed_at=None),
            replace(proof, consumed_at=NOW),
            replace(proof, normalized_email="wrong@example.test"),
        ):
            with pytest.raises(EmailChallengeInvalid):
                async with conn.transaction():
                    await lock_consumed_email_challenge(cast(Any, conn), bad)
        async with conn.transaction():
            await lock_consumed_email_challenge(cast(Any, conn), proof)
            assert (await sweep_identity_records(sweeper, now=NOW))["email_challenges"] == 0
            await attribute_email_challenge(cast(Any, conn), proof, env.alice)
        with pytest.raises(EmailChallengeInvalid):
            async with conn.transaction():
                await lock_consumed_email_challenge(cast(Any, conn), proof)
        assert (await sweep_identity_records(sweeper, now=NOW))["email_challenges"] == 1
        with pytest.raises(EmailChallengeInvalid):
            async with conn.transaction():
                await lock_consumed_email_challenge(cast(Any, conn), proof)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,days",
    [
        ("identity_audit_log", 90),
        ("email_challenges", 7),
        ("google_nonce_challenges", 7),
        ("signup_invites", 7),
    ],
)
@pytest.mark.parametrize("consumed", [False, True])
async def test_inclusive_boundary_active_preservation_and_repeat(
    env: Env, table: str, days: int, consumed: bool
) -> None:
    async with env.pool.acquire() as conn:
        ids = []
        for delta in (-1, 0, 1):
            ids.append(
                await record(
                    conn, table, NOW - days * DAY + timedelta(microseconds=delta), consumed=consumed
                )
            )
        future = await record(conn, table, NOW + DAY, consumed=consumed)
        counts = await sweep_identity_records(conn, now=NOW)
        assert counts[table] == 2
        assert (
            await conn.fetchval(f"SELECT count(*) FROM {table} WHERE id=ANY($1::uuid[])", ids[:2])
            == 0
        )
        assert (
            await conn.fetchval(
                f"SELECT count(*) FROM {table} WHERE id=ANY($1::uuid[])", [ids[2], future]
            )
            == 2
        )
        assert not any((await sweep_identity_records(conn, now=NOW)).values())
        assert (await sweep_identity_records(conn, now=NOW + timedelta(microseconds=1)))[table] == 1


@pytest.mark.asyncio
async def test_lockout_invite_anchors_and_renewal(env: Env) -> None:
    async with env.pool.acquire() as conn:
        locked = await record(conn, "email_challenges", NOW + DAY)
        await conn.execute(
            "UPDATE email_challenges SET locked_at=$2,attempts_remaining=0 WHERE id=$1",
            locked,
            NOW - 30 * DAY,
        )
        await clock(conn, NOW - 8 * DAY)
        disabled = await conn.fetchval(
            "INSERT INTO signup_invites(normalized_email,token_verifier_hash,status,expires_at) VALUES('disabled@example.test','fictional-disabled','disabled',$1) RETURNING id",
            NOW + DAY,
        )
        # Unrelated update cannot restart a disabled clock.
        await clock(conn, NOW)
        await conn.execute(
            "UPDATE signup_invites SET expires_at=$2 WHERE id=$1", disabled, NOW + 2 * DAY
        )
        assert (
            await conn.fetchval("SELECT terminal_at FROM signup_invites WHERE id=$1", disabled)
            == NOW - 8 * DAY
        )
        expired = await conn.fetchval(
            "INSERT INTO signup_invites(normalized_email,token_verifier_hash,expires_at) VALUES('late@example.test','fictional-late',$1) RETURNING id",
            NOW - 8 * DAY,
        )
        await conn.execute("UPDATE signup_invites SET status='expired' WHERE id=$1", expired)
        assert (
            await conn.fetchval("SELECT terminal_at FROM signup_invites WHERE id=$1", expired)
            == NOW - 8 * DAY
        )
        renewed = await record(conn, "signup_invites", NOW - 8 * DAY, consumed=True)
        await conn.execute(
            "UPDATE signup_invites SET status='active',expires_at=$2 WHERE id=$1",
            renewed,
            NOW + DAY,
        )
        assert (
            await conn.fetchval("SELECT terminal_at FROM signup_invites WHERE id=$1", renewed)
            is None
        )
        assert (await sweep_identity_records(conn, now=NOW))["signup_invites"] == 2
        assert await conn.fetchval("SELECT id FROM signup_invites WHERE id=$1", renewed) == renewed
        assert await conn.fetchval("SELECT id FROM email_challenges WHERE id=$1", locked) == locked
        assert (await sweep_identity_records(conn, now=NOW + 8 * DAY))["email_challenges"] == 1


@pytest.mark.asyncio
async def test_locked_invite_renewal_skipped_then_rechecked(env: Env) -> None:
    async with env.pool.acquire() as conn, env.pool.acquire() as sweeper:
        rid = await record(conn, "signup_invites", NOW - 8 * DAY)
        async with conn.transaction():
            await conn.execute(
                "UPDATE signup_invites SET status='active',expires_at=$2 WHERE id=$1",
                rid,
                NOW + DAY,
            )
            assert (await sweep_identity_records(sweeper, now=NOW))["signup_invites"] == 0
        assert (await sweep_identity_records(sweeper, now=NOW))["signup_invites"] == 0
        assert await conn.fetchval("SELECT id FROM signup_invites WHERE id=$1", rid) == rid


@pytest.mark.asyncio
async def test_concurrent_ordered_batches_bounded_and_repeat_safe(env: Env) -> None:
    async with env.pool.acquire() as conn, env.pool.acquire() as other:
        for table in (
            "identity_audit_log",
            "email_challenges",
            "google_nonce_challenges",
            "signup_invites",
        ):
            for _ in range(1001):
                await record(conn, table, NOW - 100 * DAY)
        results = await asyncio.gather(
            sweep_identity_records(conn, now=NOW), sweep_identity_records(other, now=NOW)
        )
        assert all(0 <= n <= 1000 for result in results for n in result.values())
        assert all(sum(result[t] for result in results) == 1001 for t in results[0])
        assert not any((await sweep_identity_records(conn, now=NOW)).values())


@pytest.mark.asyncio
async def test_partial_failure_commits_prior_batch_and_next_sweep_recovers(
    env: Env, monkeypatch
) -> None:
    from orchestrator.services.identity import retention

    async with env.pool.acquire() as conn:
        audit = await record(conn, "identity_audit_log", NOW - 100 * DAY)
        challenge = await record(conn, "email_challenges", NOW - 8 * DAY)
        original = retention._TABLES
        with monkeypatch.context() as scoped:
            scoped.setattr(
                retention,
                "_TABLES",
                (
                    original[0],
                    ("nonexistent_identity_table", "created_at", "created_at <= $1", DAY),
                ),
            )
            with pytest.raises(asyncpg.UndefinedTableError):
                await sweep_identity_records(conn, now=NOW)
        assert await conn.fetchval("SELECT id FROM identity_audit_log WHERE id=$1", audit) is None
        assert (
            await conn.fetchval("SELECT id FROM email_challenges WHERE id=$1", challenge)
            == challenge
        )
        assert (await sweep_identity_records(conn, now=NOW))["email_challenges"] == 1


@pytest.mark.asyncio
async def test_retention_is_absolute_utc_across_dst_for_aware_clock(env: Env) -> None:
    # Seven calendar days in this zone span 169 hours; retention is 168.
    local_now = datetime(2035, 11, 6, 12, tzinfo=ZoneInfo("America/New_York"))
    utc_now = local_now.astimezone(timezone.utc)
    cutoff = utc_now - 7 * DAY
    assert local_now - 7 * DAY != cutoff
    async with env.pool.acquire() as conn:
        rid = await record(conn, "email_challenges", cutoff)
        assert (await sweep_identity_records(conn, now=local_now))["email_challenges"] == 1
        assert await conn.fetchval("SELECT id FROM email_challenges WHERE id=$1", rid) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("by_provider", [False, True])
async def test_claim_lookup_revalidates_current_locked_identity(
    env: Env, by_provider: bool
) -> None:
    async with (
        env.pool.acquire() as writer,
        env.pool.acquire() as claimant,
        env.pool.acquire() as observer,
    ):
        await owner(writer, env.alice, "shared@example.test")
        if by_provider:
            await writer.execute(
                "INSERT INTO identity_providers(user_id,provider,provider_subject) VALUES($1,'google','fictional-sub')",
                env.alice,
            )
        pid1 = await writer.fetchval("SELECT pg_backend_pid()")
        pid2 = await claimant.fetchval("SELECT pg_backend_pid()")
        svc = AccountService(cast(Any, claimant))
        async with writer.transaction():
            await owner(writer, env.alice, "changed@example.test")
            task = asyncio.create_task(
                svc.find_user_by_provider("google", "fictional-sub", for_update=True)
                if by_provider
                else svc.find_user_by_normalized_email("shared@example.test", for_update=True)
            )
            try:
                await blocked(observer, pid2, pid1)
            except BaseException:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
        result = await asyncio.wait_for(task, 5)
        if by_provider:
            assert (
                result is not None
                and result.id == env.alice
                and result.normalized_email == "changed@example.test"
            )
        else:
            assert result is None


@pytest.mark.asyncio
async def test_first_signup_conflict_fallback_locks_committed_owner(env: Env) -> None:
    async with (
        env.pool.acquire() as first,
        env.pool.acquire() as second,
        env.pool.acquire() as observer,
    ):
        pid1 = await first.fetchval("SELECT pg_backend_pid()")
        pid2 = await second.fetchval("SELECT pg_backend_pid()")
        async with first.transaction():
            original = await AccountService(cast(Any, first)).claim_email_identity_in_transaction(
                normalized_email="signup-race@example.test",
                email_verified_at=NOW,
                signup_mode="open",
                invite_token_verifier_hash=None,
            )
            task = asyncio.create_task(
                AccountService(cast(Any, second)).claim_email_identity(
                    normalized_email="signup-race@example.test",
                    email_verified_at=NOW,
                    signup_mode="open",
                    invite_token_verifier_hash=None,
                )
            )
            try:
                await blocked(observer, pid2, pid1)
            except BaseException:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
        duplicate = await asyncio.wait_for(task, 5)
        assert duplicate.user.id == original.user.id and not duplicate.is_new_user
        assert (
            await observer.fetchval(
                "SELECT count(*) FROM account_email_ownership WHERE user_id=$1", original.user.id
            )
            == 1
        )
        assert (
            await observer.fetchval(
                "SELECT count(*) FROM tenants WHERE owner_user_id=$1", original.user.id
            )
            == 1
        )


@pytest.mark.asyncio
async def test_two_google_changes_serialize_one_history_chain(env: Env) -> None:
    async with (
        env.pool.acquire() as first,
        env.pool.acquire() as second,
        env.pool.acquire() as observer,
    ):
        await clock(first, NOW)
        await owner(first, env.alice, "google-old@example.test")
        await first.execute(
            "INSERT INTO identity_providers(user_id,provider,provider_subject) VALUES($1,'google','fictional-changing-sub')",
            env.alice,
        )
        pid1 = await first.fetchval("SELECT pg_backend_pid()")
        pid2 = await second.fetchval("SELECT pg_backend_pid()")
        async with first.transaction():
            one = await AccountService(cast(Any, first)).claim_google_identity_in_transaction(
                google_sub="fictional-changing-sub",
                normalized_email="google-one@example.test",
                email_verified=True,
                signup_mode="open",
                invite_token_verifier_hash=None,
            )
            task = asyncio.create_task(
                AccountService(cast(Any, second)).claim_google_identity(
                    google_sub="fictional-changing-sub",
                    normalized_email="google-two@example.test",
                    email_verified=True,
                    signup_mode="open",
                    invite_token_verifier_hash=None,
                )
            )
            try:
                await blocked(observer, pid2, pid1)
            except BaseException:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
        two = await asyncio.wait_for(task, 5)
        assert one.user.id == two.user.id == env.alice
        spans = await observer.fetch(
            "SELECT * FROM account_email_ownership WHERE user_id=$1", env.alice
        )
        assert len(spans) == 3
        by_email = {row["normalized_email"]: row for row in spans}
        assert (
            by_email["google-old@example.test"]["ended_at"]
            == by_email["google-one@example.test"]["started_at"]
        )
        assert (
            by_email["google-one@example.test"]["ended_at"]
            == by_email["google-two@example.test"]["started_at"]
        )
        assert by_email["google-two@example.test"]["ended_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["email", "google"])
async def test_concurrent_service_consumption_has_one_winner(env: Env, provider: str) -> None:
    from orchestrator.config import Settings
    from orchestrator.services.identity.email_challenge import (
        EmailChallengeConsumeRequest,
        EmailChallengeIssueRequest,
        EmailChallengeService,
    )
    from orchestrator.services.identity.google_verifier import (
        GoogleNonceConsumeRequest,
        GoogleNonceInvalid,
        GoogleNonceIssueRequest,
        GoogleVerifierService,
    )

    settings: Settings = cast(Any, Settings)(
        _env_file=None,
        daemon_auth_pepper="p" * 64,  # Synthetic fixture, not a credential.
    )
    async with env.pool.acquire() as first, env.pool.acquire() as second:
        if provider == "email":
            svc1 = EmailChallengeService(cast(Any, first), settings)
            svc2 = EmailChallengeService(cast(Any, second), settings)
            proof, secret = await svc1.create_challenge_for_delivery(
                EmailChallengeIssueRequest("consume@example.test", None, None, 600, 5)
            )
            request = EmailChallengeConsumeRequest(proof.id, secret)
            calls = (svc1.consume_challenge(request), svc2.consume_challenge(request))
        else:
            svc1 = GoogleVerifierService(cast(Any, first), settings, cast(Any, lambda *_args: {}))
            svc2 = GoogleVerifierService(cast(Any, second), settings, cast(Any, lambda *_args: {}))
            proof, secret = await svc1.issue_nonce(GoogleNonceIssueRequest(None, None, 600))
            request = GoogleNonceConsumeRequest(proof.id, secret)
            calls = (svc1.consume_nonce(request), svc2.consume_nonce(request))
        results = await asyncio.gather(*calls, return_exceptions=True)
        assert sum(not isinstance(r, BaseException) for r in results) == 1
        assert sum(isinstance(r, (EmailChallengeInvalid, GoogleNonceInvalid)) for r in results) == 1
