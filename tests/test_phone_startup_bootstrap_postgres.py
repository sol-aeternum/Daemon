"""Source-only phone-pilot bootstrap qualification on owned PostgreSQL.

Real ``orchestrator.main.app``/``main.lifespan``, real restricted A/W login
pools, real auth/task/conversation handlers. Settings sourcing, H single-UID
ownership/material and seeded native sessions are synthetic. The H1
observation-only refusal and worker execution barrier are
unchanged; this is not live privilege, phone, deployment or restore evidence.

The fixture database already carries the ordinary W map plus the separate
synthetic H ``task_history_*`` execute profile. Those H additions are not proof
that the pinned ordinary W map alone is sufficient.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import stat
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from orchestrator import main
from orchestrator.entitlements import attestation
from orchestrator.memory.store import MemoryStore
from orchestrator.routes import auth_config, auth_setup
from orchestrator.routes import tasks as task_routes
from orchestrator.services.identity import rate_limit_dep
from tests.restore_history_support import KEY
from tests.test_history_effects_postgres import (
    db as db,
    metadata_cluster as metadata_cluster,
    projection_db as projection_db,
    scenario as scenario,
    effects as effects,
)
from tests.test_history_observation_boundary_postgres import device
from tests.test_history_runtime_postgres import host as host

ROOT = Path(__file__).resolve().parents[1]

REFUSAL = {
    "detail": {
        "code": "history_unavailable",
        "task_id": None,
        "retry_safe": False,
    }
}

REFUSED = (
    ("POST", "/chat"),
    ("POST", "/tasks/{task_id}/cancel"),
    ("GET", "/tasks/{task_id}/inputs"),
    ("GET", "/tasks/{task_id}/presentations"),
    ("GET", "/users/me/entitlements"),
    ("GET", "/home-suggestions"),
    ("POST", "/home-suggestions/refresh"),
    ("GET", "/users/me/settings"),
    ("PATCH", "/users/me/settings"),
    ("GET", "/v1/catalog"),
    ("GET", "/v1/models"),
    ("GET", "/resources"),
)


async def aforbidden(*_args: Any, **_kwargs: Any) -> None:
    pytest.fail("H observation host started forbidden work")


@pytest.fixture
def bootstrap_settings(host, monkeypatch, tmp_path):
    """Synthetic settings sourcing only; startup validation stays real."""
    settings = host.settings
    settings.daemon_setup_token_file = str(tmp_path / "setup-token")
    # Route modules bound ``get_settings`` at import; point the exercised ones at
    # this owned development settings object instead of the process environment.
    for module in (main, auth_config, auth_setup, task_routes, rate_limit_dep):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    # The real lifespan assigns these attributes; restore any preceding test's
    # state rather than leave a closed H host selected on the shared app.
    monkeypatch.setattr(main.app.state, "app_state", None, raising=False)
    monkeypatch.setattr(main.app.state, "settings", settings, raising=False)
    # Fail-fast spies on the forbidden non-H work boundaries. The H branch must
    # not start attestation, background jobs, backfill or shipped-skill work.
    monkeypatch.setattr(attestation, "start", aforbidden)
    monkeypatch.setattr(attestation, "stop", aforbidden)
    monkeypatch.setattr(main, "_publish_shipped_skills", aforbidden)
    monkeypatch.setattr(main, "cleanup_stale_sessions", aforbidden)
    monkeypatch.setattr(main, "start_session_cleanup_task", aforbidden)
    monkeypatch.setattr(MemoryStore, "backfill_memory_content_hashes", aforbidden)
    return settings


async def assert_refused(
    client: httpx.AsyncClient, method: str, path: str, headers: dict[str, str]
):
    response = await client.request(method, path, headers=headers, json={})
    assert response.status_code == 503, (method, path, response.text)
    assert response.json() == REFUSAL, (method, path, response.text)
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_full_lifespan_bootstrap_matrix_real_principals_and_cleanup(
    host, bootstrap_settings, caplog
):
    # Seed one real active device/session before startup so first-boot setup has
    # nothing to publish; this is the authenticated bootstrap surface.
    _, headers = await device(host.s, host.s.account)
    task_id = str(host.s.accepted.task_id)
    with caplog.at_level(logging.WARNING, logger="orchestrator.main"):
        async with main.lifespan(main.app):
            state = main.app.state.app_state
            assert state.history_runtime is not None
            assert state.history_runtime.ready(state)
            assert state.history_observation is not None
            # H branch owns no Redis producer and starts no background job.
            assert state.redis is None

            async with state.db_pool.acquire() as worker:
                identities = await worker.fetchrow(
                    "SELECT current_user::text AS w, session_user::text AS s,"
                    " pg_has_role(current_user,'md_acceptor','MEMBER') AS a_member,"
                    " pg_has_role(current_user,'md_owner','MEMBER') AS owner_member,"
                    " has_table_privilege(current_user,'public.task_inference_recovery','SELECT') AS protected_select,"
                    " has_function_privilege(current_user,'public.task_history_page(uuid,bigint,text)','EXECUTE') AS history_page"
                )
            async with state.task_acceptor_pool.acquire() as acceptor:
                acceptor_identity = await acceptor.fetchval("SELECT current_user::text")
                acceptor_owner_member = await acceptor.fetchval(
                    "SELECT pg_has_role(current_user,'md_owner','MEMBER')"
                )
            assert identities["w"] == identities["s"] == "md_app"
            assert acceptor_identity == "md_acceptor"
            assert identities["a_member"] is False
            assert identities["owner_member"] is False
            assert acceptor_owner_member is False
            # Ordinary W has no direct protected-table read; the H fixture's
            # separate synthetic profile supplies the read-only history page
            # function used by this observation-only host.
            assert identities["protected_select"] is False
            assert identities["history_page"] is True

            transport = httpx.ASGITransport(main.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://fixture.test", trust_env=False
            ) as client:
                health = await client.get("/health")
                assert health.status_code == 200
                assert health.headers["cache-control"] == "no-store"

                config = await client.get("/v1/auth/config")
                assert config.status_code == 200
                assert config.headers["cache-control"] == "no-store"
                assert config.json()["mode"] == "self_hosted"

                # Anonymous refresh reaches the real handler (audited ingress)
                # and refuses for the missing credential, not as history 503.
                refresh = await client.post("/v1/auth/refresh", json={})
                assert refresh.status_code == 400
                assert "refresh token required" in refresh.text
                assert (await client.get("/conversations")).status_code == 401

                conversations = await client.get("/conversations", headers=headers)
                assert conversations.status_code == 200
                assert conversations.headers["cache-control"] == "no-store"

                task = await client.get(f"/tasks/{task_id}", headers=headers)
                assert task.status_code == 200
                assert task.json()["id"] == task_id
                assert task.headers["cache-control"] == "no-store"

                by_key = await client.get("/tasks/by-key/stable-client-key", headers=headers)
                assert by_key.status_code == 200
                assert by_key.json()["id"] == task_id
                assert by_key.headers["cache-control"] == "no-store"

                for method, template in REFUSED:
                    await assert_refused(client, method, template.format(task_id=task_id), headers)

            assert "First-boot setup check failed" not in caplog.text

    assert state.history_runtime is not None and state.history_runtime.history is not None
    assert state.history_runtime.history._closed
    assert state.history_observation is not None and state.history_observation._closed
    assert state.db_pool is not None and state.db_pool._closed
    assert state.task_acceptor_pool is not None and state.task_acceptor_pool._closed
    assert state._history_shutdown_task is not None
    assert state._history_shutdown_task.done()


@pytest.mark.asyncio
@pytest.mark.parametrize("onboarding", ["new_install", "expired_sessions"])
async def test_full_lifespan_auth_bootstrap_writes_under_w_without_task_execution(
    host, bootstrap_settings, caplog, onboarding
):
    """H observation-only is a task barrier, not an auth-write prohibition."""
    if onboarding == "expired_sessions":
        await device(host.s, host.s.account)
        async with host.s.db.connection() as owner:
            await owner.execute(
                "UPDATE sessions SET access_expires_at=now()-interval '1 day',"
                " refresh_expires_at=now()-interval '1 day'"
            )
    token_file = Path(bootstrap_settings.daemon_setup_token_file)
    assert not token_file.exists()
    with caplog.at_level(logging.WARNING, logger="orchestrator.main"):
        async with main.lifespan(main.app):
            state = main.app.state.app_state
            assert state.history_runtime.ready(state)
            assert state.redis is None
            async with state.db_pool.acquire() as worker:
                assert await worker.fetchval("SELECT current_user::text") == "md_app"
                # Inspect only verifier presence; never read/publish the token.
                assert await worker.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM system_state "
                    "WHERE key='auth.setup_token_hash' AND value IS NOT NULL)"
                )
                assert (
                    await worker.fetchval("SELECT count(*) FROM devices WHERE revoked_at IS NULL")
                    == 0
                )
                assert (
                    await worker.fetchval("SELECT count(*) FROM sessions WHERE revoked_at IS NULL")
                    == 0
                )
                if onboarding == "expired_sessions":
                    assert await worker.fetchval("SELECT count(*) FROM devices") == 1
                    assert await worker.fetchval("SELECT count(*) FROM sessions") == 1
                assert (
                    await worker.fetchval(
                        "SELECT status FROM tasks WHERE id=$1", host.s.accepted.task_id
                    )
                    == "queued"
                )
            assert token_file.is_file()
            assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
        assert "First-boot setup check failed" not in caplog.text
    assert state.db_pool._closed and state.task_acceptor_pool._closed
    assert state.history_runtime.history._closed and state.history_observation._closed


CHILD = r"""
import json
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

