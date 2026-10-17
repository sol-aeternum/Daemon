"""Explicit opt-in real PostgreSQL/asyncpg qualification of the uncalled store.

Fault triggers/transport loss are deliberate and labelled; ordinary contention,
ACLs, reset deadlines and decoding use the real installed driver/server.
"""

import asyncio
import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
import re
import sys
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from orchestrator import compute_metadata as m
from tests.compute_metadata_support import (
    Bridge,
    Database,
    DOWN,
    ENV,
    FUNCTIONS,
    KEY,
    MIGRATION,
    ROOT,
    UP,
    metadata_cluster as metadata_cluster,
)


@pytest_asyncio.fixture
async def db(metadata_cluster):
    cluster = metadata_cluster
    name = "md_" + uuid4().hex
    cluster.prepare(name)
    resource = cluster.resource / ("m-" + uuid4().hex[:6])
    resource.mkdir(mode=0o700)
    assert len(os.fsencode(resource / ".s.PGSQL.5432")) < 108
    bridge = Bridge(cluster, resource / ".s.PGSQL.5432")
    await bridge.start()
    database = Database(cluster, name, resource, bridge)
    try:
        yield database
    finally:
        await bridge.close()
        async with asyncio.timeout(2):
            while (
                remaining := cluster.sql(
                    f"SELECT count(*) FROM pg_stat_activity WHERE datname='{name}'", role="postgres"
                )
            ) != "0":
                await asyncio.sleep(0.01)
        assert remaining == "0", "owned database still has connected producers"
        cluster.record("database_reconciled", database=name, connections=0)
        assert name in cluster.databases and re.fullmatch(r"md_[0-9a-f]{32}", name)
        cluster.sql(f'DROP DATABASE "{name}"')
        cluster.sql("CHECKPOINT")  # Bound WAL in this exact disposable cluster's finite tmpfs.
        cluster.databases.remove(name)
        cluster.record("owned_database_dropped", database=name)


async def seed(db, owner, store):
    account = await db.account(owner)
    scope = m.new_scope_metadata_identity(account_id=account)
    await store.create_scope(scope)
    reservation = await db.reservation(owner, account)
    binding = m.new_reservation_metadata_identity(scope=scope, reservation_id=reservation)
    return account, scope, reservation, binding


@pytest.mark.asyncio
async def test_real_decode_collisions_nulls_no_provision_and_no_reopen(db):
    async with db.connection() as owner, db.pool() as pool:
        store = m.ComputeMetadataStore(pool)
        a, scope, r, ident = await seed(db, owner, store)
        before = await db.snapshot(owner, a)
        bound = await store.attach_reservation(ident)
        assert bound.identity == ident and bound.created_at.utcoffset() is not None
        assert await store.attach_reservation(ident) == bound
        assert await store.read_binding(account_id=a, reservation_id=r) == bound
        read_scope = await store.read_scope(account_id=a, metadata_scope_id=scope.metadata_scope_id)
        assert read_scope is not None and read_scope.identity == scope
        assert await store.read_scope(account_id=a, metadata_scope_id=uuid4()) is None
        assert await store.read_binding(account_id=a, reservation_id=uuid4()) is None
        assert await db.snapshot(owner, a) == before
        b, other_scope, other_r, other = await seed(db, owner, store)
        collisions = [
            replace(ident, dispatch_nonce=uuid4()),
            replace(ident, runtime_identity=uuid4()),
            replace(ident, scope=other_scope),
            replace(other, runtime_identity=ident.runtime_identity),
            replace(other, dispatch_nonce=ident.dispatch_nonce),
            replace(other, reservation_id=r),
        ]
        for bad in collisions:
            with pytest.raises(m.ComputeMetadataIdentityConflict) as caught:
                await store.attach_reservation(bad)
            assert caught.value.identity is bad and not caught.value.commit_acknowledged
            assert str(caught.value) == "identity_conflict"
        with pytest.raises(m.ComputeMetadataIdentityConflict):
            await store.create_scope(m.ScopeMetadataIdentity(b, scope.metadata_scope_id))
        assert await store.read_binding(account_id=b, reservation_id=r) is None
        async with db.connection("md_app") as app:
            args = [a, scope.metadata_scope_id, r, ident.runtime_identity, ident.dispatch_nonce]
            for index in range(5):
                bad = args.copy()
                bad[index] = None
                with pytest.raises(asyncpg.RaiseError, match="identity_conflict"):
                    await app.fetchrow(m._STATEMENTS["attach"], *bad)
            with pytest.raises(asyncpg.RaiseError, match="identity_conflict"):
                await app.fetchrow(m._STATEMENTS["attach"], *([None] * 5))
            assert await app.fetchrow(m._STATEMENTS["attach"], *args)
        missing = uuid4()
        await owner.execute(
            "INSERT INTO public.users(id,username) VALUES($1,$2)", missing, str(missing)
        )
        with pytest.raises(m.ComputeMetadataAccountUnavailable):
            await store.create_scope(m.new_scope_metadata_identity(account_id=missing))
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM entitlement_accounts WHERE user_id=$1", missing
            )
            == 0
        )

        new_r = await db.reservation(owner, a)
        new_binding = m.new_reservation_metadata_identity(scope=scope, reservation_id=new_r)
        await owner.execute(
            "UPDATE entitlement_accounts SET status='suspended' WHERE user_id=$1", a
        )
        with pytest.raises(m.ComputeMetadataAccountUnavailable):
            await store.attach_reservation(new_binding)
        await owner.execute(
            """UPDATE entitlement_reservations SET status='settled',actual_microusd=15,
            settled_at=clock_timestamp() WHERE id=$1""",
            r,
        )
        before = await db.snapshot(owner, a)
        assert await store.attach_reservation(ident) == bound
        await owner.execute(
            "REVOKE EXECUTE ON FUNCTION public.compute_metadata_attach(uuid,uuid,uuid,uuid,uuid) FROM md_app"
        )
        with pytest.raises(m.ComputeMetadataUnavailable):
            await store.attach_reservation(ident)
        await owner.execute(
            "GRANT EXECUTE ON FUNCTION public.compute_metadata_attach(uuid,uuid,uuid,uuid,uuid) TO md_app"
        )
        assert (
            await store.attach_reservation(ident) == bound and await db.snapshot(owner, a) == before
        )
        await owner.execute("UPDATE entitlement_accounts SET status='active' WHERE user_id=$1", a)
        before = await db.snapshot(owner, a)
        assert (
            await store.attach_reservation(ident) == bound and await db.snapshot(owner, a) == before
        )
        assert (await store.attach_reservation(new_binding)).identity == new_binding
        assert (
            await owner.fetchval("SELECT status FROM entitlement_reservations WHERE id=$1", r)
            == "settled"
        )
        assert other_r != r


