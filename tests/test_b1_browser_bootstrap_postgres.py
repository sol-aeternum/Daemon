"""Actual B1 HTTPS/production-Next/SW/full-lifespan qualification, owned PG only.

No devices/sessions or accepted work are seeded. Singleton identity/entitlement,
foreign conversation fixtures and H material ownership are test-only. A prior
worker reproduces the old rule boundary; it is not a historical production build.
Two contexts are two singleton devices, not proof of a distinct-account sign-in.
Physical phones, live privileges/deployment and historical-worker provenance are
not qualified here. No inference, background worker, hosted auth or live flags.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket
import stat
from types import SimpleNamespace
from typing import ClassVar
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
import httpx
import pytest
import pytest_asyncio
from pydantic_settings import SettingsConfigDict
import uvicorn

from orchestrator import config, main
from orchestrator.config import Settings
from orchestrator.entitlements import attestation
from orchestrator.memory.store import MemoryStore
from orchestrator.routes import auth_config, auth_setup
from orchestrator.routes import tasks as task_routes
from orchestrator.services.deletion_journal.store import initialize
from orchestrator.services.identity import rate_limit_dep
from orchestrator.services.restore_history import runtime
from orchestrator.services.restore_history.configuration import (
    HistoryConfiguration,
    HistoryMaterial,
)
from tests.b1_browser_support import (
    FRONTEND,
    ROOT,
    JsonChild,
    certificate,
    node_environment,
    production_build,
    reserve,
)
from tests.exact_worker_privileges import MAP
from tests.restore_history_support import KEY
from tests.test_history_acceptance_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
)
from tests.test_history_effects_postgres import install
from tests.test_restore_history_store import open_store, owned

SINGLETON = UUID("00000000-0000-0000-0000-000000000001")
H_EXECUTE = {
    "md_app": {
        "task_history_acceptance_page(uuid,bigint,text)",
        "task_history_page(uuid,bigint,text)",
        "task_history_send_project(bytea)",
        "task_history_stop_project(bytea)",
    },
    "md_acceptor": {
        "task_history_acceptance_page(uuid,bigint,text)",
        "task_history_acceptance_project(bytea)",
        "task_history_page(uuid,bigint,text)",
    },
}
COUNTS = (
    "devices",
    "sessions",
    "pending_enrollments",
    "conversations",
    "messages",
    "tasks",
    "task_events",
    "attempt_lifecycle_control",
    "attempt_lifecycle_commands",
    "task_inference_recovery",
    "entitlement_reservations",
    "entitlement_usage_periods",
    "task_history_acceptance",
    "task_history_projection",
)


class FixtureSettings(Settings):
    """Use explicit synthetic init values only, never dotenv or host credentials."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(env_file=None, extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)


class OwnedHost(SimpleNamespace):
    def __repr__(self):
        return "<owned B1 fixture; synthetic auth material suppressed>"


async def forbidden(*_args, **_kwargs):
    raise AssertionError("B1 H fixture attempted forbidden background work")


