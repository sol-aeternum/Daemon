"""Cross-device revoke serialization on owned synthetic PostgreSQL only.

Real hosted email auth or self-hosted setup/enrollment supplies two legitimate
sessions. A test trigger pauses the first revoke after its actual COUNT; the
second must wait on the existing auth transaction lock, then refuse with409.
"""

import asyncio
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from orchestrator import main
from orchestrator.auth_runtime_state import AUTH_RUNTIME_LOCK_KEY
from tests.test_exact_worker_privileges_postgres import ordinary as ordinary, sign_in
from tests.test_inference_recovery_postgres import db as db, metadata_cluster as metadata_cluster
from tests.test_task_startup_postgres import services as services


async def current_device(ordinary, headers):
    response = await ordinary.client.get("/v1/auth/devices", headers=headers)
    assert response.status_code == 200
    return next(row["id"] for row in response.json()["devices"] if row["current"])


async def legitimate_devices(ordinary, tmp_path, mode):
    if mode == "hosted":
        email = uuid4().hex + "@example.test"
        uid, _, a = await sign_in(ordinary, email)
        same, _, b = await sign_in(ordinary, email)
        assert same == uid
    else:
        ordinary.settings.daemon_deployment_mode = "self_hosted"
        ordinary.settings.daemon_hosted_identity_enabled = False
        uid = UUID("00000000-0000-0000-0000-000000000001")
        async with ordinary.db.connection() as owner:
            # Test-only, explicit legacy provisioning; no production repair.
            await owner.execute(
                "INSERT INTO entitlement_accounts(user_id) VALUES($1) ON CONFLICT DO NOTHING", uid
            )
        token_file = Path(ordinary.settings.daemon_setup_token_file)
        assert token_file.is_relative_to(tmp_path)
        response = await ordinary.client.post(
            "/v1/auth/setup", json={"setup_token": token_file.read_text().strip()}
        )
        assert response.status_code == 200
        a = {"Authorization": "Bearer " + response.json()["access_token"]}
        ordinary.client.cookies.clear()
        start = await ordinary.client.post("/v1/auth/enroll/start", headers=a)
        assert start.status_code == 200
        proof = start.json()
        completed = await ordinary.client.post(
            "/v1/auth/enroll/complete",
            json={
                "pending_id": proof["pending_id"],
                "code": proof["code"],
                "client_kind": "native",
            },
        )
        assert completed.status_code == 200
        b = {"Authorization": "Bearer " + completed.json()["access_token"]}
    first, second = await current_device(ordinary, a), await current_device(ordinary, b)
    assert first != second
    for h in (a, b):
        assert (await ordinary.client.get("/users/me/settings", headers=h)).status_code == 200
    await main._check_first_boot_setup(ordinary.state)
    assert not Path(ordinary.settings.daemon_setup_token_file).exists()
    return uid, [(first, a), (second, b)]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["hosted", "self_hosted"])
@pytest.mark.parametrize("reverse", [False, True])
async def test_concurrent_self_revoke_serializes_and_preserves_surviving_access(
    ordinary, tmp_path, mode, reverse
):
    uid, actors = await legitimate_devices(ordinary, tmp_path, mode)
    if reverse:
        actors.reverse()
    first, survivor = actors
    ordinary.db.sql(
        "CREATE FUNCTION fixture_pause_revoke() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN PERFORM pg_advisory_xact_lock_shared(903,44); RETURN NEW; END $$;"
        "CREATE TRIGGER fixture_pause_revoke BEFORE UPDATE OF revoked_at ON devices "
        "FOR EACH ROW WHEN(OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL) "
        "EXECUTE FUNCTION fixture_pause_revoke()"
    )
    observed_serial_wait = False
    async with ordinary.db.connection() as observer:
        await observer.execute("SELECT pg_advisory_lock(903,44)")
        pending = [
            asyncio.create_task(
                ordinary.client.delete("/v1/auth/devices/" + first[0], headers=first[1])
            )
        ]
        try:
            async with asyncio.timeout(3):
                first_pid = None
                while first_pid is None:
                    first_pid = await observer.fetchval(
                        "SELECT pid FROM pg_locks WHERE locktype='advisory' AND classid=903 "
                        "AND objid=44 AND objsubid=2 AND NOT granted"
                    )
                    if first_pid is None:
                        await asyncio.sleep(0.005)
            pending.append(
                asyncio.create_task(
                    ordinary.client.delete("/v1/auth/devices/" + survivor[0], headers=survivor[1])
                )
            )
            async with asyncio.timeout(3):
                while True:
                    waiters = await observer.fetchval(
                        "SELECT count(*) FROM pg_locks WHERE locktype='advisory' "
                        "AND objsubid=1 AND NOT granted AND $1=ANY(pg_blocking_pids(pid)) "
                        "AND objid=(hashtext($2)::bigint & 4294967295)::oid "
                        "AND classid=(CASE WHEN hashtext($2)<0 THEN 4294967295 ELSE 0 END)::oid",
                        first_pid,
                        AUTH_RUNTIME_LOCK_KEY,
                    )
                    fixture_waiters = await observer.fetchval(
                        "SELECT count(*) FROM pg_locks WHERE locktype='advisory' "
                        "AND classid=903 AND objid=44 AND objsubid=2 AND NOT granted"
                    )
                    if waiters == 1:
                        observed_serial_wait = True
                        assert fixture_waiters == 1
                        break
                    if fixture_waiters == 2:
                        # Baseline defect: both COUNTs ran before either commit.
                        break
                    await asyncio.sleep(0.005)
        finally:
            await observer.execute("SELECT pg_advisory_unlock(903,44)")
            responses = await asyncio.gather(*pending)
    statuses = [response.status_code for response in responses]
    pool = ordinary.state.db_pool
    active = await pool.fetchval(
        "SELECT count(*) FROM devices WHERE user_id=$1 AND revoked_at IS NULL", uid
    )
    facts = {
        "mode": mode,
        "reverse": reverse,
        "serial_wait_observed": observed_serial_wait,
        "statuses": statuses,
        "active_devices": active,
        "different_devices": first[0] != survivor[0],
    }
    evidence = Path(os.environ["OPENCODE_WORK_EVIDENCE_DIR"])
    (evidence / ("revoke-" + mode + "-" + str(reverse) + ".json")).write_text(
        json.dumps(facts, indent=2) + "\n"
    )
    assert observed_serial_wait and statuses == [204, 409] and active == 1, facts
    assert responses[1].json()["detail"] == "cannot_revoke_last_active_device"
    assert "cache" in responses[0].headers["Clear-Site-Data"]
    assert await pool.fetchval(
        "SELECT id FROM devices WHERE user_id=$1 AND revoked_at IS NULL", uid
    ) == UUID(survivor[0])
    assert (await ordinary.client.get("/users/me/settings", headers=first[1])).status_code == 401
    assert (await ordinary.client.get("/users/me/settings", headers=survivor[1])).status_code == 200
    # The surviving legitimate session must not re-enter setup on the actual
    # startup check. Self-hosted setup remains completed; hosted stays disabled.
    await main._check_first_boot_setup(ordinary.state)
    assert not Path(ordinary.settings.daemon_setup_token_file).exists()
    setup = await ordinary.client.post(
        "/v1/auth/setup", json={"setup_token": "invalid-fixture-proof"}
    )
    assert setup.status_code == (403 if mode == "hosted" else 409)