@pytest.mark.asyncio
async def test_isolation_all_methods_and_restricted_principals(db):
    async with db.connection() as owner, db.pool() as pool:
        store = m.ComputeMetadataStore(pool)
        a, s, r, ident = await seed(db, owner, store)
        await store.attach_reservation(ident)
        args: dict[m._Operation, tuple] = {
            "create_scope": (a, s.metadata_scope_id),
            "attach": (a, s.metadata_scope_id, r, ident.runtime_identity, ident.dispatch_nonce),
            "read_scope": (a, s.metadata_scope_id),
            "read_binding": (a, r),
        }
        async with db.connection("md_app") as app:
            for key, values in args.items():
                with pytest.raises(asyncpg.RaiseError, match="metadata_isolation_required"):
                    async with app.transaction(isolation="repeatable_read"):
                        await app.fetchrow(m._STATEMENTS[key], *values)
            await app.execute(
                "CREATE TEMP TABLE entitlement_accounts(user_id uuid,status text); SET search_path=pg_temp"
            )
            assert (await app.fetchrow(m._STATEMENTS["read_binding"], a, r))["reservation_id"] == r
        for role in ("md_app", "md_controller", "md_third"):
            async with db.connection(role) as conn:
                for sql in (
                    "SELECT * FROM public.compute_scope_metadata",
                    "DELETE FROM public.compute_reservation_metadata",
                    "UPDATE public.compute_scope_metadata SET created_at=now()",
                ):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await conn.execute(sql)
                if role != "md_app":
                    for key, values in args.items():
                        with pytest.raises(asyncpg.InsufficientPrivilegeError):
                            await conn.fetchrow(m._STATEMENTS[key], *values)
                else:
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await conn.execute("SELECT public.compute_metadata_check_account($1)", a)
        for role, expected in (
            ("md_app", True),
            ("md_owner", False),
            ("md_controller", False),
            ("md_third", False),
            ("postgres", False),
        ):
            async with db.connection(role) as conn:
                assert await conn.fetchval(m.ROLE_PRECONDITION) is expected
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM pg_proc WHERE proname LIKE 'compute_metadata_%' AND proconfig=ARRAY['search_path=pg_catalog']"
            )
            == 5
        )


@pytest.mark.asyncio
async def test_clock_skew_and_actual_legacy_hold_sql(db):
    tree = ast.parse((ROOT / "orchestrator/entitlements/store.py").read_text())
    hold = next(
        ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.AnnAssign)
        and n.value is not None
        and getattr(n.target, "id", None) == "_HOLD_SQL"
    )
    async with db.connection() as owner, db.pool() as pool:
        store = m.ComputeMetadataStore(pool)
        a, s, _, _ = await seed(db, owner, store)
        for seconds in (-1, 1, -86400):
            r = await db.reservation(owner, a)
            await owner.execute(
                """UPDATE entitlement_reservations SET created_at=(
                SELECT created_at FROM compute_scope_metadata WHERE id=$1)+$2::interval WHERE id=$3""",
                s.metadata_scope_id,
                timedelta(seconds=seconds),
                r,
            )
            before = await db.snapshot(owner, a)
            await store.attach_reservation(
                m.new_reservation_metadata_identity(scope=s, reservation_id=r)
            )
            assert await db.snapshot(owner, a) == before
        for grouped in (False, True):
            a = await db.account(owner)
            group = uuid4() if grouped else None
            await db.reservation(owner, a, group)
            await owner.execute(
                "INSERT INTO entitlement_usage_periods(user_id,period_key,reserved_microusd) VALUES($1,'2026-10',100)",
                a,
            )
            args = {
                1: f"'{a}'::uuid",
                2: "'2026-10'",
                3: "10",
                4: "false",
                5: "clock_timestamp()",
                6: "100000",
                7: "100000",
                8: "100",
                9: "1",
                10: "10",
                11: "true",
                12: "interval '1 minute'",
                13: "false",
                14: "false",
                15: "NULL::uuid",
            }

            async def count():
                sql = re.sub(r"\$(\d+)", lambda x: args[int(x.group(1))], hold)
                return await owner.fetchval("WITH h AS (" + sql + ") SELECT count(*) FROM h")

            assert await count() == 0  # max1: NULL is its own operation, never free grouping.
            if grouped:
                args[15] = f"'{group}'::uuid"
                assert await count() == 1
            else:
                args[9] = "2"
                assert await count() == 1
                await db.reservation(owner, a)
                assert await count() == 0


