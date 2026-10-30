"""Approved read-time history identities on real native task/PG composition."""

from uuid import uuid4

import pytest
import pytest_asyncio

from orchestrator.routes.conversations import router
from tests import test_resource_http_postgres as h
from tests.compute_metadata_support import metadata_cluster as metadata_cluster
from tests.test_resource_inputs_postgres import submission

lifecycle_db = h.lifecycle_db
db = h.db
reservation_scenario = h.reservation_scenario
reservation_key = h.reservation_key
resource_scenario = h.resource_scenario
resource_key = h.resource_key
input_scenario = h.input_scenario
catalog_scenario = h.catalog_scenario
http_scenario = h.http_scenario


@pytest_asyncio.fixture
async def history_scenario(http_scenario):
    _, app, _, _, _, _, scenario = http_scenario
    tasks = scenario[3]
    app.state.app_state.memory_store = tasks._memory
    app.include_router(router)
    yield http_scenario


@pytest.mark.asyncio
async def test_owned_original_ids_on_all_pages_and_legacy_null(history_scenario):
    db, _, client, auth, _, task, scenario = history_scenario
    tasks, expected = scenario[3:5]
    ids = {str(task.task_id)}
    for i in range(22):
        db.sql(
            "UPDATE tasks SET status='completed',finished_at=clock_timestamp(),lease_owner=NULL,lease_expires_at=NULL"
        )
        accepted = await tasks.accept(
            **submission(expected, key=f"older-{i}", conversation=task.conversation_id)
        )
        ids.add(str(accepted.task_id))
    path = f"/conversations/{task.conversation_id}"
    ordinary = await client.get(path, headers=auth[0])
    assert ordinary.status_code == 200, ordinary.text
    messages = ordinary.json()["messages"]
    assert len(messages) == 46 and {m["task_id"] for m in messages} == ids
    assert {m["role"] for m in messages} == {"user", "assistant"}
    found = []
    cursor = None
    while True:
        page = await client.get(
            path,
            headers=auth[1],
            params={"pagination": "cursor", "limit": 7, **({"before": cursor} if cursor else {})},
        )
        assert page.status_code == 200, page.text
        data = page.json()
        found = data["messages"] + found
        cursor = data["message_page"]["next_cursor"]
        if not cursor:
            break
    assert {m["id"]: m["task_id"] for m in found} == {m["id"]: m["task_id"] for m in messages}
    assert len(found) == len({m["id"] for m in found}) == 46
    assert (await client.get(path, headers=auth[2])).status_code == 404
    # No rewriting plaintext/encrypted task metadata to attach a discovery field.
    legacy = await tasks._memory.insert_message(
        task.conversation_id, expected.account_id, "assistant", "legacy"
    )
    response = (await client.get(path, headers=auth[0])).json()
    assert next(m for m in response["messages"] if m["id"] == str(legacy["id"]))["task_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "foreign_owner",
        "wrong_role",
        "wrong_conversation",
        "stale_origin",
        "invalid_origin",
        "duplicate",
    ],
)
async def test_inconsistent_candidates_fail_closed(history_scenario, fault):
    db, _, client, auth, _, task, scenario = history_scenario
    tasks, expected = scenario[3:5]
    async with db.connection() as conn:
        if fault == "foreign_owner":
            other = await db.account(conn)
            await conn.execute("UPDATE tasks SET user_id=$2 WHERE id=$1", task.task_id, other)
        elif fault == "wrong_role":
            await conn.execute(
                "UPDATE messages SET role='system' WHERE id=$1", task.result_message_id
            )
        elif fault == "wrong_conversation":
            other = await tasks._memory.create_conversation(expected.account_id, title="other")
            await conn.execute(
                "UPDATE tasks SET conversation_id=$2 WHERE id=$1", task.task_id, other["id"]
            )
        elif fault in ("stale_origin", "invalid_origin"):
            row = await conn.fetchrow("SELECT * FROM tasks WHERE id=$1", task.task_id)
            value = tasks._open(row["input_ciphertext"])
            value["origin_account_generation"] = 1 if fault == "stale_origin" else True
            await conn.execute(
                "UPDATE tasks SET input_ciphertext=$2 WHERE id=$1", task.task_id, tasks._seal(value)
            )
        elif fault == "duplicate":
            # Different terminal task, same live message link: real DB uniqueness
            # does not protect these occurrence associations.
            await conn.execute(
                "UPDATE tasks SET status='completed',finished_at=clock_timestamp() WHERE id=$1",
                task.task_id,
            )
            other = await tasks.accept(
                **submission(expected, key="duplicate", conversation=task.conversation_id)
            )
            await conn.execute(
                "UPDATE tasks SET user_message_id=$2 WHERE id=$1",
                other.task_id,
                task.user_message_id,
            )
    response = await client.get(f"/conversations/{task.conversation_id}", headers=auth[0])
    assert response.status_code == 503 and "messages" not in response.json(), response.text


