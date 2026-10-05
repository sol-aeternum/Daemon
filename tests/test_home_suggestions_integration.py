"""Real PostgreSQL and Redis checks on disposable, fictional-only Docker instances.

Skip when Docker or the existing local images are unavailable; never pull images,
connect to a configured app database, invoke inference or touch deployed services.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import uuid

import asyncpg
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from redis.asyncio import Redis

from orchestrator.home_suggestions.cache import SuggestionCache
from orchestrator.home_suggestions.contracts import SuggestionError, bound_context, fingerprint
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.DEVNULL).strip()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def isolated_services():
    if not shutil.which("docker"):
        pytest.skip("Docker unavailable for fictional integration tests")
    try:
        docker("info", "--format", "{{.ServerVersion}}")
        for image in ("redis:7-alpine", "pgvector/pgvector:pg16"):
            docker("image", "inspect", image)
    except subprocess.CalledProcessError:
        pytest.skip("Docker daemon or cached PostgreSQL/Redis images unavailable")
    names: list[str] = []
    try:
        for kind, image, port, extra in (
            ("redis", "redis:7-alpine", "6379", []),
            (
                "pg",
                "pgvector/pgvector:pg16",
                "5432",
                ["-e", "POSTGRES_PASSWORD=fictional-test-password"],
            ),
        ):
            name = f"daemon-home-test-{kind}-{uuid.uuid4().hex[:12]}"
            docker(
                "run",
                "--pull=never",
                "-d",
                "--name",
                name,
                "-p",
                f"127.0.0.1::{port}",
                *extra,
                image,
            )
            names.append(name)
        redis_port = int(docker("port", names[0], "6379/tcp").split(":")[-1])
        pg_port = int(docker("port", names[1], "5432/tcp").split(":")[-1])
        redis = Redis(host="127.0.0.1", port=redis_port)
        pool = None
        for _ in range(100):
            try:
                await redis.ping()
                pool = await asyncpg.create_pool(
                    host="127.0.0.1",
                    port=pg_port,
                    user="postgres",
                    password="fictional-test-password",
                    database="postgres",
                    min_size=1,
                    max_size=5,
                )
                break
            except (OSError, asyncpg.PostgresError):
                await asyncio.sleep(0.1)
        assert pool is not None, "Disposable PostgreSQL did not become ready"
        await pool.execute("""
            CREATE TABLE users (id uuid PRIMARY KEY, settings jsonb NOT NULL, updated_at timestamptz DEFAULT now());
            CREATE TABLE conversations (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid REFERENCES users(id), pipeline text NOT NULL, title text, metadata jsonb DEFAULT '{}', updated_at timestamptz DEFAULT now(), last_activity_at timestamptz DEFAULT now());
            CREATE TABLE messages (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), conversation_id uuid REFERENCES conversations(id) ON DELETE CASCADE, user_id uuid REFERENCES users(id), role text, content text, status text, metadata jsonb DEFAULT '{}', created_at timestamptz DEFAULT now());
        """)
        try:
            yield pool, redis
        finally:
            await pool.close()
            await redis.aclose()
    finally:
        for name in names:
            docker("rm", "-f", "-v", name)


@pytest.mark.asyncio(loop_scope="module")
async def test_real_lua_admission_claim_expiry_and_revocation(isolated_services):
    _, redis = isolated_services
    cache = SuggestionCache(redis, ContentEncryption(Fernet.generate_key().decode()), uuid.uuid4())
    assert await cache.sync(True, 1)
    results = await asyncio.gather(*[cache.admit(1, "source-fingerprint", True) for _ in range(5)])
    assert [status for status, _ in results].count("queued") == 1
    assert [status for status, _ in results].count("limited") == 1
    token = next(token for status, token in results if status == "queued")
    assert await cache.publish(
        1, token, {"version": 1, "private": "fictional encrypted content"}, "ready"
    )
    payload, raw = await cache.read()
    assert payload is not None and raw is not None
    assert payload["private"] == "fictional encrypted content"
    assert b"fictional encrypted content" not in raw
    claims = await asyncio.gather(*[cache.claim(1, raw, "candidate") for _ in range(5)])
    assert sum(claims) == 1
    assert await cache.sync(False, 2)
    assert not await cache.sync(True, 1)
    assert (await cache.admit(2, "changed-source", True))[0] == "disabled"
    assert not await cache.publish(1, token, {"version": 1}, "ready")
    assert (await cache.read())[0] is None
    # Admission survives opt-out, and uses Redis TIME rather than process clocks.
    assert await redis.zcard(cache.keys[4]) == 4
    seconds, micros = await redis.time()
    now_ms = int(seconds) * 1000 + int(micros) // 1000
    entries = await redis.zrange(cache.keys[4], 0, -1)
    await redis.zadd(cache.keys[4], {entry: now_ms - 3_600_100 for entry in entries})
    assert await cache.sync(True, 3)
    status, new_token = await cache.admit(3, "changed-source", True)
    assert status == "queued"
    await redis.pexpire(cache.keys[2], 1)
    await asyncio.sleep(0.01)
    assert not await cache.publish(3, new_token, {"version": 1}, "ready")
    status, newest = await cache.admit(3, "new-source", True)
    assert status == "queued"
    await cache.release(new_token)
    assert await cache.lease_valid(3, newest)


@pytest.mark.asyncio(loop_scope="module")
async def test_real_sql_owner_cloud_snapshot_binding_and_optout(isolated_services):
    pool, _ = isolated_services
    enc = ContentEncryption(Fernet.generate_key().decode())
    store = MemoryStore(pool, enc)
    owner, other = uuid.uuid4(), uuid.uuid4()
    settings = json.dumps({"preferences": {"home_suggestions_enabled": True}})
    await pool.executemany(
        "INSERT INTO users (id, settings) VALUES ($1, $2::jsonb)",
        [(owner, settings), (other, settings)],
    )
    source = uuid.uuid4()
    await pool.execute(
        "INSERT INTO conversations (id,user_id,pipeline,title) VALUES ($1,$2,'cloud','Fictional release')",
        source,
        owner,
    )
    await pool.execute(
        "INSERT INTO messages (conversation_id,user_id,role,content,status) VALUES ($1,$2,'user',$3,'complete')",
        source,
        owner,
        enc.encrypt("Fictional release notes"),
    )
    await pool.execute(
        "INSERT INTO messages (conversation_id,user_id,role,content,status) VALUES ($1,$2,'user',$3,'complete')",
        source,
        other,
        enc.encrypt("Foreign-owner data must not bind"),
    )
    enabled, epoch, sources = await store.home_suggestion_snapshot(owner)
    assert enabled and len(sources) == 1 and len(sources[0]["messages"]) == 1
    context = bound_context({"id": "a" * 32, "source_index": 0}, sources)
    destination = await store.bind_home_suggestion(
        owner,
        epoch=epoch,
        expected_fingerprint=fingerprint(sources),
        prompt="Prepare a release checklist.",
        context=context,
    )
    turn = await store.get_home_suggestion_turn(destination, owner)
    assert turn is not None
    assert turn["metadata"]["home_suggestion"] == context
    raw = await pool.fetchval(
        "SELECT metadata::text FROM messages WHERE conversation_id=$1", destination
    )
    assert "Fictional release notes" not in raw
    assert await store.get_home_suggestion_turn(destination, other) is None
    await store.merge_user_preferences(owner, {"home_suggestions_enabled": False})
    with pytest.raises(SuggestionError, match="disabled"):
        await store.bind_home_suggestion(
            owner,
            epoch=epoch,
            expected_fingerprint=fingerprint(sources),
            prompt="Prepare another checklist.",
            context=context,
        )
    assert await pool.fetchval("SELECT count(*) FROM conversations WHERE user_id=$1", owner) == 2


@pytest.mark.asyncio(loop_scope="module")
async def test_real_parent_and_message_locks_serialize_acceptance(isolated_services):
    pool, _ = isolated_services
    enc = ContentEncryption(Fernet.generate_key().decode())
    owner, source = uuid.uuid4(), uuid.uuid4()
    await pool.execute(
        'INSERT INTO users (id,settings) VALUES ($1,\'{"preferences":{"home_suggestions_enabled":true}}\')',
        owner,
    )
    await pool.execute(
        "INSERT INTO conversations (id,user_id,pipeline,title) VALUES ($1,$2,'cloud','Lock fixture')",
        source,
        owner,
    )
    message = await pool.fetchval(
        "INSERT INTO messages (conversation_id,user_id,role,content,status) VALUES ($1,$2,'user',$3,'complete') RETURNING id",
        source,
        owner,
        enc.encrypt("Fictional locking source"),
    )
    store = MemoryStore(pool, enc)
    async with pool.acquire() as conn, conn.transaction():
        await conn.fetchrow("SELECT settings FROM users WHERE id=$1 FOR UPDATE", owner)
        await store._home_snapshot_on_connection(conn, owner)
        async with pool.acquire() as writer:
            await writer.execute("SET lock_timeout='100ms'")
            with pytest.raises(asyncpg.LockNotAvailableError):
                await writer.execute(
                    "UPDATE messages SET content=$2 WHERE id=$1",
                    message,
                    enc.encrypt("Edited source"),
                )
            with pytest.raises(asyncpg.LockNotAvailableError):
                await writer.execute("DELETE FROM conversations WHERE id=$1", source)
    await pool.execute(
        "UPDATE messages SET content=$2 WHERE id=$1",
        message,
        enc.encrypt("Edited after acceptance boundary"),
    )
