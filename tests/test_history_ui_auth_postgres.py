"""Actual setup/enrollment/rotation on W; synthetic inputs, no auth overrides."""

from pathlib import Path
import os

from httpx import ASGITransport, AsyncClient
import pytest

from tests.history_ui_support import actual_auth_app
from tests.owned_search_redis import OwnedSearchRedis
from tests.test_history_text_execution_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    text_host as text_host,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("text_host", ["self-hosted-ui"], indirect=True)
async def test_real_setup_enroll_refresh_and_csrf_without_privilege_changes(text_host, monkeypatch):
    s = text_host
    owner = OwnedSearchRedis(
        Path(os.environ["OPENCODE_WORK_TEST_DIR"]), "daily-driver-app-flow-20261113"
    )
    redis = None
    try:
        owner.start()
        redis = await owner.connect()
        async with s.make():
            wired = await actual_auth_app(s, monkeypatch, redis, "http://fixture.test")
            transport = ASGITransport(wired.app)
            async with (
                AsyncClient(
                    transport=transport,
                    base_url="http://fixture.test",
                    headers={"Origin": "http://fixture.test"},
                ) as first,
                AsyncClient(
                    transport=transport,
                    base_url="http://fixture.test",
                    headers={"Origin": "http://fixture.test"},
                ) as second,
            ):
                setup = await first.post("/v1/auth/setup", json={"setup_token": wired.bootstrap})
                assert setup.status_code == 200, setup.text
                assert "daemon_refresh" in first.cookies
                a = {"Authorization": "Bearer " + setup.json()["access_token"]}
                start = await first.post("/v1/auth/enroll/start", headers=a)
                assert start.status_code == 200, start.text
                enrollment = start.json()
                complete = await second.post(
                    "/v1/auth/enroll/complete",
                    json={
                        "pending_id": enrollment["pending_id"],
                        "code": enrollment["code"],
                        "client_kind": "web",
                    },
                )
                assert complete.status_code == 200, complete.text
                assert "refresh_token" not in complete.json()
                assert first.cookies.get("daemon_refresh") != second.cookies.get("daemon_refresh")
                b = {"Authorization": "Bearer " + complete.json()["access_token"]}
                assert a != b
                for client, auth in ((first, a), (second, b)):
                    assert (
                        await client.get("/conversations?pagination=cursor", headers=auth)
                    ).status_code == 200
                    refreshed = await client.post("/v1/auth/refresh")
                    assert refreshed.status_code == 200 and "refresh_token" not in refreshed.json()
                    assert (
                        await client.post("/v1/auth/cache/clear", content=b"")
                    ).status_code == 204
                refused = await second.post(
                    "/v1/auth/refresh", headers={"Origin": "http://foreign.invalid"}
                )
                assert refused.status_code == 403
                assert s.db.sql("SELECT count(*) FROM devices") == "2"
                assert s.db.sql("SELECT count(*) FROM tenants") == "1"
                assert (
                    s.db.sql(
                        "SELECT count(*) FROM identity_audit_log WHERE event_type='device_created'"
                    )
                    == "1"
                )
    finally:
        if redis is not None:
            await redis.aclose()
        owner.close()