@pytest.mark.asyncio
async def test_guarded_down_psql_entry_and_unsafe_defaults(db):
    # Precisely the documented psql entry, with one complete stdin file (-f -).
    from tests.compute_metadata_support import command

    def down():
        return command(
            [
                "docker",
                "exec",
                "-i",
                db.cluster.container,
                "psql",
                "-X",
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "md_owner",
                "-d",
                db.name,
                "-f",
                "-",
            ],
            text=DOWN.read_text(),
        )

    marker = "SELECT count(*) FROM public._migrations WHERE filename='048_compute_metadata.sql'"
    async with db.connection() as owner:
        await owner.execute("SELECT pg_advisory_lock($1)", KEY)
        failed = await asyncio.to_thread(down)
        assert failed.returncode and "migration_lock_unavailable" in failed.stderr
        assert await owner.fetchval(marker) == 1
        await owner.execute("SELECT pg_advisory_unlock($1)", KEY)
        await owner.execute("DELETE FROM public._migrations WHERE filename=$1", MIGRATION)
        failed = await asyncio.to_thread(down)
        assert failed.returncode and "rollback_marker_mismatch" in failed.stderr
        assert await owner.fetchval("SELECT to_regclass('public.compute_scope_metadata')")
        await owner.execute("INSERT INTO public._migrations(filename) VALUES($1)", MIGRATION)
        # A real metadata-table lock also bounds/refuses the whole down atomically.
        async with owner.transaction():
            await owner.execute("LOCK TABLE public.compute_scope_metadata IN ACCESS SHARE MODE")
            failed = await asyncio.to_thread(down)
            assert failed.returncode and "lock timeout" in failed.stderr
        assert await owner.fetchval(marker) == 1
        assert (await asyncio.to_thread(down)).returncode == 0
        assert await owner.fetchval(marker) == 0
        assert await owner.fetchval("SELECT to_regclass('public.compute_scope_metadata')") is None
        # Named-role function defaults as well as inherited PUBLIC/named table ACLs.
        for kind, grantee in (
            ("TABLES", "PUBLIC"),
            ("TABLES", "md_controller"),
            ("FUNCTIONS", "md_controller"),
        ):
            privilege = "SELECT" if kind == "TABLES" else "EXECUTE"
            await owner.execute(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT {privilege} ON {kind} TO {grantee}"
            )
            try:
                with pytest.raises(asyncpg.RaiseError, match="unsafe_default_privileges"):
                    async with owner.transaction():
                        await owner.execute(UP.read_text())
                assert (
                    await owner.fetchval("SELECT to_regclass('public.compute_scope_metadata')")
                    is None
                )
            finally:
                await owner.execute(
                    f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE {privilege} ON {kind} FROM {grantee}"
                )
        # PostgreSQL disallows grant options to PUBLIC, including default ACLs.
        with pytest.raises(asyncpg.InvalidGrantOperationError):
            await owner.execute(
                "ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO PUBLIC WITH GRANT OPTION"
            )
        async with owner.transaction():
            await owner.execute(UP.read_text())
            await owner.execute("INSERT INTO public._migrations(filename) VALUES($1)", MIGRATION)
        with pytest.raises(asyncpg.InvalidGrantOperationError):
            await owner.execute(
                "GRANT EXECUTE ON FUNCTION public.compute_metadata_create_scope(uuid,uuid) TO PUBLIC WITH GRANT OPTION"
            )
        db.cluster.grants(db.name)
        async with db.pool() as pool:
            s = m.new_scope_metadata_identity(account_id=await db.account(owner))
            await m.ComputeMetadataStore(pool).create_scope(s)
        failed = await asyncio.to_thread(down)
        assert failed.returncode and "rollback_refused_nonempty" in failed.stderr
        assert await owner.fetchval(marker) == 1
        assert await owner.fetchval("SELECT count(*) FROM compute_scope_metadata") == 1
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM pg_proc WHERE proname LIKE 'compute_metadata_%'"
            )
            == 5
        )