packet = json.loads(Path(os.environ["DAEMON_TEST_PACKET"]).read_text())

from orchestrator.config import Settings
import orchestrator.config as config

settings = Settings(_env_file=None, **packet["settings"])
config.get_settings = lambda: settings

from orchestrator.services.restore_history import configuration as history_configuration
from orchestrator.services.restore_history import runtime as history_runtime
from orchestrator.services.restore_history.model import Identity
from orchestrator.services.restore_history.store import Ownership

identity = Identity(UUID(packet["history_id"]), packet["key_check"])
key = bytes.fromhex(packet["key"])
history_configuration.HistoryConfiguration.ownership = property(
    lambda self: Ownership(os.geteuid(), frozenset({os.geteuid()}), os.getegid())
)
history_runtime.load_material = (
    lambda configuration, service, *, deadline: history_configuration.HistoryMaterial(
        identity, key if service == "http" else None
    )
)

violations = []
real_connect = socket.socket.connect
real_connect_ex = socket.socket.connect_ex


def local_connect(sock, address):
    if sock.family != socket.AF_UNIX:
        violations.append("network_connect")
        raise AssertionError("bootstrap attempted network egress")
    return real_connect(sock, address)


def local_connect_ex(sock, address):
    if sock.family != socket.AF_UNIX:
        violations.append("network_connect_ex")
        raise AssertionError("bootstrap attempted network egress")
    return real_connect_ex(sock, address)


