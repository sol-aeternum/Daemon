"""Actual bearer sessions + owned PG/POSIX HTTP, never auth dependency overrides."""

import asyncio
import hashlib
import secrets
import json
import socket
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio

from orchestrator.resource_http import install_resource_routes
from tests import test_resource_catalog_postgres as catalog_tests
from tests.compute_metadata_support import metadata_cluster as metadata_cluster
from tests.test_resource_inputs_postgres import submission

lifecycle_db = catalog_tests.lifecycle_db
db = catalog_tests.db
reservation_scenario = catalog_tests.reservation_scenario
reservation_key = catalog_tests.reservation_key
resource_scenario = catalog_tests.resource_scenario
resource_key = catalog_tests.resource_key
input_scenario = catalog_tests.input_scenario
catalog_scenario = catalog_tests.catalog_scenario


@pytest_asyncio.fixture
async def http_scenario(catalog_scenario):
    db, catalog, presentations, tasks, expected, _, _ = catalog_scenario
    app = FastAPI()
    app.state.app_state = SimpleNamespace(db_pool=tasks._pool)
    install_resource_routes(app, catalog, presentations)
    tokens, sessions = [], []
    async with db.connection() as conn:
        other = await db.account(conn)
        for owner in (expected.account_id, expected.account_id, other):
            token = secrets.token_urlsafe(32)
            device = await conn.fetchval(
                "INSERT INTO devices(user_id,display_name) VALUES($1,'synthetic') RETURNING id",
                owner,
            )
            session = await conn.fetchval(
                "INSERT INTO sessions(user_id,device_id,client_kind,access_token_hash,access_expires_at,refresh_token_hash,refresh_expires_at) VALUES($1,$2,'web',$3,clock_timestamp()+interval '1 hour',$4,clock_timestamp()+interval '1 day') RETURNING id",
                owner,
                device,
                hashlib.sha256(token.encode()).hexdigest(),
                hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            )
            tokens.append({"Authorization": "Bearer " + token})
            sessions.append(session)
    task = await tasks.accept(**submission(expected))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://owned.test"
    ) as client:
        yield db, app, client, tokens, sessions, task, catalog_scenario


def private(response):
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_actual_two_sessions_content_metadata_ranges_head_foreign_denial(http_scenario):
    db, _, client, auth, _, task, _ = http_scenario
    page = await client.get("/resources", headers=auth[0])
    assert page.status_code == 200, page.text
    private(page)
    assert page.json() == (await client.get("/resources", headers=auth[1])).json()
    for item in page.json()["items"]:
        path = f"/resources/{item['resource_id']}/versions/{item['version_id']}"
        assert (await client.get(path, headers=auth[0])).json()["item"] == item
        for suffix in ("", "/preview", "/content"):
            for method in ("GET", "HEAD") if suffix == "/content" else ("GET",):
                denied = await client.request(
                    method,
                    path + suffix,
                    headers={**auth[2], "Range": "bytes=0-1", "If-None-Match": "*"},
                )
                assert denied.status_code == 404, denied.text
                private(denied)
                assert "etag" not in denied.headers and "content-range" not in denied.headers
        content = await client.get(path + "/content", headers=auth[0])
        if item["representation"] == "metadata_only":
            assert content.status_code == 409 and "etag" not in content.headers
            continue
        assert content.status_code == 200
        assert content.content == (await client.get(path + "/content", headers=auth[1])).content
        etag = content.headers["etag"]
        assert (
            await client.get(path + "/content", headers={**auth[0], "If-None-Match": etag})
        ).status_code == 304
        partial = await client.get(path + "/content", headers={**auth[0], "Range": "bytes=0-1"})
        assert partial.status_code == 206 and partial.content == content.content[:2]
        head = await client.head(path + "/content", headers={**auth[0], "Range": "bytes=0-1"})
        assert (
            head.status_code == 206 and not head.content and head.headers["content-length"] == "2"
        )
        assert (
            await client.get(path + "/content", headers={**auth[0], "Range": "bytes=999999-"})
        ).status_code == 416
        assert (
            await client.get(path + "/content", headers={**auth[0], "Range": "bytes=0-1,3-4"})
        ).status_code == 400
        assert (
            await client.get(
                path + "/content",
                headers={**auth[0], "Range": "bytes=0-1", "If-Range": "not-match"},
            )
        ).status_code == 200
    for suffix in ("inputs", "presentations"):
        assert (
            await client.get(f"/tasks/{task.task_id}/{suffix}", headers=auth[2])
        ).status_code == 404
    assert (
        len((await client.get(f"/tasks/{task.task_id}/inputs", headers=auth[0])).json()["items"])
        == 3
    )