@pytest.mark.asyncio
async def test_uncomposed_legacy_and_source_deleted_do_not_invent_ids(history_scenario):
    db, _, client, auth, _, task, scenario = history_scenario
    tasks, expected = scenario[3:5]
    async with db.connection() as conn:
        row = await conn.fetchrow("SELECT * FROM tasks WHERE id=$1", task.task_id)
        value = tasks._open(row["input_ciphertext"])
        del value["origin_account_id"]
        del value["origin_account_generation"]
        await conn.execute(
            "UPDATE tasks SET input_ciphertext=$2 WHERE id=$1", task.task_id, tasks._seal(value)
        )
    # Missing legacy origin has no qualified identity, without losing saved prose.
    response = await client.get(f"/conversations/{task.conversation_id}", headers=auth[0])
    assert response.status_code == 200 and all(
        m["task_id"] is None for m in response.json()["messages"]
    )
    tasks._memory._resource_inputs = None
    tasks._resource_inputs = None
    response = await client.get(f"/conversations/{task.conversation_id}", headers=auth[0])
    assert response.status_code == 200 and all(
        m["task_id"] is None for m in response.json()["messages"]
    )


@pytest.mark.asyncio
async def test_history_pin_does_not_follow_newer_resource_version(history_scenario):
    from orchestrator.artifact_reservations import JobBinding
    from tests.test_resource_store_postgres import CONTENT

    db, _, client, auth, _, _, scenario = history_scenario
    _, catalog, presentations, tasks, expected, _, storage = scenario
    output, identity, command = storage[1:4]
    first = await output.publish_output(identity, command.binding.intent_id, CONTENT)
    await presentations.record(
        identity, [{"resource_id": str(first.resource_id), "version_id": str(first.version_id)}]
    )
    job = JobBinding(uuid4(), identity.account_id, identity.generation, identity.task_id, uuid4())
    async with db.connection("md_app") as app, app.transaction():
        await storage[8].register_output_job(app, identity, job)
    intent = await output.reserve_version(
        identity, job, first.resource_id, 0, b"column\nsecond\n", "second.csv"
    )
    second = await output.publish_output(identity, intent, b"column\nsecond\n")
    assert first.version_id != second.version_id
    async with db.connection() as conn:
        conversation = await conn.fetchval(
            "SELECT conversation_id FROM tasks WHERE id=$1", identity.task_id
        )
    for _ in range(2):
        saved = (
            await client.get(f"/conversations/{conversation}?pagination=cursor", headers=auth[1])
        ).json()
        original = next(m for m in saved["messages"] if m["role"] == "assistant")["task_id"]
        envelope = (await client.get(f"/tasks/{original}/presentations", headers=auth[1])).json()[
            "items"
        ][0]
        assert envelope["items"] == [
            {"resource_id": str(first.resource_id), "version_id": str(first.version_id)}
        ]
    db.sql(
        "UPDATE tasks SET status='completed',finished_at=clock_timestamp(),lease_owner=NULL,lease_expires_at=NULL"
    )
    assert await tasks.delete_conversation(expected.account_id, conversation) == (True, None)
    assert (await client.get(f"/conversations/{conversation}", headers=auth[1])).status_code == 404
    assert (await output.read(expected, first.resource_id, first.version_id)).content == CONTENT