@pytest.mark.asyncio
async def test_c1_owner_memberships_privileges_and_exact_acl_restoration(db):
    # The V6 predicate without ONLY the C1 schema/function-owner clauses is a
    # discriminating control. C1 negatives must not trip a pre-existing clause.
    start = m.ROLE_PRECONDITION.index(" AND NOT EXISTS (\n   SELECT 1 FROM pg_catalog.pg_namespace")
    end = m.ROLE_PRECONDITION.index(" AND NOT EXISTS (\n   SELECT 1 FROM pg_catalog.pg_class")
    old = m.ROLE_PRECONDITION[:start] + m.ROLE_PRECONDITION[end:]
    async with (
        db.connection("postgres") as admin,
        db.connection("md_app") as app,
        db.pool() as pool,
    ):
        store = m.ComputeMetadataStore(pool)
        account = await db.account(admin)

        async def catalog():
            return await admin.fetchval("""SELECT jsonb_build_object(
              'schema',(SELECT jsonb_build_object('owner',nspowner,'acl',nspacl::text) FROM pg_namespace WHERE nspname='public'),
              'db',(SELECT datdba FROM pg_database WHERE datname=current_database()),
              'functions',(SELECT jsonb_agg(jsonb_build_object('oid',oid,'owner',proowner,'acl',proacl::text) ORDER BY oid) FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname LIKE 'compute_metadata_%'),
              'tables',(SELECT jsonb_agg(jsonb_build_object('oid',oid,'owner',relowner,'acl',relacl::text) ORDER BY oid) FROM pg_class WHERE oid IN ('public.compute_scope_metadata'::regclass,'public.compute_reservation_metadata'::regclass)),
              'memberships',(SELECT coalesce(jsonb_agg(jsonb_build_object('r',roleid,'m',member,'i',inherit_option,'s',set_option) ORDER BY roleid,member),'[]'::jsonb) FROM pg_auth_members WHERE member IN (SELECT oid FROM pg_roles WHERE rolname IN ('md_app','md_mid'))))""")

        baseline = await catalog()

        async def control():
            assert await app.fetchval(old) is True
            assert await app.fetchval(m.ROLE_PRECONDITION) is True
            assert (
                await catalog() == baseline
            )  # Includes exact function ACL TEXT, not just effective grants.

        async def negative(*, c1=False):
            if c1:
                assert await app.fetchval(old) is True
                assert not await app.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_class WHERE oid IN ('public.compute_scope_metadata'::regclass,'public.compute_reservation_metadata'::regclass) AND pg_has_role(CURRENT_USER,relowner,'MEMBER'))"
                )
            assert await app.fetchval(m.ROLE_PRECONDITION) is False
            ident = m.new_scope_metadata_identity(account_id=account)
            with pytest.raises(m.ComputeMetadataUnavailable) as caught:
                await store.create_scope(ident)
            assert caught.value.code == "role_precondition" and caught.value.identity is ident
            assert not caught.value.commit_acknowledged
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM compute_scope_metadata WHERE id=$1",
                    ident.metadata_scope_id,
                )
                == 0
            )

        await control()
        # Schema exact/direct/transitive/non-inheriting and implicit pg_database_owner.
        cases = [
            (
                "ALTER SCHEMA public OWNER TO md_app",
                "ALTER SCHEMA public OWNER TO pg_database_owner",
            ),
            (
                "ALTER SCHEMA public OWNER TO md_schema; GRANT md_schema TO md_app",
                "REVOKE md_schema FROM md_app; ALTER SCHEMA public OWNER TO pg_database_owner",
            ),
            (
                "ALTER SCHEMA public OWNER TO md_schema; GRANT md_schema TO md_mid; GRANT md_mid TO md_app",
                "REVOKE md_mid FROM md_app; REVOKE md_schema FROM md_mid; ALTER SCHEMA public OWNER TO pg_database_owner",
            ),
            (
                "ALTER SCHEMA public OWNER TO md_schema; GRANT md_schema TO md_mid; GRANT md_mid TO md_app WITH INHERIT FALSE",
                "REVOKE md_mid FROM md_app; REVOKE md_schema FROM md_mid; ALTER SCHEMA public OWNER TO pg_database_owner",
            ),
            (
                f"ALTER DATABASE {db.name} OWNER TO md_app",
                f"ALTER DATABASE {db.name} OWNER TO postgres",
            ),
            (
                f"ALTER DATABASE {db.name} OWNER TO md_schema; GRANT md_schema TO md_mid; GRANT md_mid TO md_app",
                f"REVOKE md_mid FROM md_app; REVOKE md_schema FROM md_mid; ALTER DATABASE {db.name} OWNER TO postgres",
            ),
            (
                f"ALTER DATABASE {db.name} OWNER TO md_schema; GRANT md_schema TO md_mid; GRANT md_mid TO md_app WITH INHERIT FALSE",
                f"REVOKE md_mid FROM md_app; REVOKE md_schema FROM md_mid; ALTER DATABASE {db.name} OWNER TO postgres",
            ),
        ]
        for change, restore in cases:
            await admin.execute(change)
            try:
                await negative(c1=True)
            finally:
                await admin.execute(restore)
            await control()
        for fn in FUNCTIONS:
            await admin.execute(f"ALTER FUNCTION public.{fn} OWNER TO md_app")
            try:
                await negative(c1=fn != FUNCTIONS[0])
            finally:
                await admin.execute(f"ALTER FUNCTION public.{fn} OWNER TO md_owner")
                db.cluster.grants(db.name)
            await control()
        for fn in (FUNCTIONS[1], FUNCTIONS[0]):
            for chain in (
                "GRANT md_function TO md_app",
                "GRANT md_function TO md_mid; GRANT md_mid TO md_app",
                "GRANT md_function TO md_mid; GRANT md_mid TO md_app WITH INHERIT FALSE",
            ):
                await admin.execute(f"ALTER FUNCTION public.{fn} OWNER TO md_function;" + chain)
                try:
                    # Inheriting private-helper owners already trip helper EXECUTE;
                    # NOINHERIT specifically isolates its new owner-MEMBER guard.
                    isolated = fn != FUNCTIONS[0] or "INHERIT FALSE" in chain
                    if fn == FUNCTIONS[0] and isolated:
                        assert (
                            await app.fetchval(
                                "SELECT has_function_privilege(CURRENT_USER,'public.compute_metadata_check_account(uuid)','EXECUTE')"
                            )
                            is False
                        )
                    await negative(c1=isolated)
                finally:
                    await admin.execute(
                        "REVOKE md_function FROM md_app; REVOKE md_mid FROM md_app; REVOKE md_function FROM md_mid;"
                        + f"ALTER FUNCTION public.{fn} OWNER TO md_owner"
                    )
                    db.cluster.grants(db.name)
                await control()
        for table in ("compute_scope_metadata", "compute_reservation_metadata"):
            for target, chain, revoke in (
                ("md_app", "", ""),
                ("md_table", "GRANT md_table TO md_app", "REVOKE md_table FROM md_app"),
                (
                    "md_table",
                    "GRANT md_table TO md_mid; GRANT md_mid TO md_app WITH INHERIT FALSE",
                    "REVOKE md_mid FROM md_app; REVOKE md_table FROM md_mid",
                ),
            ):
                await admin.execute(f"ALTER TABLE public.{table} OWNER TO {target};" + chain)
                try:
                    await negative()
                finally:
                    await admin.execute(
                        revoke + ";" + f"ALTER TABLE public.{table} OWNER TO md_owner"
                    )
                await control()
        # Original default/role/table/column/helper guards, direct and transitive.
        changes = [
            ("GRANT postgres TO md_app", "REVOKE postgres FROM md_app"),
            (
                "GRANT pg_execute_server_program TO md_mid; GRANT md_mid TO md_app WITH INHERIT FALSE",
                "REVOKE md_mid FROM md_app; REVOKE pg_execute_server_program FROM md_mid",
            ),
            ("GRANT md_owner TO md_app", "REVOKE md_owner FROM md_app"),
            (
                "ALTER ROLE md_mid CREATEROLE; GRANT md_mid TO md_app WITH INHERIT FALSE",
                "REVOKE md_mid FROM md_app; ALTER ROLE md_mid NOCREATEROLE",
            ),
            (
                "ALTER ROLE md_mid BYPASSRLS; GRANT md_mid TO md_app",
                "REVOKE md_mid FROM md_app; ALTER ROLE md_mid NOBYPASSRLS",
            ),
            ("GRANT pg_read_server_files TO md_app", "REVOKE pg_read_server_files FROM md_app"),
            (
                "GRANT pg_write_server_files TO md_mid; GRANT md_mid TO md_app",
                "REVOKE md_mid FROM md_app; REVOKE pg_write_server_files FROM md_mid",
            ),
            (
                "GRANT md_owner TO md_mid; GRANT md_mid TO md_app WITH INHERIT FALSE",
                "REVOKE md_mid FROM md_app; REVOKE md_owner FROM md_mid",
            ),
            (
                "GRANT EXECUTE ON FUNCTION public.compute_metadata_check_account(uuid) TO md_app",
                "REVOKE EXECUTE ON FUNCTION public.compute_metadata_check_account(uuid) FROM md_app",
            ),
        ]
        for fn in FUNCTIONS[1:]:
            changes.append(
                (
                    f"REVOKE EXECUTE ON FUNCTION public.{fn} FROM md_app",
                    f"GRANT EXECUTE ON FUNCTION public.{fn} TO md_app",
                )
            )
        for table in ("compute_scope_metadata", "compute_reservation_metadata"):
            for privilege in (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
                "SELECT(created_at)",
            ):
                changes.append(
                    (
                        f"GRANT {privilege} ON public.{table} TO md_app",
                        f"REVOKE {privilege} ON public.{table} FROM md_app",
                    )
                )
        for change, restore in changes:
            await admin.execute(change)
            try:
                await negative()
            finally:
                await admin.execute(restore)
            await control()
        db.cluster.record("c1_restored", database=db.name, exact_catalog_and_function_acl=True)