socket.socket.connect = local_connect
socket.socket.connect_ex = local_connect_ex

import uvicorn
from orchestrator import main
from orchestrator.entitlements import attestation
from orchestrator.memory.store import MemoryStore
import orchestrator.runtime as service_runtime


async def forbidden(*args, **kwargs):
    violations.append("background_work")
    raise AssertionError("H observation host started forbidden work")


attestation.start = forbidden
attestation.stop = forbidden
main._publish_shipped_skills = forbidden
main.cleanup_stale_sessions = forbidden
main.start_session_cleanup_task = forbidden
MemoryStore.backfill_memory_content_hashes = forbidden
real_config = uvicorn.Config
real_lifespan = main.app.router.lifespan_context


@asynccontextmanager
async def observed_lifespan(app):
    # Observe cleanup without changing the real startup/shutdown sequence.
    async with real_lifespan(app):
        yield
    state = app.state.app_state
    assert not violations, "bootstrap crossed a forbidden work boundary"
    assert state.db_pool._closed and state.task_acceptor_pool._closed
    assert state.history_runtime.history._closed and state.history_observation._closed
    assert state._history_shutdown_task.done()
    Path(packet["shutdown_receipt"]).write_text(json.dumps({"cleanup_complete": True}))


def loopback_config(*args, **kwargs):
    # Test-only inherited-socket fd branch, not Uvicorn's host/port bind branch.
    # The parent retains this socket until the child has stopped: no rebind race.
    kwargs["host"] = "127.0.0.1"
    kwargs["port"] = packet["port"]
    kwargs["fd"] = packet["fd"]
    return real_config(*args, **kwargs)