@pytest_asyncio.fixture
async def b1_host(projection_db, tmp_path, monkeypatch):
    database = projection_db
    install(database)  # Schema/profile only: unlike effects(), accepts no task.
    async with database.connection() as owner:
        await owner.execute(
            "INSERT INTO users(id,username) VALUES($1,'b1-singleton') ON CONFLICT(id) DO NOTHING",
            SINGLETON,
        )
        await owner.execute(
            "INSERT INTO entitlement_accounts(user_id) VALUES($1) ON CONFLICT(user_id) DO NOTHING",
            SINGLETON,
        )
        foreign = await database.account(owner)
        conversation = await owner.fetchval(
            "INSERT INTO conversations(user_id,title,pipeline) VALUES($1,'B1 foreign fictional','cloud') RETURNING id",
            foreign,
        )
        assert await owner.fetchval("SELECT count(*) FROM devices") == 0
        assert await owner.fetchval("SELECT count(*) FROM sessions") == 0
        assert await owner.fetchval("SELECT count(*) FROM tasks") == 0
    history_path = tmp_path / "h"
    identity, ownership = owned(history_path)
    journal, journal_id = tmp_path / "j1", uuid4()
    initialize(journal, journal_id)
    listeners = {name: reserve() for name in ("edge", "next", "backend")}
    ports = {name: listener.getsockname()[1] for name, listener in listeners.items()}
    origin = f"https://127.0.0.1:{ports['edge']}"
    from urllib.parse import urlencode

    def dsn(principal):
        return (
            f"postgresql://{principal}@/{database.name}?{urlencode({'host': str(database.host)})}"
        )

    settings = FixtureSettings(
        durable_chat_enabled=True,
        redis_url=None,
        daemon_environment="development",
        daemon_deployment_mode="self_hosted",
        daemon_encryption_key=Fernet.generate_key().decode(),
        daemon_auth_pepper="b" * 64,
        openrouter_api_key="synthetic-no-network",
        database_url=dsn("md_app"),
        daemon_task_acceptor_database_url=dsn("md_acceptor"),
        daemon_task_acceptor_role="md_acceptor",
        daemon_task_worker_role="md_app",
        daemon_deletion_journal_dir=str(journal),
        daemon_deletion_journal_id=str(journal_id),
        daemon_setup_token_file=str(tmp_path / "setup-token"),
        daemon_cookie_secure=True,
        daemon_public_origin=origin,
        daemon_allowed_origins=origin,
        daemon_allowed_hosts="127.0.0.1",
        daemon_restore_history_dir=str(history_path),
        daemon_restore_history_expected_file=str(tmp_path / "operator" / "expected"),
        daemon_restore_history_operator_uid="0",
        daemon_restore_history_backend_uid=str(os.getuid()),
        daemon_restore_history_worker_uid=str(os.getuid() + 1),
        daemon_restore_history_group_gid=str(os.getgid()),
        daemon_restore_history_key_gid=str(os.getgid() + 1),
        daemon_restore_history_key_file=str(tmp_path / "material" / "synthetic"),
    )
    original_settings = config.get_settings
    for module in (config, main, auth_config, auth_setup, task_routes, rate_limit_dep):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    main.app.dependency_overrides[original_settings] = lambda: settings
    monkeypatch.setattr(main.app.state, "app_state", None, raising=False)
    monkeypatch.setattr(main.app.state, "settings", settings, raising=False)
    monkeypatch.setattr(HistoryConfiguration, "ownership", property(lambda _: ownership))
    monkeypatch.setattr(
        runtime,
        "load_material",
        lambda configuration, service, *, deadline: HistoryMaterial(
            identity, KEY if service == "http" else None
        ),
    )
    for module, name in (
        (attestation, "start"),
        (attestation, "stop"),
        (main, "_publish_shipped_skills"),
        (main, "cleanup_stale_sessions"),
        (main, "start_session_cleanup_task"),
        (MemoryStore, "backfill_memory_content_hashes"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    # Python is only allowed to connect to the fixture's Unix PG socket. Node
    # and Chromium own the loopback HTTP path and have independent egress guards.
    native_connect = socket.socket.connect
    native_connect_ex = socket.socket.connect_ex

    def local_connect(sock, address):
        assert sock.family == socket.AF_UNIX, "B1 Python attempted network egress"
        return native_connect(sock, address)

    def local_connect_ex(sock, address):
        assert sock.family == socket.AF_UNIX, "B1 Python attempted network egress"
        return native_connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", local_connect_ex)
    host = OwnedHost(
        db=database,
        settings=settings,
        listeners=listeners,
        ports=ports,
        origin=origin,
        path=history_path,
        identity=identity,
        ownership=ownership,
        directory=tmp_path,
        foreign_conversation=conversation,
    )
    try:
        async with main.lifespan(main.app):
            host.state = main.app.state.app_state
            assert host.state.history_runtime.ready(host.state)
            assert host.state.redis is None
            await exact_acl(host.state)
            yield host
        assert host.state.db_pool._closed and host.state.task_acceptor_pool._closed
        assert host.state.history_runtime.history._closed and host.state.history_observation._closed
        assert host.state._history_shutdown_task.done()
        with open_store(history_path, identity, ownership) as history:
            assert history.verify(deadline=__import__("time").monotonic() + 5).entries == ()
    finally:
        main.app.dependency_overrides.pop(original_settings, None)
        for listener in listeners.values():
            listener.close()


async def exact_acl(state):
    """Recheck effective authority on the actual H-selected application pool."""
    async with state.db_pool.acquire() as worker:
        assert (
            await worker.fetchval("SELECT current_user=session_user AND current_user='md_app'")
            is True
        )
        for principal in ("md_owner", "md_acceptor"):
            assert (
                await worker.fetchval("SELECT pg_has_role(current_user,$1,'MEMBER')", principal)
                is False
            )
        for entry in MAP["ordinary_tables"]:
            for privilege in (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
            ):
                assert await worker.fetchval(
                    "SELECT has_table_privilege(current_user,$1,$2)", entry["table"], privilege
                ) is (privilege in entry["table_privileges"])
            columns = await worker.fetch(
                "SELECT attname FROM pg_attribute WHERE attrelid=$1::regclass AND attnum>0 AND NOT attisdropped",
                entry["table"],
            )
            for column in columns:
                assert await worker.fetchval(
                    "SELECT has_column_privilege(current_user,$1,$2,'UPDATE')",
                    entry["table"],
                    column["attname"],
                ) is (
                    "UPDATE" in entry["table_privileges"]
                    or column["attname"] in entry["update_columns"]
                )
        for table in MAP["protected_tables_no_direct_privileges"]:
            assert (
                await worker.fetchval(
                    "SELECT has_table_privilege(current_user,$1,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')",
                    table,
                )
                is False
            )
            assert (
                await worker.fetchval(
                    "SELECT has_any_column_privilege(current_user,$1,'SELECT,INSERT,UPDATE,REFERENCES')",
                    table,
                )
                is False
            )
        for row in await worker.fetch(
            "SELECT c.oid::regclass::text AS name FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='S'"
        ):
            name = "public." + row["name"].removeprefix("public.")
            assert await worker.fetchval(
                "SELECT has_sequence_privilege(current_user,$1,'USAGE')", name
            ) is (name in MAP["sequences_usage_only"])
            assert (
                await worker.fetchval(
                    "SELECT has_sequence_privilege(current_user,$1,'UPDATE')", name
                )
                is False
            )
        for signature in MAP["ordinary_helper_execute"] + MAP["worker_public_entrypoints_execute"]:
            assert (
                await worker.fetchval(
                    "SELECT has_function_privilege(current_user,$1,'EXECUTE')", signature
                )
                is True
            )
        for helper in (
            "compute_metadata_check_account(uuid)",
            "attempt_lifecycle_identity(jsonb)",
            "attempt_lifecycle_lock(jsonb)",
            "attempt_lifecycle_live(jsonb)",
            "task_inference_recovery_immutable()",
        ):
            assert (
                await worker.fetchval(
                    "SELECT has_function_privilege(current_user,$1,'EXECUTE')", "public." + helper
                )
                is False
            )
        assert (
            await worker.fetchval(
                "SELECT has_table_privilege(current_user,'public._migrations','SELECT,INSERT,UPDATE,DELETE')"
            )
            is False
        )
        assert (
            await worker.fetchval(
                "SELECT has_table_privilege(current_user,'public.deletion_journal_binding','INSERT,UPDATE,DELETE')"
            )
            is False
        )
    # Full effective H function enumeration, not a single 'H page works' flag.
    for principal, pool in (("md_app", state.db_pool), ("md_acceptor", state.task_acceptor_pool)):
        async with pool.acquire() as connection:
            assert (
                await connection.fetchval(
                    "SELECT current_user=session_user AND current_user=$1", principal
                )
                is True
            )
            assert (
                await connection.fetchval("SELECT pg_has_role(current_user,'md_owner','MEMBER')")
                is False
            )
            rows = await connection.fetch(
                "SELECT p.oid::regprocedure::text AS signature, has_function_privilege(current_user,p.oid,'EXECUTE') AS allowed FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname LIKE 'task_history_%'"
            )
            actual = {row["signature"].removeprefix("public.") for row in rows if row["allowed"]}
            assert actual == H_EXECUTE[principal]


async def snapshot(host):
    async with host.db.connection() as owner:
        counts = tuple([await owner.fetchval("SELECT count(*) FROM " + name) for name in COUNTS])
        preferences = await owner.fetchval(
            "SELECT preferences::text FROM users WHERE id=$1", SINGLETON
        )
        seen = tuple(await owner.fetch("SELECT id,last_seen_at FROM devices ORDER BY id"))
        return counts, preferences, seen


def private(result, status=200, *, empty=None, code=None):
    assert result.get("status") == status
    assert "no-store" in (result.get("cache") or "")
    assert result.get("marker") is False
    if empty is not None:
        assert result.get("emptyConversations") is empty
    if code is not None:
        assert result.get("code") == code


async def own_history_counts(host):
    """The singleton's own content, distinct from the permitted foreign fixture."""
    async with host.db.connection() as owner:
        return {
            "conversations": await owner.fetchval(
                "SELECT count(*) FROM conversations WHERE user_id=$1", SINGLETON
            ),
            "messages": await owner.fetchval(
                "SELECT count(*) FROM messages WHERE user_id=$1", SINGLETON
            ),
            "tasks": await owner.fetchval("SELECT count(*) FROM tasks WHERE user_id=$1", SINGLETON),
        }


async def delayed_refresh_phases(host, edge, browser):
    """Actual committed response held before headers / during body, no mocks."""
    receipts = []
    protected_reads = {
        "/daemon/conversations",
        "/daemon/users/me/settings",
        "/daemon/users/me/entitlements",
        "/api/entitlements",
        "/api/v1/auth/devices",
        "/daemon/v1/auth/devices",
        "/daemon/home-suggestions",
    }
    refresh_dispatch = {"method": "POST", "path": "/daemon/v1/auth/refresh"}
    for phase in ("headers", "body"):
        await browser.command("prepare-held-refresh")
        async with host.db.connection() as owner:
            before_ids = [row["id"] for row in await owner.fetch("SELECT id FROM sessions")]
        before = await edge.command("state")
        await edge.command("hold-refresh", phase=phase)
        try:
            await browser.command("start-held-reload")
            # A bounded awaited predicate, not Promise-truthy browser polling.
            async with asyncio.timeout(4):
                while True:
                    held = await edge.command("state")
                    if held.get("heldRefresh", {}).get("backendComplete"):
                        break
                    await asyncio.sleep(0.025)
            assert held["heldRefresh"] == {
                "phase": phase,
                "backendComplete": True,
                "status": 200,
                "headersSent": phase == "body",
                "released": False,
            }
            async with host.db.connection() as owner:
                new_sessions = await owner.fetch(
                    "SELECT id,refresh_consumed_at,revoked_at FROM sessions WHERE NOT (id=ANY($1::uuid[]))",
                    before_ids,
                )
                assert len(new_sessions) == 1
                successor = new_sessions[0]
                assert successor["refresh_consumed_at"] is None and successor["revoked_at"] is None
                assert (
                    await owner.fetchval(
                        "SELECT s.refresh_consumed_at IS NOT NULL FROM refresh_rotation_grace g "
                        "JOIN sessions s ON s.id=g.predecessor_session_id WHERE g.successor_session_id=$1",
                        successor["id"],
                    )
                    is True
                )
            observed = await browser.command("inspect-held-refresh", phase=phase)
            for key in (
                "pending",
                "kindRefresh",
                "attemptPresent",
                "sameAttempt",
                "epochUnchanged",
                "lockUnavailable",
                "gateVisible",
            ):
                assert observed[key] is True, key
            assert observed["headersSeen"] is (phase == "body")
            assert observed["refreshStatus"] == (200 if phase == "body" else None)
            assert observed["bodyFinished"] is False and observed["failed"] is False
            assert observed["protectedWidgetVisible"] is False
            during = await edge.command("state")
            window = during["requests"][len(before["requests"]) :]
            assert window.count(refresh_dispatch) == 1
            assert not [r for r in window if r["method"] == "GET" and r["path"] in protected_reads]
        finally:
            # Release through private IPC even when an assertion fails. Never
            # leave a credential-bearing buffered response beyond owned cleanup.
            await edge.command("release-refresh")
        finished = await browser.command("finish-held-refresh")
        assert finished == {
            "stable": True,
            "epochUnchanged": True,
            "lockAvailable": True,
            "bodyFinished": True,
            "refreshStatus": 200,
            "failed": False,
            "gateVisible": False,
            "protectedWidgetVisible": True,
        }
        after = await edge.command("state")
        assert after["requests"][len(before["requests"]) :].count(refresh_dispatch) == 1
        async with host.db.connection() as owner:
            assert await owner.fetchval("SELECT count(*) FROM sessions") == len(before_ids) + 1
            for table in (
                "tasks",
                "task_events",
                "attempt_lifecycle_control",
                "attempt_lifecycle_commands",
                "task_inference_recovery",
                "entitlement_reservations",
                "task_history_acceptance",
                "task_history_projection",
            ):
                assert await owner.fetchval("SELECT count(*) FROM " + table) == 0
        receipts.append(
            {
                "phase": phase,
                "committed_successors": 1,
                "upstream_dispatches": 1,
                "while_held": observed,
                "after_release": finished,
                "scope": "real-delay-before-browser-termination-not-abort-ignoring",
            }
        )
    return receipts


@pytest.mark.asyncio
async def test_b1_actual_https_production_browser_empty_history(b1_host):
    host = b1_host
    # No own content or accepted work exists before a real browser session; the
    # single foreign conversation belongs to another fixture account and is a
    # permitted negative fixture, not history of this singleton.
    own_before = await own_history_counts(host)
    assert own_before == {"conversations": 0, "messages": 0, "tasks": 0}
    async with host.db.connection() as owner:
        assert (
            await owner.fetchval("SELECT count(*) FROM conversations WHERE user_id<>$1", SINGLETON)
            == 1
        )
    cert, key = certificate(host.directory)
    env = node_environment(host.directory, host.origin, cert)
    children = []
    server = uvicorn.Server(
        uvicorn.Config(main.app, log_level="critical", access_log=False, lifespan="off")
    )
    serving = asyncio.create_task(server.serve(sockets=[host.listeners["backend"]]))
    receipt = {
        "topology": "owned-https-edge/daemon-production-next-real-main-H-PG",
        "distinct_account_switch": False,
        "prior_worker": "synthetic-rule-boundary-reproduction-not-historical-production",
    }
    try:
        # Listener reservations exist before the build fixes NEXT_PUBLIC_API_URL.
        await production_build(env)
        nonce = uuid4().hex
        packet = host.directory / "edge-packet.json"
        packet.write_text(
            json.dumps(
                {
                    "nonce": nonce,
                    "port": host.ports["edge"],
                    "backendPort": host.ports["backend"],
                    "nextPort": host.ports["next"],
                    "fd": host.listeners["edge"].fileno(),
                    "key": str(key),
                    "cert": str(cert),
                }
            )
        )
        edge = await JsonChild.start(
            FRONTEND / "e2e/b1-fixture-edge.mjs",
            [str(packet)],
            env,
            fds=(host.listeners["edge"].fileno(),),
        )
        children.append(edge)
        assert (await edge.frame())["event"] == "bound"
        next_server = await JsonChild.start(
            FRONTEND / "e2e/b1-fixture-next.mjs",
            [str(host.ports["next"]), nonce, str(host.listeners["next"].fileno())],
            env,
            fds=(host.listeners["next"].fileno(),),
        )
        children.append(next_server)
        assert (await next_server.frame())["event"] == "ready"
        await edge.command("arm")
        browser = await JsonChild.start(ROOT / "tests/b1_browser_clients.mjs", [], env)
        children.append(browser)
        await browser.command("launch", origin=host.origin)
        prior = await browser.command("prior")
        assert prior["prefixOfflineDenied"] and prior["prefixOnline401"]
        receipt["old_controller_window"] = prior
        upgraded = await browser.command("upgrade")
        assert upgraded["privateEntries"] == 0
        assert upgraded["preserved"] == upgraded["expectedPreserved"]
        receipt["activation_cleanup"] = upgraded
        token_file = Path(host.settings.daemon_setup_token_file)
        assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
        setup = await browser.command("setup", token=token_file.read_text().strip())
        assert not token_file.exists()
        enrolled = await browser.command("enroll")
        for result in (setup, enrolled):
            assert result["cookie"] == {
                "secure": True,
                "httpOnly": True,
                "sameSite": "Strict",
                "hostOnly": True,
                "path": "/",
                "hidden": True,
            }
        assert enrolled["distinctAccess"] is True
        async with host.db.connection() as owner:
            assert (
                await owner.fetchval("SELECT count(*) FROM devices WHERE revoked_at IS NULL") == 2
            )
            assert await owner.fetchval("SELECT count(DISTINCT user_id) FROM devices") == 1
            assert await owner.fetchval("SELECT count(*) FROM tasks") == 0
        # Let startup hooks finish, then prove this is a live last_seen probe.
        async with host.db.connection() as owner:
            await owner.execute("UPDATE devices SET last_seen_at=now()-interval '1 day'")
        private(
            await browser.command("fetch", path="/daemon/conversations"),
            empty=True,
        )
        async with host.db.connection() as owner:
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM devices WHERE last_seen_at>now()-interval '5 minutes'"
                )
                == 1
            )
        for index in (0, 1):
            for path in (
                "/daemon/conversations",
                "/daemon/users/me/settings",
                "/daemon/users/me/entitlements",
                "/api/entitlements",
                "/daemon/v1/catalog",
            ):
                private(
                    await browser.command("fetch", index=index, path=path),
                    empty=True if path == "/daemon/conversations" else None,
                )
        receipt["delayed_refresh_ownership"] = await delayed_refresh_phases(host, edge, browser)
        decode_baseline = await edge.command("state")
        decoded = await browser.command("decode-probes")
        assert decoded["total"] == len(decoded["observations"])
        for observation in decoded["observations"]:
            assert observation.get("marker") is False
            assert observation.get("status") in (200, 400, 401, 404, 422, 503)
        decode_after = await edge.command("state")
        assert any(
            r["path"].startswith("/daemon/conversations")
            for r in decode_after["requests"][len(decode_baseline["requests"]) :]
        )
        receipt["supplemental_edge_cache_paths"] = decoded
        # Real mounted plan read: positive with the fixture entitlement, then the
        # fixture owner removes ONLY that row and the same mounted proxy read
        # must show unavailable with no stale allowance and no recreated row.
        plan_ready = await browser.command("plan-status")
        assert plan_ready["settled"] == "ready", plan_ready
        assert plan_ready["currentPlan"] is True
        assert plan_ready["freeHeadline"] is True
        assert plan_ready["serviceUnavailable"] is False
        async with host.db.connection() as owner:
            entitlement = await owner.fetchval(
                "SELECT row_to_json(t)::text FROM entitlement_accounts t WHERE user_id=$1",
                SINGLETON,
            )
            assert entitlement is not None
            await owner.execute("DELETE FROM entitlement_accounts WHERE user_id=$1", SINGLETON)
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM entitlement_accounts WHERE user_id=$1",
                    SINGLETON,
                )
                == 0
            )
        plan_missing = await browser.command("plan-status")
        assert plan_missing["settled"] == "unavailable", plan_missing
        assert plan_missing["currentPlan"] is False
        assert plan_missing["freeHeadline"] is False
        assert plan_missing["serviceUnavailable"] is True
        assert plan_missing["retry"] is True
        # Distinguish the plan 503 from an auth failure: the same session still
        # reads its own content, and the plan handler refuses without repair.
        private(
            await browser.command("fetch", path="/daemon/conversations"),
            empty=True,
        )
        plan_refusal = await browser.command("fetch", path="/daemon/users/me/entitlements")
        private(plan_refusal, 503, code="entitlements_unavailable")
        async with host.db.connection() as owner:
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM entitlement_accounts WHERE user_id=$1",
                    SINGLETON,
                )
                == 0
            )
            # Restore the identical fixture row only after the negative proof.
            await owner.execute(
                "INSERT INTO entitlement_accounts SELECT * FROM"
                " json_populate_record(null::entitlement_accounts, $1::json)",
                entitlement,
            )
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM entitlement_accounts WHERE user_id=$1",
                    SINGLETON,
                )
                == 1
            )
        receipt["plan_read"] = {
            "positive": plan_ready,
            "removed_only_singleton_entitlement": True,
            "negative": plan_missing,
            "plan_503_code": plan_refusal["code"],
            "session_read_while_plan_503": 200,
            "row_recreated": False,
            "fixture_row_restored": True,
        }
        # Home suggestions: real mounted panel, H-refused backend read, no
        # fabricated success/disabled state and no automatic refresh/polling.
        baseline = await edge.command("state")
        suggestions = await browser.command("home-suggestions")
        assert suggestions["settled"] == "unavailable", suggestions
        assert suggestions["rows"] == 0
        assert suggestions["disabledNote"] is False
        assert suggestions["retryVisible"] is True
        assert suggestions["manageLink"] is True
        await asyncio.sleep(3.0)
        after_wait = await edge.command("state")
        window = after_wait["requests"][len(baseline["requests"]) :]
        suggestion_reads = [
            request
            for request in window
            if request == {"method": "GET", "path": "/daemon/home-suggestions"}
        ]
        suggestion_refreshes = [
            request
            for request in window
            if request == {"method": "POST", "path": "/daemon/home-suggestions/refresh"}
        ]
        assert len(suggestion_reads) == 1, window
        assert suggestion_refreshes == [], window
        receipt["home_suggestions"] = {
            "visible_state": suggestions["settled"],
            "rows": suggestions["rows"],
            "personal_off_note": suggestions["disabledNote"],
            "retry_visible": suggestions["retryVisible"],
            "manage_link_visible": suggestions["manageLink"],
            "bounded_window_seconds": 3,
            "backend_reads": len(suggestion_reads),
            "refresh_posts": len(suggestion_refreshes),
        }
        # CSRF must not consume/rotate a real cookie-backed session.
        before = await snapshot(host)
        assert (await browser.command("csrf"))["status"] == 403
        assert await snapshot(host) == before
        # Wrong-method/adjacent/unknown H refusals precede auth dependency work.
        async with host.db.connection() as owner:
            await owner.execute("UPDATE devices SET last_seen_at=now()-interval '1 day'")
        before = await snapshot(host)
        for method, path in (
            ("PATCH", "/daemon/users/me/settings"),
            ("POST", "/daemon/conversations"),
            ("GET", "/daemon/users/me/settings/presets"),
            ("GET", "/daemon/v1/models"),
            ("GET", "/daemon/resources"),
            ("GET", "/daemon/resources/" + str(uuid4())),
            ("POST", "/daemon/chat"),
            ("GET", "/daemon/unknown-b1"),
        ):
            private(
                await browser.command(
                    "fetch",
                    path=path,
                    method=method,
                    body={} if method in ("POST", "PATCH") else None,
                ),
                503,
            )
        assert await snapshot(host) == before
        # Owner-scoped conversation negatives go through native ownership checks.
        for path in (
            f"/daemon/conversations/{host.foreign_conversation}",
            f"/daemon/conversations/{uuid4()}",
        ):
            private(await browser.command("fetch", path=path), 404)
        private(await browser.command("fetch", path="/daemon/conversations", anonymous=True), 401)
        # Real Next proxy transport tests, not protected response substitutes.
        for code in (307, 308):
            result = await browser.command(
                "fetch", path=f"/api/v1/auth/__b1_redirect_{code}", method="POST"
            )
            private(result, 502)
            assert result["opaque"]
        headers = await browser.command("headers")
        assert headers == {"status": 204, "count": 2}
        edge_state = await edge.command("state")
        assert edge_state["redirectTargets"] == 0 and edge_state["headerProbes"] == 1
        receipt["next_transport"] = {
            "redirects_rejected": [307, 308],
            "target_hits": 0,
            "set_cookie_count": 2,
        }
        offline = await browser.command("cache-offline")
        receipt["offline_observations"] = offline
        assert "diagnosticFailure" not in offline, "offline fixture stage failed"
        assert offline["denied"] == offline["total"]
        assert offline["preserved"] == offline["expectedPreserved"]
        receipt["offline"] = offline
        receipt["credential_lifetime"] = await browser.command("lifetime")
        # A real handler commits rotation; edge drops delivery BEFORE Next sees
        # headers. No fake protected 200 or database commit-fault substitute.
        await browser.command("save-cookie", label="lost-predecessor")
        edge_before = await edge.command("state")
        async with host.db.connection() as owner:
            sessions_before = await owner.fetchval("SELECT count(*) FROM sessions")
            session_ids_before = [row["id"] for row in await owner.fetch("SELECT id FROM sessions")]
        await edge.command("drop-refresh")
        lost = await browser.command("reload")
        assert lost["status"] == "uncertain", lost
        receipt["lost_reload"] = lost
        assert lost["refreshStatus"] == 502
        edge_after = await edge.command("state")
        requests = edge_after["requests"][len(edge_before["requests"]) :]
        assert (
            sum(r == {"method": "POST", "path": "/daemon/v1/auth/refresh"} for r in requests) == 1
        )
        assert edge_after["dropped"] == 1
        async with host.db.connection() as owner:
            assert await owner.fetchval("SELECT count(*) FROM sessions") == sessions_before + 1
            grace = await owner.fetchrow(
                "SELECT predecessor_session_id AS predecessor,"
                " successor_session_id AS successor,"
                " extract(epoch FROM (grace_expires_at - created_at)) AS window"
                " FROM refresh_rotation_grace WHERE NOT (successor_session_id=ANY($1::uuid[]))",
                session_ids_before,
            )
            assert grace is not None
            # The real rotation handler created the unchanged 30-second window.
            assert 25 <= float(grace["window"]) <= 35
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM refresh_rotation_grace g JOIN sessions s ON s.id=g.successor_session_id WHERE g.predecessor_session_id=$1 AND s.refresh_consumed_at IS NULL AND s.revoked_at IS NULL",
                    grace["predecessor"],
                )
                == 1
            )
        one = await browser.command(
            "probe-old",
            label="lost-predecessor",
            resultLabel="same-successor",
            captureCookieLabel="successor-cookie",
        )
        two = await browser.command(
            "probe-old", label="lost-predecessor", resultLabel="same-successor"
        )
        assert one["status"] == two["status"] == 200 and two["sameSuccessor"]
        assert one["cookieClear"] is False and two["cookieClear"] is False
        async with host.db.connection() as owner:
            unchanged = await owner.fetchrow(
                "SELECT successor_session_id AS successor FROM refresh_rotation_grace WHERE predecessor_session_id=$1",
                grace["predecessor"],
            )
            assert unchanged is not None
            assert unchanged["successor"] == grace["successor"]
        used = await browser.command("probe-old", label="successor-cookie")
        assert used["status"] == 200
        async with host.db.connection() as owner:
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM refresh_rotation_grace WHERE predecessor_session_id=$1",
                    grace["predecessor"],
                )
                == 0
            )
            assert (
                await owner.fetchval(
                    "SELECT refresh_consumed_at IS NOT NULL FROM sessions WHERE id=$1",
                    grace["successor"],
                )
                is True
            )
        reused = await browser.command("probe-old", label="lost-predecessor")
        # A real reuse refusal also clears the refresh cookie in its headers.
        assert reused["status"] == 401 and reused["cookieClear"] is True
        private(await browser.command("fetch", index=1, path="/daemon/conversations"))
        async with host.db.connection() as owner:
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM sessions s JOIN devices d ON d.id=s.device_id WHERE d.revoked_at IS NOT NULL AND s.revoked_at IS NULL"
                )
                == 0
            )
        receipt["lost_delivery"] = {
            "committed": True,
            "dispatches": 1,
            "client_state": "uncertain",
            "grace_seconds": 30,
            "grace_same_successor": True,
            "used_successor_reuse_401": True,
            "reuse_cookie_clear": True,
        }
        await browser.command("reenroll")
        await browser.command("save-cookie", label="outside-grace")
        async with host.db.connection() as owner:
            before_outside_ids = [row["id"] for row in await owner.fetch("SELECT id FROM sessions")]
        inside = await browser.command("probe-old", label="outside-grace")
        assert inside["status"] == 200
        async with host.db.connection() as owner:
            await owner.execute(
                "UPDATE refresh_rotation_grace SET grace_expires_at=now()-interval '1 second' WHERE NOT (successor_session_id=ANY($1::uuid[]))",
                before_outside_ids,
            )
        outside = await browser.command("probe-old", label="outside-grace")
        assert outside["status"] == 401 and outside["cookieClear"] is True
        # Explicit native revocation of an actually enrolled second device:
        # enroll again through the real flow, then DELETE that other device over
        # the real Next auth proxy and prove the caller's device stays valid.
        await browser.command("reenroll")
        async with host.db.connection() as owner:
            active_before_revoke = await owner.fetchval(
                "SELECT count(*) FROM devices WHERE revoked_at IS NULL"
            )
        revoked = await browser.command("revoke-other", index=1)
        assert revoked["revoked"] is True and revoked["status"] in (200, 204)
        assert revoked["activeBefore"] == active_before_revoke
        private(await browser.command("fetch", index=1, path="/daemon/users/me/entitlements"))
        private(await browser.command("fetch", index=0, path="/daemon/conversations"), 401)
        async with host.db.connection() as owner:
            active_after_revoke = await owner.fetchval(
                "SELECT count(*) FROM devices WHERE revoked_at IS NULL"
            )
            assert active_after_revoke == active_before_revoke - 1
            assert revoked["active"] == active_after_revoke
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM sessions s JOIN devices d ON d.id=s.device_id WHERE d.revoked_at IS NOT NULL AND s.revoked_at IS NULL"
                )
                == 0
            )
        receipt["explicit_device_revoke"] = {
            "native_delete_status": revoked["status"],
            "other_device_still_valid": True,
            "revoked_device_read_401": True,
            "active_devices_before": active_before_revoke,
            "active_devices_after": active_after_revoke,
            "sql_session_seeding": False,
        }
        receipt["grace_expiry_cookie_clear"] = True
        # Expiry/revocation negatives use actual issued sessions, never SQL auth seeds.
        async with host.db.connection() as owner:
            await owner.execute(
                "UPDATE sessions SET access_expires_at=now()-interval '1 second' WHERE revoked_at IS NULL"
            )
        private(await browser.command("fetch", index=1, path="/daemon/conversations"), 401)
        async with host.db.connection() as owner:
            for name in (
                "tasks",
                "messages",
                "attempt_lifecycle_control",
                "task_inference_recovery",
                "entitlement_reservations",
                "task_history_acceptance",
                "task_history_projection",
            ):
                assert await owner.fetchval("SELECT count(*) FROM " + name) == 0
        own_after = await own_history_counts(host)
        assert own_after == own_before == {"conversations": 0, "messages": 0, "tasks": 0}
        async with host.db.connection() as owner:
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM conversations WHERE user_id<>$1", SINGLETON
                )
                == 1
            )
        receipt["empty_history_no_execution"] = True
        receipt["criteria"] = {
            "authenticated_empty_history": {
                "own_conversations_before": own_before["conversations"],
                "own_conversations_after": own_after["conversations"],
                "own_messages_after": own_after["messages"],
                "own_tasks_after": own_after["tasks"],
                "foreign_conversation_preserved": True,
                "accepted_work": 0,
            },
            "plan_unavailable_without_stale_allowance": {
                "positive_plan_visible": plan_ready["settled"],
                "negative_plan_visible": plan_missing["settled"],
                "stale_allowance_absent": plan_missing["freeHeadline"] is False,
                "plan_503_code": plan_refusal["code"],
                "session_read_while_plan_503": 200,
                "entitlement_row_recreated": False,
                "fixture_row_restored": True,
            },
            "home_suggestions_unavailable_no_polling": {
                "visible_state": suggestions["settled"],
                "rows": suggestions["rows"],
                "personal_off_note": suggestions["disabledNote"],
                "backend_reads_in_window": len(suggestion_reads),
                "refresh_posts_in_window": len(suggestion_refreshes),
            },
            "explicit_device_revoke": receipt["explicit_device_revoke"],
            "refresh_cookie_cleared_on_401": {
                "reuse": reused["cookieClear"],
                "grace_expired": outside["cookieClear"],
            },
        }
        receipt["complete"] = True
    finally:
        failures = []
        # Evidence failures must not skip owned child/server cleanup or expose
        # exception context containing the private fixture's credential input.
        try:
            evidence = Path(os.environ["OPENCODE_WORK_EVIDENCE_DIR"])
            (evidence / "b1-browser-qualification.json").write_text(
                json.dumps(receipt, indent=2) + "\n"
            )
        except Exception:
            failures.append("evidence-write")
        for child in reversed(children):
            try:
                await child.stop()
            except Exception:
                failures.append("child-cleanup")
        server.should_exit = True
        try:
            await asyncio.wait_for(serving, 15)
        except TimeoutError:
            server.force_exit = True
            await asyncio.wait_for(serving, 5)
            failures.append("uvicorn-grace")
        assert not failures, "B1 owned cleanup incomplete"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["identity", "entitlement"])