@pytest.mark.asyncio
async def test_real_store_statement_errors_and_bounded_account_barrier(db):
    async with db.connection("postgres") as admin, db.connection() as owner, db.pool() as pool:
        store = m.ComputeMetadataStore(pool)
        a = await db.account(owner)
        identity = m.new_scope_metadata_identity(account_id=a)
        async with owner.transaction():
            await owner.execute("SELECT 1 FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", a)
            for call in (
                store.create_scope(identity),
                store.read_scope(account_id=a, metadata_scope_id=identity.metadata_scope_id),
            ):
                start = asyncio.get_running_loop().time()
                with pytest.raises(m.ComputeMetadataUnavailable) as caught:
                    await call
                assert caught.value.sqlstate == "55P03" and not caught.value.commit_acknowledged
                assert asyncio.get_running_loop().time() - start < 2
        # Deliberate server-side trigger faults, NOT Python exceptions/classifier-only tests.
        await admin.execute(
            "CREATE TABLE public.md_denied(secret int); REVOKE ALL ON public.md_denied FROM PUBLIC"
        )
        for body, state, cls in (
            ("PERFORM pg_sleep(2);", "57014", m.ComputeMetadataUnavailable),
            ("PERFORM 'not-a-uuid'::uuid;", "22P02", m.ComputeMetadataIdentityConflict),
            ("PERFORM * FROM public.md_denied;", "42501", m.ComputeMetadataUnavailable),
        ):
            await owner.execute(
                "CREATE OR REPLACE FUNCTION public.md_fault() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                + body
                + " RETURN NEW; END $$"
            )
            await owner.execute(
                "CREATE TRIGGER md_fault BEFORE INSERT ON public.compute_scope_metadata FOR EACH ROW EXECUTE FUNCTION public.md_fault()"
            )
            ident = m.new_scope_metadata_identity(account_id=a)
            try:
                with pytest.raises(cls) as caught:
                    await store.create_scope(ident)
                assert caught.value.sqlstate == state and caught.value.identity is ident
                assert not caught.value.commit_acknowledged
                assert (
                    await store.read_scope(account_id=a, metadata_scope_id=ident.metadata_scope_id)
                    is None
                )
                db.cluster.record(
                    "actual_adapter_server_fault", sqlstate=state, deliberate_trigger=True
                )
            finally:
                await owner.execute("DROP TRIGGER md_fault ON public.compute_scope_metadata")