@pytest.mark.asyncio
async def test_native_components_real_browser_sessions_proxy_and_history(
    history_scenario, monkeypatch
):
    import asyncio
    import json
    import socket
    from pathlib import Path
    from types import SimpleNamespace
    from urllib.parse import urlsplit
    import asyncpg
    import uvicorn
    from orchestrator import attempt_lifecycle as lc
    from orchestrator.artifact_reservations import JobBinding
    from orchestrator.resource_csv import ResourceCSV
    from orchestrator.routes import auth_setup

    db, app, _, auth, _, task, scenario = history_scenario
    _, catalog, presentations, tasks, expected, *_ = scenario
    # Original native accepted input task is leased and registered through actual
    # methods, never a fake authority callback. Zero inference.
    scope, metadata = uuid4(), uuid4()
    claim = await tasks.claim(task.task_id, worker_id="c1-native-ui", lease_s=180)
    assert claim is not None
    await tasks.record_compute_scope(task.task_id, claim.epoch, scope, binding=claim)
    async with db.connection() as conn:
        await conn.execute(
            "SELECT public.compute_metadata_create_scope($1,$2)", expected.account_id, metadata
        )
    identity = lc.AttemptIdentity(
        uuid4(),
        expected.account_id,
        expected.generation,
        task.task_id,
        claim.epoch,
        scope,
        metadata,
        uuid4(),
    )
    restricted = await asyncpg.create_pool(
        host=str(db.host),
        user="md_app",
        password="",
        database=db.name,
        ssl=False,
        min_size=0,
        max_size=2,
    )
    try:
        await lc.AttemptLifecycleStore(restricted).register(identity, uuid4())
        adapter = ResourceCSV(restricted, presentations, byte_limit=1000)
        job = JobBinding(
            uuid4(), identity.account_id, identity.generation, identity.task_id, uuid4()
        )
        first = await adapter.publish(
            identity, job, 0, [["column"], ["original native output"]], "native.csv"
        )
        newer = JobBinding(
            uuid4(), identity.account_id, identity.generation, identity.task_id, uuid4()
        )
        async with restricted.acquire() as conn, conn.transaction():
            await adapter.r0.register_output_job(conn, identity, newer)
        intent = await catalog.store.reserve_version(
            identity, newer, first.resource_id, 0, b"column\nnewer\n", "native.csv"
        )
        second = await catalog.store.publish_output(identity, intent, b"column\nnewer\n")
    finally:
        await restricted.close()
    app.add_api_route(
        "/v1/auth/cache/clear",
        auth_setup.clear_browser_cache_endpoint,
        methods=["POST"],
        status_code=204,
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(32)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", access_log=False, lifespan="off", ws="none")
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
            str(Path(__file__).with_name("resource_native_browser.mjs")),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert child.stdin is not None and child.stdout is not None
        child.stdin.write(
            (
                json.dumps(
                    {
                        "base": f"http://127.0.0.1:{port}",
                        "headers": auth,
                        "conversation": str(task.conversation_id),
                        "pin1": str(first.version_id),
                        "pin2": str(second.version_id),
                    }
                )
                + "\n"
            ).encode()
        )
        await child.stdin.drain()
        async with asyncio.timeout(30):
            startup = json.loads(await child.stdout.readline())
        ui = urlsplit(startup["ui"])
        assert ui.scheme == "http" and ui.hostname == "127.0.0.1" and ui.port and ui.path == ""
        # Only actual private-pipe-proven fixture origin is admitted for the real
        # production CSRF/cache handler; no wildcard/runtime environment change.
        monkeypatch.setattr(
            auth_setup,
            "get_settings",
            lambda: SimpleNamespace(
                daemon_allowed_origins=startup["ui"], daemon_public_origin=startup["ui"]
            ),
        )
        child.stdin.write(b"armed\n")
        await child.stdin.drain()
        async with asyncio.timeout(80):
            stdout, stderr = await child.communicate()
        assert child.returncode == 0, stderr.decode()[:4000]
        report = json.loads(stdout)
        assert (
            report["native_components"]
            and report["production_auth_cache"]
            and report["actual_resource_proxy"]
            and report["pinned_history_reloaded"]
            and report["account_switch_cleared"]
        )
        print(json.dumps(report, sort_keys=True))
    finally:
        if child is not None and child.returncode is None:
            child.kill()
            await child.wait()
        server.should_exit = True
        await asyncio.wait_for(producer, 5)
        listener.close()