async def test_b1_setup_prerequisites_refuse_without_native_side_effects(b1_host, missing):
    host = b1_host
    async with host.db.connection() as owner:
        if missing == "identity":
            # This disposable prerequisite has no enrolled devices or accepted
            # work. Remove its migration-created personal tenant first, honoring
            # the owner FK rather than disabling constraints to delete a user.
            assert await owner.fetchval("SELECT count(*) FROM devices") == 0
            assert await owner.fetchval("SELECT count(*) FROM sessions") == 0
            assert await owner.fetchval("SELECT count(*) FROM tasks") == 0
            await owner.execute("DELETE FROM tenants WHERE owner_user_id=$1", SINGLETON)
            await owner.execute("DELETE FROM users WHERE id=$1", SINGLETON)
        else:
            await owner.execute("DELETE FROM entitlement_accounts WHERE user_id=$1", SINGLETON)
    token_file = Path(host.settings.daemon_setup_token_file)
    before = await snapshot(host)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(main.app), base_url=host.origin, trust_env=False
    ) as client:
        response = await client.post(
            "/v1/auth/setup",
            json={"setup_token": token_file.read_text().strip()},
            headers={"Origin": host.origin, "Sec-Fetch-Site": "same-origin"},
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "service_unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert await snapshot(host) == before
    assert token_file.exists()


@pytest.mark.asyncio
async def test_b1_setup_missing_live_verifier_does_not_resurrect_authority(b1_host):
    """An existing operator token file cannot replace absent shared authority."""
    host = b1_host
    token_file = Path(host.settings.daemon_setup_token_file)
    token = token_file.read_text().strip()
    async with host.db.connection() as owner:
        await owner.execute("DELETE FROM system_state WHERE key='auth.setup_token_hash'")
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM system_state WHERE key='auth.setup_token_hash'"
            )
            == 0
        )
    before = await snapshot(host)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(main.app), base_url=host.origin, trust_env=False
    ) as client:
        response = await client.post(
            "/v1/auth/setup",
            json={"setup_token": token},
            headers={"Origin": host.origin, "Sec-Fetch-Site": "same-origin"},
        )
    assert response.status_code == 409
    assert response.json() == {"detail": "setup_already_complete"}
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert await snapshot(host) == before
    assert token_file.exists()
    async with host.db.connection() as owner:
        assert (
            await owner.fetchval(
                "SELECT count(*) FROM system_state WHERE key='auth.setup_token_hash'"
            )
            == 0
        )
        assert await owner.fetchval("SELECT count(*) FROM devices") == 0
        assert await owner.fetchval("SELECT count(*) FROM sessions") == 0