@pytest.mark.asyncio
async def test_all_errors_private_and_revocation_real_db(http_scenario):
    db, _, client, auth, sessions, _, _ = http_scenario
    for method, path, headers, status in (
        ("GET", "/resources", {}, 401),
        ("POST", "/resources", auth[0], 405),
        ("GET", "/resources/no/versions/no", auth[0], 422),
        ("GET", "/resources/bad", auth[0], 404),
        ("GET", "/resources?limit=0", auth[0], 400),
        ("GET", "/resources?limit=1&limit=2", auth[0], 400),
    ):
        response = await client.request(method, path, headers=headers)
        assert response.status_code == status, response.text
        private(response)
    async with db.connection() as conn:
        await conn.execute(
            "UPDATE sessions SET revoked_at=clock_timestamp() WHERE id=$1", sessions[0]
        )
    response = await client.get("/resources", headers=auth[0])
    assert response.status_code == 401
    private(response)


@pytest.mark.asyncio
async def test_revocation_after_verification_before_headers_discards_payload(
    http_scenario, monkeypatch
):
    db, _, client, auth, sessions, _, scenario = http_scenario
    catalog = scenario[1]
    original = catalog.page

    async def revoked(*a, **kw):
        result = await original(*a, **kw)
        async with db.connection() as conn:
            await conn.execute(
                "UPDATE sessions SET revoked_at=clock_timestamp() WHERE id=$1", sessions[0]
            )
        return result

    monkeypatch.setattr(catalog, "page", revoked)
    result = await client.get("/resources", headers=auth[0])
    assert result.status_code == 401 and "items" not in result.json()
    private(result)


@pytest.mark.asyncio
async def test_real_send_backpressure_and_cancellation_releases_shared_slot(http_scenario):
    _, app, _, auth, _, _, scenario = http_scenario
    inputs = scenario[1].inputs
    entered, release = asyncio.Event(), asyncio.Event()

    async def send(message):
        if message["type"] == "http.response.body":
            entered.set()
            await release.wait()

    async def receive():
        await asyncio.Event().wait()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "scheme": "http",
        "method": "GET",
        "path": "/resources",
        "raw_path": b"/resources",
        "query_string": b"",
        "headers": [(b"authorization", auth[0]["Authorization"].encode())],
        "server": ("owned.test", 80),
        "client": ("127.0.0.1", 1234),
        "root_path": "",
    }
    producer = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert inputs._count == 1
        producer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await producer
    finally:
        release.set()
        if not producer.done():
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
    assert inputs._count == 0 and not inputs._active


@pytest.mark.asyncio
async def test_actual_browser_contexts_and_fresh_composition_restart(http_scenario):
    import uvicorn
    from cryptography.fernet import Fernet
    from orchestrator.resource_catalog import ResourceCatalog
    from orchestrator.resource_inputs import ResourceInputs
    from orchestrator.resource_presentations import ResourcePresentations
    from orchestrator.resource_store import ResourceStore
    from orchestrator.memory.encryption import ContentEncryption
    from orchestrator.memory.store import MemoryStore
    from orchestrator.tasks.store import TaskStore

    db, app, client, auth, _, task, scenario = http_scenario
    before = (await client.get("/resources", headers=auth[0])).json()
    # Only this fixture's exact loopback listener/server and child are owned here.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(32)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", access_log=False, lifespan="off")
    )
    producer = asyncio.create_task(server.serve(sockets=[listener]))
    child = None
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if producer.done():
                    await producer
                await asyncio.sleep(0.01)
        child = await asyncio.create_subprocess_exec(
            "node",
            str(Path(__file__).with_name("resource_browser_clients.mjs")),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        async with asyncio.timeout(60):
            stdout, stderr = await child.communicate(
                json.dumps(
                    {
                        "base": f"http://127.0.0.1:{port}",
                        "headers": auth,
                        "taskId": str(task.task_id),
                    }
                ).encode()
            )
        assert child.returncode == 0, stderr.decode()[:2000]
        report = json.loads(stdout)
        assert report["contexts"] == 3 and report["closed_A_replayed"] and report["foreign_denied"]
    finally:
        if child is not None and child.returncode is None:
            child.kill()
            await child.wait()
        server.should_exit = True
        await asyncio.wait_for(producer, 5)
        listener.close()
    # No re-provisioning or old Python readiness object after this reconstruction.
    _, old, _, _, expected, key, storage = scenario
    pool = old.inputs._tasks._pool
    store = ResourceStore(pool, storage[5], storage[9], storage[10], Fernet(key))
    memory = MemoryStore(pool, ContentEncryption(key.decode()))
    inputs = ResourceInputs(
        store, TaskStore(pool, memory.encryption, memory), key, old.inputs._limits
    )
    await inputs.bind()
    fresh = ResourceCatalog(inputs, scan_limit=100)
    presentations = ResourcePresentations(fresh, item_limit=20, record_limit=20)
    restarted = FastAPI()
    restarted.state.app_state = SimpleNamespace(db_pool=pool)
    install_resource_routes(restarted, fresh, presentations)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted), base_url="http://owned.test"
    ) as after:
        response = await after.get("/resources", headers=auth[1])
        assert response.status_code == 200 and response.json() == before
        for item in before["items"]:
            if item["representation"] != "metadata_only":
                path = f"/resources/{item['resource_id']}/versions/{item['version_id']}/content"
                assert (await after.get(path, headers=auth[1])).content == (
                    await client.get(path, headers=auth[1])
                ).content