uvicorn.Config = loopback_config
main.app.router.lifespan_context = observed_lifespan
service_runtime.main(["backend"])
"""


def settings_packet(settings, tmp_path) -> dict[str, Any]:
    packet_settings: dict[str, Any] = {
        "durable_chat_enabled": True,
        "redis_url": None,
        "daemon_environment": "development",
        "daemon_allowed_hosts": "127.0.0.1",
        "daemon_encryption_key": settings.daemon_encryption_key,
        "daemon_auth_pepper": settings.daemon_auth_pepper,
        "openrouter_api_key": "synthetic-no-network",
        "database_url": settings.database_url,
        "daemon_task_acceptor_database_url": settings.daemon_task_acceptor_database_url,
        "daemon_task_acceptor_role": settings.daemon_task_acceptor_role,
        "daemon_task_worker_role": settings.daemon_task_worker_role,
        "daemon_deletion_journal_dir": settings.daemon_deletion_journal_dir,
        "daemon_deletion_journal_id": settings.daemon_deletion_journal_id,
        "daemon_setup_token_file": str(tmp_path / "setup-token"),
    }
    for name in (
        "dir",
        "expected_file",
        "operator_uid",
        "backend_uid",
        "worker_uid",
        "group_gid",
        "key_gid",
        "key_file",
    ):
        packet_settings["daemon_restore_history_" + name] = getattr(
            settings, "daemon_restore_history_" + name
        )
    return packet_settings


@pytest.mark.asyncio
async def test_supported_launcher_process_real_app_loopback_and_graceful_stop(host, tmp_path):
    """Fresh subprocess ``runtime.main(['backend'])``; real uvicorn and lifespan.

    The parent event loop must stay responsive: the owned PostgreSQL bridge is
    in-process, so the child is driven with asyncio subprocess/HTTP only.
    """
    _, headers = await device(host.s, host.s.account)
    task_id = str(host.s.accepted.task_id)
    # The child must decrypt the accepted task with the fixture's actual key.
    host.settings.daemon_encryption_key = host.s.content_key
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    packet = {
        "port": port,
        "fd": listener.fileno(),
        "settings": settings_packet(host.settings, tmp_path),
        "history_id": str(host.s.identity.history_id),
        "key_check": host.s.identity.key_check,
        "key": KEY.hex(),
        "shutdown_receipt": str(tmp_path / "shutdown-receipt.json"),
    }
    packet_path = tmp_path / "child-packet.json"
    packet_path.write_text(json.dumps(packet))
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(ROOT),
        "PYTHONUNBUFFERED": "1",
        "LITELLM_MODE": "PRODUCTION",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "DAEMON_TEST_PACKET": str(packet_path),
        "TMPDIR": str(tmp_path),
        "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"),
    }
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            CHILD,
            cwd=str(ROOT),
            env=env,
            pass_fds=(listener.fileno(),),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        base_url = f"http://127.0.0.1:{port}"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 60.0
        async with httpx.AsyncClient(base_url=base_url, timeout=2.0, trust_env=False) as client:
            while True:
                if process.returncode is not None:
                    assert process.stderr is not None
                    stderr = (await process.stderr.read()).decode()
                    pytest.fail(
                        f"child launcher exited {process.returncode} before readiness:\n{stderr}"
                    )
                try:
                    ready = await client.get("/health")
                except httpx.HTTPError:
                    ready = None
                if ready is not None and ready.status_code == 200:
                    break
                assert loop.time() < deadline, "child launcher did not become ready"
                await asyncio.sleep(0.05)

            assert (await client.get("/v1/auth/config")).status_code == 200
            assert (await client.post("/v1/auth/refresh", json={})).status_code == 400
            conversations = await client.get("/conversations", headers=headers)
            assert conversations.status_code == 200
            task = await client.get(f"/tasks/{task_id}", headers=headers)
            assert task.status_code == 200 and task.json()["id"] == task_id
            by_key = await client.get("/tasks/by-key/stable-client-key", headers=headers)
            assert by_key.status_code == 200 and by_key.json()["id"] == task_id
            # One representative H refusal over real loopback HTTP.
            refused = await client.get("/users/me/entitlements", headers=headers)
            assert refused.status_code == 503
            assert refused.json() == REFUSAL
    finally:
        try:
            if process is not None and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=20)
                except TimeoutError:
                    process.kill()
                    await process.wait()
                    pytest.fail("child launcher did not stop within the bounded grace period")
        finally:
            listener.close()

    assert process is not None
    assert process.stderr is not None
    stderr = (await process.stderr.read()).decode()
    # Uvicorn re-raises the original SIGTERM *after* lifespan shutdown. Numeric
    # signal exit alone is not cleanup evidence; require the post-cleanup receipt.
    assert process.returncode == -signal.SIGTERM, stderr
    assert json.loads(Path(packet["shutdown_receipt"]).read_text()) == {"cleanup_complete": True}