@pytest.mark.asyncio
async def test_natural_deadlock_through_store_not_classifier(db):
    async with db.connection("postgres") as admin, db.connection() as owner:
        # Synthetic cluster only. 10ms detector lets the real deadlock beat the
        # store's 250ms lock timeout; the opposing connection detects at 5s.
        await admin.execute(f"ALTER ROLE md_app IN DATABASE {db.name} SET deadlock_timeout='10ms'")
        a, b = await db.account(owner), await db.account(owner)
        await owner.execute(f"""CREATE FUNCTION public.md_deadlock() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN PERFORM 1 FROM public.entitlement_accounts WHERE user_id='{b}'::uuid FOR UPDATE; RETURN NEW; END $$;
            CREATE TRIGGER md_deadlock BEFORE INSERT ON public.compute_scope_metadata FOR EACH ROW EXECUTE FUNCTION public.md_deadlock();""")
        async with db.pool() as pool, db.connection("postgres") as opponent:
            await opponent.execute(
                "BEGIN; SET LOCAL deadlock_timeout='5s'; SET LOCAL statement_timeout='3s'"
            )
            await opponent.execute(
                "SELECT 1 FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", b
            )
            ident = m.new_scope_metadata_identity(account_id=a)
            job = asyncio.create_task(m.ComputeMetadataStore(pool).create_scope(ident))
            try:
                # Observe the actual store waiter, then close the cycle.
                async with asyncio.timeout(1):
                    while not await admin.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=$1 AND usename='md_app' AND wait_event_type='Lock')",
                        db.name,
                    ):
                        await asyncio.sleep(0.005)
                other = asyncio.create_task(
                    opponent.execute(
                        "SELECT 1 FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", a
                    )
                )
                with pytest.raises(m.ComputeMetadataUnavailable) as caught:
                    await job
                assert caught.value.sqlstate == "40P01" and caught.value.identity is ident
                assert not caught.value.commit_acknowledged
                await other
                db.cluster.record("actual_adapter_deadlock", sqlstate="40P01", natural_cycle=True)
            finally:
                await opponent.execute("ROLLBACK")
                if not job.done():
                    job.cancel()
                    await asyncio.gather(job, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_asyncpg_reset_timeout_after_ack_and_resource_reconciliation(db):
    reset_entered = asyncio.Event()
    pids = []

    async def slow_reset(conn):
        pids.append(conn.get_server_pid())
        reset_entered.set()
        await conn.execute(
            "SELECT pg_sleep(3)"
        )  # Real protocol/query under asyncpg's release allowance.

    async with db.connection() as owner, db.connection("postgres") as admin:
        a = await db.account(owner)
        ident = m.new_scope_metadata_identity(account_id=a)
        async with db.pool(reset=slow_reset) as pool:
            store = m.ComputeMetadataStore(pool)
            start = asyncio.get_running_loop().time()
            with pytest.raises(m.ComputeMetadataCommittedResultUnavailable) as caught:
                await store.create_scope(ident)
            elapsed = asyncio.get_running_loop().time() - start
            assert reset_entered.is_set() and 0.4 < elapsed < 2
            assert caught.value.commit_acknowledged and caught.value.identity is ident
            # asyncpg itself terminates/detaches on reset timeout. A second local
            # terminate may report failure; never interpret that as rollback.
            assert pool.get_idle_size() == pool.get_size() == 0
            # Local termination is NOT remote-stop evidence. The isolated bridge
            # can close while a server query keeps running until it notices EOF.
            # Reconcile only these exact owned database/app backend identities.
            remaining = await admin.fetch(
                "SELECT pid,datname,usename FROM pg_stat_activity WHERE pid=ANY($1::int[])", pids
            )
            for backend in remaining:
                assert backend["datname"] == db.name and backend["usename"] == "md_app"
                assert await admin.fetchval("SELECT pg_terminate_backend($1)", backend["pid"])
            async with asyncio.timeout(2):
                while await admin.fetchval(
                    "SELECT count(*) FROM pg_stat_activity WHERE pid=ANY($1::int[])", pids
                ):
                    await asyncio.sleep(0.01)
        async with db.pool() as fresh:
            row = await m.ComputeMetadataStore(fresh).read_scope(
                account_id=a, metadata_scope_id=ident.metadata_scope_id
            )
            assert row is not None and row.identity == ident
        db.cluster.record(
            "actual_release_timeout",
            elapsed=elapsed,
            commit_acknowledged=True,
            backend_pids=pids,
            explicit_backend_reconciliation=len(remaining),
            backends_remaining=0,
        )


@pytest.mark.parametrize("commit", [True, False])
@pytest.mark.asyncio
async def test_actual_adapter_commit_ack_loss_or_unsent_commit(db, commit):
    async with db.connection() as owner:
        account = await db.account(owner)
        ident = m.new_scope_metadata_identity(account_id=account)
        db.bridge.commit_fault = "after" if commit else "before"
        async with db.pool() as pool:
            job = asyncio.create_task(m.ComputeMetadataStore(pool).create_scope(ident))
            await asyncio.wait_for(db.bridge.commit_seen.wait(), 1)
            with pytest.raises(m.ComputeMetadataWriteUncertain) as caught:
                await job
            assert caught.value.identity is ident and not caught.value.commit_acknowledged
        db.bridge.commit_fault = None
        async with db.pool() as fresh:
            store = m.ComputeMetadataStore(fresh)
            row = await store.read_scope(
                account_id=account, metadata_scope_id=ident.metadata_scope_id
            )
            assert (row is not None) is commit
            if row:
                assert row.identity == ident
        assert await owner.fetchval(
            "SELECT count(*) FROM compute_scope_metadata WHERE id=$1", ident.metadata_scope_id
        ) == int(commit)
        assert not commit or db.bridge.discarded_commit_bytes > 0
        db.cluster.record(
            "actual_adapter_commit_transport_loss",
            commit_forwarded=commit,
            response_bytes_forwarded=0,
            discarded_bytes=db.bridge.discarded_commit_bytes,
            authoritative_same_identity_present=row is not None,
        )


@pytest.mark.asyncio
async def test_actual_killed_adapter_client_after_unacknowledged_commit(db):
    # A real separate Python client calls the production store; -9 prevents its
    # COMMIT acknowledgement. Not exit0, not an exception raised after success.
    async with db.connection() as owner:
        a = await db.account(owner)
        ident = m.new_scope_metadata_identity(account_id=a)
        db.bridge.commit_fault = "after"
        client_tag = "md-killed-" + uuid4().hex
        code = """import asyncio,asyncpg,sys
from uuid import UUID
from orchestrator.compute_metadata import ComputeMetadataStore,ScopeMetadataIdentity
async def main():
 p=await asyncpg.create_pool(host=sys.argv[1],user='md_app',password='',database=sys.argv[2],ssl=False,min_size=0,max_size=1,server_settings={'application_name':sys.argv[5]})
 await ComputeMetadataStore(p).create_scope(ScopeMetadataIdentity(UUID(sys.argv[3]),UUID(sys.argv[4])))
asyncio.run(main())
"""
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            code,
            str(db.host),
            db.name,
            str(a),
            str(ident.metadata_scope_id),
            client_tag,
            env={**ENV, "PYTHONPATH": str(ROOT)},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        db.cluster.record("adapter_client_started", pid=proc.pid)
        backend_pid = None
        try:
            await asyncio.wait_for(db.bridge.commit_seen.wait(), 1.5)
            backend_pid = int(
                db.sql(
                    f"SELECT pid FROM pg_stat_activity WHERE datname='{db.name}' AND usename='md_app' AND application_name='{client_tag}'",
                    role="postgres",
                )
            )
            # COMMIT forwarded; owner account barrier resolves server completion.
            async with owner.transaction():
                await owner.execute(
                    "SELECT 1 FROM entitlement_accounts WHERE user_id=$1 FOR UPDATE", a
                )
                assert (
                    await owner.fetchval(
                        "SELECT count(*) FROM compute_scope_metadata WHERE id=$1",
                        ident.metadata_scope_id,
                    )
                    == 1
                )
            proc.kill()
            await asyncio.wait_for(proc.wait(), 1)
            assert proc.returncode == -9
        finally:
            if proc.returncode is None:
                proc.kill()
                await asyncio.wait_for(proc.wait(), 1)
            await asyncio.wait_for(proc.communicate(), 0.5)
            db.bridge.commit_fault = None
            if backend_pid is not None:
                # -9 is client loss, never proof that its remote bridge/backend
                # stopped. Reconcile this captured exact synthetic identity.
                db.sql(
                    f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid={backend_pid} AND datname='{db.name}' AND usename='md_app' AND application_name='{client_tag}'",
                    role="postgres",
                )
        async with db.pool() as fresh:
            row = await m.ComputeMetadataStore(fresh).read_scope(
                account_id=a, metadata_scope_id=ident.metadata_scope_id
            )
            assert row is not None and row.identity == ident
        db.cluster.record(
            "actual_adapter_client_killed",
            pid=proc.pid,
            returncode=proc.returncode,
            backend_pid=backend_pid,
            explicit_backend_reconciliation=True,
            same_identity_resolved=True,
            commit_response_bytes_forwarded=0,
        )


@pytest.mark.asyncio
async def test_create_attach_duplicate_conflict_and_cross_account_real_waiters(db):
    async with (
        db.connection() as owner,
        db.connection("postgres") as admin,
        db.connection("md_app") as left,
    ):
        async with db.pool(server_settings={"application_name": "md-real-waiter"}) as pool:
            store = m.ComputeMetadataStore(pool)
            for case in (
                "create_duplicate",
                "create_cross_account",
                "attach_duplicate",
                "attach_conflict",
                "attach_cross_account_unique",
            ):
                a, s, r, ident = await seed(db, owner, store)
                if case.startswith("create"):
                    scope_id = uuid4()
                    other_a = await db.account(owner) if case.endswith("cross_account") else a
                    wanted = m.ScopeMetadataIdentity(other_a, scope_id)
                    key, args = "create_scope", (a, scope_id)
                else:
                    key, args = (
                        "attach",
                        (a, s.metadata_scope_id, r, ident.runtime_identity, ident.dispatch_nonce),
                    )
                    wanted = (
                        replace(ident, dispatch_nonce=uuid4())
                        if case == "attach_conflict"
                        else ident
                    )
                    if case == "attach_cross_account_unique":
                        _, other_s, other_r, _ = await seed(db, owner, store)
                        wanted = m.ReservationMetadataIdentity(
                            other_s, other_r, ident.runtime_identity, uuid4()
                        )
                await left.execute("BEGIN")
                await left.fetchrow(m._STATEMENTS[key], *args)
                job = asyncio.create_task(
                    store.create_scope(wanted)
                    if isinstance(wanted, m.ScopeMetadataIdentity)
                    else store.attach_reservation(wanted)
                )
                try:
                    async with asyncio.timeout(0.2):
                        while not await admin.fetchval(
                            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=$1 AND application_name='md-real-waiter' AND wait_event_type='Lock')",
                            db.name,
                        ):
                            await asyncio.sleep(0.005)
                    await left.execute("COMMIT")
                    if case.endswith("duplicate"):
                        assert (await job).identity == wanted
                    else:
                        with pytest.raises(m.ComputeMetadataIdentityConflict) as caught:
                            await job
                        assert caught.value.identity is wanted
                        assert str(caught.value) == "identity_conflict"
                    db.cluster.record("real_waiter_race", case=case, observed_waiter=True)
                finally:
                    await left.execute("ROLLBACK")
                    if not job.done():
                        job.cancel()
                        await asyncio.gather(job, return_exceptions=True)