@pytest.mark.asyncio
async def test_b1_setup_missing_operator_material_uses_real_verifier(b1_host):
    """The setup token file is delivery material, not the authority.

    Source behavior: only the live verifier in auth runtime state decides
    ``/v1/auth/setup``. Removing the operator file (material no longer
    available) neither authorizes nor refuses setup, and the real handler
    completes once with a valid token, then refuses a repeat attempt. Nothing
    is repaired, recreated or seeded from SQL.
    """
    host = b1_host
    token_file = Path(host.settings.daemon_setup_token_file)
    token = token_file.read_text().strip()
    token_file.unlink()
    assert not token_file.exists()
    before = await snapshot(host)
    assert before[0][0] == 0 and before[0][1] == 0
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(main.app), base_url=host.origin, trust_env=False
    ) as client:
        first = await client.post(
            "/v1/auth/setup",
            json={"setup_token": token},
            headers={"Origin": host.origin, "Sec-Fetch-Site": "same-origin"},
        )
        assert first.status_code == 200
        assert first.json().get("access_token")
        async with host.db.connection() as owner:
            # Real handler cleared the verifier and had no file to delete.
            assert (
                await owner.fetchval(
                    "SELECT count(*) FROM system_state WHERE key='auth.setup_token_hash'"
                )
                == 0
            )
            assert (
                await owner.fetchval("SELECT count(*) FROM devices WHERE revoked_at IS NULL") == 1
            )
        assert not token_file.exists()
        second = await client.post(
            "/v1/auth/setup",
            json={"setup_token": token},
            headers={"Origin": host.origin, "Sec-Fetch-Site": "same-origin"},
        )
        assert second.status_code == 409
        assert second.json() == {"detail": "setup_already_complete"}
    # Repeated setup after verifier consumption creates no replacement authority.
    assert not token_file.exists()
    assert await own_history_counts(host) == {
        "conversations": 0,
        "messages": 0,
        "tasks": 0,
    }