@pytest.mark.asyncio
async def test_deletion_shapes_fk_direction_and_both_attach_delete_orders(db):
    async with db.connection() as owner, db.connection("postgres") as admin, db.pool() as pool:
        store = m.ComputeMetadataStore(pool)
        a, s, r, ident = await seed(db, owner, store)
        await store.attach_reservation(ident)
        for state in ("queued", "completed", "running"):
            conversation, user_msg, assistant_msg, task = (uuid4() for _ in range(4))
            await owner.execute(
                "INSERT INTO conversations(id,user_id,pipeline) VALUES($1,$2,'cloud')",
                conversation,
                a,
            )
            await owner.execute(
                """INSERT INTO messages(id,conversation_id,user_id,role,content)
                VALUES($1,$3,$4,'user','synthetic'),($2,$3,$4,'assistant','synthetic')""",
                user_msg,
                assistant_msg,
                conversation,
                a,
            )
            await owner.execute(
                """INSERT INTO tasks(id,user_id,conversation_id,user_message_id,result_message_id,
                input_ciphertext,request_hash,status,lease_owner,lease_expires_at,finished_at)
                VALUES($1,$2,$3,$4,$5,'synthetic',$6,$7,$8,$9,$10)""",
                task,
                a,
                conversation,
                user_msg,
                assistant_msg,
                "0" * 64,
                state,
                "fixture" if state == "running" else None,
                datetime.now(timezone.utc) + timedelta(seconds=45) if state == "running" else None,
                datetime.now(timezone.utc) if state == "completed" else None,
            )
            # Existing conversation deletion SQL shapes, not full hosted purge.
            async with owner.transaction():
                await owner.fetchval(
                    "SELECT user_id FROM conversations WHERE id=$1 FOR UPDATE", conversation
                )
                await owner.fetch(
                    "SELECT * FROM tasks WHERE conversation_id=$1 AND status IN ('queued','running') FOR UPDATE",
                    conversation,
                )
                if state == "running":
                    await owner.execute(
                        "UPDATE tasks SET cancel_requested_at=now(),updated_at=now() WHERE id=$1",
                        task,
                    )
                else:
                    await owner.execute("DELETE FROM conversations WHERE id=$1", conversation)
            assert await owner.fetchval(
                "SELECT count(*) FROM conversations WHERE id=$1", conversation
            ) == int(state == "running")
            row = await store.read_binding(account_id=a, reservation_id=r)
            assert row is not None and row.identity == ident
        before = await db.snapshot(owner, a)
        await owner.execute("DELETE FROM compute_scope_metadata WHERE id=$1", s.metadata_scope_id)
        assert await db.snapshot(owner, a) == before  # Metadata never deletes financial parents.
        s2 = m.new_scope_metadata_identity(account_id=a)
        await store.create_scope(s2)
        await store.attach_reservation(
            m.new_reservation_metadata_identity(scope=s2, reservation_id=r)
        )
        await owner.execute("DELETE FROM entitlement_reservations WHERE id=$1", r)
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM compute_reservation_metadata WHERE reservation_id=$1", r
            )
            == 0
        )
        await owner.execute("DELETE FROM users WHERE id=$1", a)
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM compute_scope_metadata WHERE account_id=$1", a
            )
            == 0
        )
        assert (
            await owner.fetchval("SELECT count(*) FROM entitlement_accounts WHERE user_id=$1", a)
            == 0
        )
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM entitlement_reservations WHERE user_id=$1", a
            )
            == 0
        )
        async with db.connection("md_app") as attach, db.connection() as delete:
            for attach_first in (True, False):
                a, s, r, ident = await seed(db, owner, store)
                args = (a, s.metadata_scope_id, r, ident.runtime_identity, ident.dispatch_nonce)
                if attach_first:
                    await attach.execute("BEGIN")
                    await attach.fetchrow(m._STATEMENTS["attach"], *args)
                    job = asyncio.create_task(delete.execute("DELETE FROM users WHERE id=$1", a))
                    try:
                        await db.wait_lock(admin, delete.get_server_pid())
                    finally:
                        await attach.execute("COMMIT")
                    await job
                else:
                    await delete.execute("BEGIN")
                    await delete.execute("DELETE FROM users WHERE id=$1", a)
                    job = asyncio.create_task(attach.fetchrow(m._STATEMENTS["attach"], *args))
                    try:
                        await db.wait_lock(admin, attach.get_server_pid())
                    finally:
                        await delete.execute("COMMIT")
                    with pytest.raises(asyncpg.RaiseError, match="metadata_account_unavailable"):
                        await job
                assert (
                    await owner.fetchval(
                        "SELECT count(*) FROM compute_reservation_metadata WHERE reservation_id=$1",
                        r,
                    )
                    == 0
                )
                db.cluster.record(
                    "attach_delete_order", attach_first=attach_first, real_waiter=True
                )


@pytest.mark.asyncio
async def test_missing_required_function_refuses_before_any_write(db):
    async with db.connection() as owner, db.pool() as pool:
        a = await db.account(owner)
        ident = m.new_scope_metadata_identity(account_id=a)
        await owner.execute(
            "ALTER FUNCTION public.compute_metadata_read_scope(uuid,uuid) RENAME TO md_hidden"
        )
        try:
            with pytest.raises(m.ComputeMetadataUnavailable) as caught:
                await m.ComputeMetadataStore(pool).create_scope(ident)
            assert caught.value.identity is ident and not caught.value.commit_acknowledged
            assert await owner.fetchval("SELECT count(*) FROM compute_scope_metadata") == 0
        finally:
            await owner.execute(
                "ALTER FUNCTION public.md_hidden(uuid,uuid) RENAME TO compute_metadata_read_scope"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt", ["cancel", "deadline"])
async def test_acknowledged_write_release_interruption_retains_driver_ownership(
    db, interrupt, monkeypatch
):
    entered, finish = asyncio.Event(), asyncio.Event()
    resets = []
    backend = []
    armed = False

    async def paused_reset(conn):
        if not armed:
            return
        # Synthetic reset pause around REAL SQL, COMMIT and native Pool.release.
        backend.append(conn.get_server_pid())
        resets.append(asyncio.current_task())
        entered.set()
        await finish.wait()

    async with db.connection() as owner, db.pool(reset=paused_reset, max_size=1) as pool:
        account = await db.account(owner)
        ident = m.new_scope_metadata_identity(account_id=account)
        # Connect through Docker/bridge before shortening the operation budget.
        async with pool.acquire(timeout=2) as warm:
            assert await warm.fetchval("SELECT 1") == 1
        armed = True
        if interrupt == "deadline":
            monkeypatch.setattr(m, "OPERATION_S", 0.3)
        job = asyncio.create_task(m.ComputeMetadataStore(pool).create_scope(ident))
        try:
            async with asyncio.timeout(2):
                await entered.wait()
            if interrupt == "cancel":
                job.cancel("original metadata release cancellation")
            with pytest.raises(
                asyncio.CancelledError
                if interrupt == "cancel"
                else m.ComputeMetadataCommittedResultUnavailable
            ) as caught:
                async with asyncio.timeout(2):
                    await job
            if interrupt == "cancel":
                assert caught.value.args == ("original metadata release cancellation",)
                assert getattr(caught.value, "metadata_identity") is ident
                assert getattr(caught.value, "metadata_outcome") == "committed"
                assert not getattr(caught.value, "metadata_termination_failed")
            else:
                assert isinstance(caught.value, m.ComputeMetadataCommittedResultUnavailable)
                assert caught.value.identity is ident and caught.value.commit_acknowledged
                assert not caught.value.termination_failed
            # Before native release's own 0.5s allowance expires, the paused reset
            # still owns the sole lease. No claim that the pause disables that cap.
            assert pool.get_size() == 1 and pool.get_idle_size() == 0
        finally:
            finish.set()
            if not job.done():
                job.cancel()
            async with asyncio.timeout(2):
                await asyncio.gather(job, *resets, return_exceptions=True)
        async with pool.acquire(timeout=0.5) as next_conn:
            assert next_conn.get_server_pid() == backend[0]
            assert await next_conn.fetchval("SELECT 1") == 1
        async with db.pool() as fresh:
            row = await m.ComputeMetadataStore(fresh).read_scope(
                account_id=account, metadata_scope_id=ident.metadata_scope_id
            )
            assert row is not None and row.identity == ident
        db.cluster.record(
            "metadata_acknowledged_release_interruption",
            interrupt=interrupt,
            identity_preserved=True,
            commit_acknowledged=True,
            native_holder_retained=True,
            committed_readback=True,
            synthetic_reset_pause=True,
            remote_stop_certified=False,
        )
