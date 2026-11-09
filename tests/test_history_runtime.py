"""Closed startup and ingress contracts; synthetic resources, no provisioning."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
import time
from typing import Any, cast

from fastapi import Depends
from fastapi.routing import iter_route_contexts
from httpx import ASGITransport, AsyncClient
import pytest

from orchestrator.config import Settings
from orchestrator.services.restore_history.configuration import (
    configured_history,
    history_selected,
    load_material,
)
from orchestrator.services.restore_history.model import HistoryError
from orchestrator.services.restore_history.runtime import HistoryRuntime, physical_completion
from orchestrator.services.restore_history.ingress import AUDITED


def settings(**updates):
    return cast(Any, Settings)(_env_file=None, **updates)


def selected(**updates):
    values = dict(
        daemon_restore_history_dir="/synthetic/history",
        daemon_restore_history_expected_file="/synthetic/operator/identity",
        daemon_restore_history_operator_uid="0",
        daemon_restore_history_backend_uid="1001",
        daemon_restore_history_worker_uid="1002",
        daemon_restore_history_group_gid="1003",
        daemon_restore_history_key_gid="1004",
        daemon_restore_history_key_file="/synthetic/keys/key",
    )
    return settings(**(values | updates))


def test_empty_selection_and_partial_selection_are_distinct():
    value = settings()
    assert not history_selected(value) and configured_history(value, "http") is None
    value.daemon_restore_history_dir = ""
    assert not history_selected(value)
    value.daemon_restore_history_dir = "/synthetic/history"
    assert history_selected(value)
    with pytest.raises(HistoryError):
        configured_history(value, "http")


@pytest.mark.parametrize(
    "field,value",
    [
        ("daemon_restore_history_backend_uid", "0"),
        ("daemon_restore_history_backend_uid", "01"),
        ("daemon_restore_history_backend_uid", "-1"),
        ("daemon_restore_history_backend_uid", "4294967295"),
        ("daemon_restore_history_worker_uid", "1001"),
        ("daemon_restore_history_key_gid", "1003"),
        ("daemon_restore_history_dir", "relative"),
        ("daemon_restore_history_dir", "/synthetic/../history"),
        ("daemon_restore_history_dir", "/synthetic//history"),
        ("daemon_restore_history_expected_file", "/synthetic/history/identity"),
        ("daemon_restore_history_key_file", "/synthetic/history/key"),
        ("daemon_restore_history_key_file", "/synthetic/operator/identity"),
    ],
)
def test_config_profile_refuses_ambiguous_authority_or_paths(field, value):
    with pytest.raises(HistoryError):
        configured_history(selected(**{field: value}), "http")


@pytest.mark.parametrize("service", ["http", "worker"])
@pytest.mark.parametrize("operator_uid", ["1001", "1002"])
def test_operator_service_uid_collision_refuses_before_io(monkeypatch, service, operator_uid):
    monkeypatch.setattr("os.open", lambda *a, **kw: pytest.fail("collision started file I/O"))
    value = selected(daemon_restore_history_operator_uid=operator_uid)
    if service == "worker":
        value.daemon_restore_history_key_file = None
    with pytest.raises(HistoryError, match="history_configuration_required"):
        configured_history(value, service)


@pytest.mark.parametrize("operator_uid", [0, 1005])
@pytest.mark.parametrize("service", ["http", "worker"])
def test_root_or_distinct_operator_is_supported(operator_uid, service):
    value = selected(daemon_restore_history_operator_uid=str(operator_uid))
    if service == "worker":
        value.daemon_restore_history_key_file = None
    config = configured_history(value, service)
    assert config is not None and config.operator_uid == operator_uid
    assert len({config.operator_uid, config.backend_uid, config.worker_uid}) == 3


@pytest.mark.parametrize("service", ["http", "worker"])
def test_direct_profile_cannot_reintroduce_operator_service_collision(monkeypatch, service):
    config = configured_history(selected(), "http")
    assert config is not None
    monkeypatch.setattr("os.open", lambda *a, **kw: pytest.fail("collision started file I/O"))
    with pytest.raises(HistoryError, match="history_configuration_required"):
        replace(config, operator_uid=config.backend_uid if service == "http" else config.worker_uid)


def test_worker_never_loads_backend_key_even_when_common_profile_is_invalid(monkeypatch):
    monkeypatch.setattr("os.open", lambda *a, **kw: pytest.fail("unexpected file access"))
    with pytest.raises(HistoryError, match="history_worker_key_forbidden"):
        configured_history(selected(), "worker")
    config = configured_history(selected(), "http")
    assert config is not None
    with pytest.raises(HistoryError, match="history_worker_key_forbidden"):
        load_material(config, "worker", deadline=time.monotonic() + 1)


@pytest.mark.parametrize(
    "service,uid,groups,success",
    [
        ("http", 1001, [1003, 1004], True),
        ("worker", 1002, [1003], True),
        ("worker", 1002, [1003, 1004], False),
        ("http", 1001, [1003], False),
        ("http", 1002, [1003, 1004], False),
        ("worker", 1002, [1004], False),
    ],
)
def test_service_identity_and_group_boundary(monkeypatch, service, uid, groups, success):
    config = configured_history(selected(), "http")
    assert config is not None
    monkeypatch.setattr("os.getuid", lambda: uid)
    monkeypatch.setattr("os.geteuid", lambda: uid)
    monkeypatch.setattr("os.getegid", lambda: groups[0])
    monkeypatch.setattr("os.getgroups", lambda: groups)
    if success:
        config.check_service(service)
    else:
        with pytest.raises(HistoryError):
            config.check_service(service)


@pytest.mark.asyncio
@pytest.mark.parametrize("expire", [False, True])
async def test_physical_initializer_drains_late_result_without_success(expire):
    entered, release = asyncio.Event(), asyncio.Event()

    async def physical():
        entered.set()
        await release.wait()
        return "late key material"

    child = asyncio.create_task(
        physical_completion(physical(), deadline=time.monotonic() + (0.03 if expire else 5))
    )
    await entered.wait()
    if expire:
        await asyncio.sleep(0.06)
    else:
        child.cancel("original")
        await asyncio.sleep(0)
        child.cancel("again")
    await asyncio.sleep(0)
    assert not child.done()
    release.set()
    with pytest.raises(TimeoutError if expire else asyncio.CancelledError) as caught:
        await child
    if not expire:
        assert caught.value.args == ("original",)


@pytest.mark.asyncio
async def test_actual_host_barrier_covers_every_unaudited_route_before_dependencies(monkeypatch):
    from orchestrator import main

    host = object.__new__(HistoryRuntime)
    monkeypatch.setattr(HistoryRuntime, "ready", lambda self, state: True)
    value = selected()
    state = SimpleNamespace(settings=value, history_runtime=host)
    monkeypatch.setattr(main.app.state, "app_state", state, raising=False)
    effects = []

    async def side_effect():
        effects.append("dependency")

    async def unclassified(dependency=Depends(side_effect)):
        effects.append("handler")
        return {"unexpected": True}

    route_count = len(main.app.routes)
    main.app.add_api_route("/synthetic-new-handler", unclassified, methods=["GET", "POST"])
    try:
        async with AsyncClient(transport=ASGITransport(main.app), base_url="http://test") as client:
            checked = 0
            for route in iter_route_contexts(main.app.routes):
                endpoint = getattr(route, "endpoint", None)
                for method in getattr(route, "methods", ()) or ():
                    signature = (
                        getattr(endpoint, "__module__", ""),
                        getattr(endpoint, "__name__", ""),
                        route.path,
                        method,
                    )
                    if signature in AUDITED:
                        continue
                    path = route.path
                    assert path is not None
                    for param in getattr(route, "param_convertors", {}):
                        path = path.replace(
                            "{" + param + "}", "00000000-0000-0000-0000-000000000001"
                        )
                        path = path.replace("{" + param + ":path}", "synthetic")
                    response = await client.request(method, path, json={})
                    assert response.status_code == 503, (method, path, response.text)
                    assert method == "HEAD" or response.json() == {
                        "detail": {
                            "code": "history_unavailable",
                            "task_id": None,
                            "retry_safe": False,
                        }
                    }
                    assert response.headers["cache-control"] == "no-store"
                    assert response.headers["x-content-type-options"] == "nosniff"
                    checked += 1
            assert checked == sum(
                (
                    getattr(route.endpoint, "__module__", ""),
                    getattr(route.endpoint, "__name__", ""),
                    route.path,
                    method,
                )
                not in AUDITED
                for route in iter_route_contexts(main.app.routes)
                for method in route.methods or ()
            )
            assert checked > 0
            assert effects == []
            # Native Stop, legacy chat, token-producing GET, compatibility and
            # unknown paths all refuse, including preflight to producer paths.
            for path in (
                "/chat",
                "/api/chat",
                "/audio/token",
                "/unknown",
                "/tasks/00000000-0000-0000-0000-000000000001/cancel",
            ):
                assert (await client.post(path)).status_code == 503
            assert (
                await client.options(
                    "/chat",
                    headers={
                        "Origin": "http://localhost:3000",
                        "Access-Control-Request-Method": "POST",
                    },
                )
            ).status_code == 503
    finally:
        del main.app.routes[route_count:]


@pytest.mark.asyncio
async def test_selected_unready_host_refuses_even_audited_route_and_legacy_is_unchanged(
    monkeypatch,
):
    from orchestrator import main

    state = SimpleNamespace(settings=selected(), history_runtime=None)
    monkeypatch.setattr(main.app.state, "app_state", state, raising=False)
    async with AsyncClient(transport=ASGITransport(main.app), base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 503
        state.settings = settings()
        assert (await client.get("/unknown-synthetic")).status_code == 404


@pytest.mark.asyncio
async def test_h_lifespan_has_auth_but_no_producer_jobs_and_closes_on_startup_failure(monkeypatch):
    from orchestrator import main

    async def schema_present(sql):
        assert "to_regclass('memory_mutations')" in sql
        return True

    state = SimpleNamespace(
        settings=selected(),
        history_runtime=object(),
        db_pool=SimpleNamespace(fetchval=schema_present),
    )
    monkeypatch.setattr(main.app.state, "app_state", state, raising=False)
    monkeypatch.setattr(main.app.state, "settings", state.settings, raising=False)
    events = []

    async def init(_):
        return state

    async def pepper(*a):
        events.append("auth")

    async def check(*a):
        events.append("auth-setup")

    async def close(*a):
        events.append("close")

    monkeypatch.setattr(main, "init_app_state", init)
    monkeypatch.setattr(main, "_validate_startup_config", lambda _: None)
    monkeypatch.setattr(main, "apply_resolved_database_url", lambda _: None)
    monkeypatch.setattr(main, "initialize_development_pepper", pepper)
    monkeypatch.setattr(main, "_check_first_boot_setup", check)
    monkeypatch.setattr(main, "close_app_state", close)
    monkeypatch.setattr(main, "get_settings", lambda: state.settings)
    async with main.lifespan(main.app):
        assert events == ["auth", "auth-setup"]
    assert events == ["auth", "auth-setup", "close"]

    async def fail(*a):
        raise RuntimeError("auth setup failed")

    monkeypatch.setattr(main, "_check_first_boot_setup", fail)
    with pytest.raises(RuntimeError):
        async with main.lifespan(main.app):
            pytest.fail("unready lifespan")
    assert events[-1] == "close"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_shutdown_retains_one_close_attempt_and_drains_before_pools(monkeypatch, failure):
    from unittest.mock import AsyncMock
    from orchestrator import db

    config = configured_history(selected(), "http")
    assert config is not None
    host = HistoryRuntime(config, selected())
    entered, proceed = asyncio.Event(), asyncio.Event()
    events = []

    async def close_reader(*, deadline):
        events.append("read-start")
        entered.set()
        await proceed.wait()
        events.append("read-end")
        if failure:
            raise OSError("synthetic uncertain close")

    async def close_history(*, deadline):
        events.append("history-close")

    cast(Any, host).reader = SimpleNamespace(aclose=close_reader, _begin_shutdown=lambda: None)
    cast(Any, host).history = SimpleNamespace(aclose=close_history)
    pool = AsyncMock()

    async def close_pool():
        events.append("pool-close")

    pool.close.side_effect = close_pool
    state = db.AppState(settings=selected(), history_runtime=host, db_pool=pool)
    task = asyncio.create_task(db.close_app_state(state))
    await entered.wait()
    task.cancel("original shutdown cancellation")
    await asyncio.sleep(0)
    task.cancel("repeat")
    await asyncio.sleep(0)
    assert not task.done() and events == ["read-start"]
    proceed.set()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert caught.value.args == ("original shutdown cancellation",)
    assert events == ["read-start", "read-end", "pool-close", "history-close"]
    if failure:
        with pytest.raises(OSError):
            await db.close_app_state(state)
    else:
        await db.close_app_state(state)
    assert events == ["read-start", "read-end", "pool-close", "history-close"]


@pytest.mark.asyncio
@pytest.mark.parametrize("extension", [False, True])
async def test_websocket_ingress_is_denied_before_acceptance_or_handler(monkeypatch, extension):
    from unittest.mock import AsyncMock
    from orchestrator import main
    from orchestrator.services.restore_history.ingress import HistoryObservationOnlyMiddleware

    host = object.__new__(HistoryRuntime)
    monkeypatch.setattr(HistoryRuntime, "ready", lambda *a: True)
    monkeypatch.setattr(
        main.app.state,
        "app_state",
        SimpleNamespace(settings=selected(), history_runtime=host),
        raising=False,
    )
    handler, receive = AsyncMock(), AsyncMock()
    messages = []

    async def send(message):
        messages.append(message)

    guard = HistoryObservationOnlyMiddleware(handler, application=main.app)
    await guard(
        {
            "type": "websocket",
            "path": "/synthetic",
            "headers": [],
            "extensions": {"websocket.http.response": {}} if extension else {},
        },
        receive,
        send,
    )
    handler.assert_not_awaited()
    receive.assert_not_awaited()
    if extension:
        assert messages[0]["type"] == "websocket.http.response.start"
        assert messages[0]["status"] == 503
        assert (b"cache-control", b"no-store") in messages[0]["headers"]
    else:
        assert messages == [{"type": "websocket.close", "code": 1008}]


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["application", "runtime", "reader"])
async def test_shutdown_scheduling_failure_closes_admission_and_unstarted_coroutine(
    monkeypatch, entry
):
    from orchestrator import db

    config = configured_history(selected(), "http")
    assert config is not None
    host = HistoryRuntime(config, selected())
    state = db.AppState(settings=host.settings, history_runtime=host)
    calls = []
    reader = SimpleNamespace(_closing=False)
    reader._begin_shutdown = lambda: setattr(reader, "_closing", True)

    async def close_reader(*, deadline):
        pytest.fail("unstarted reader cleanup executed")

    reader.aclose = close_reader
    cast(Any, host).reader = reader
    original = RuntimeError("synthetic shutdown scheduling failure")

    def fail(call):
        calls.append(call)
        raise original

    monkeypatch.setattr(asyncio, "create_task", fail)
    with pytest.raises(RuntimeError) as caught:
        if entry == "application":
            await db.close_app_state(state)
        elif entry == "runtime":
            await host.aclose(deadline=time.monotonic() + 5)
        else:
            await host.drain_reader(deadline=time.monotonic() + 5)
    assert caught.value is original
    assert reader._closing
    assert host._closing, "failed shutdown left observation admission open"
    assert state._history_shutdown_task is None
    assert len(calls) == 1 and calls[0].cr_frame is None


@pytest.mark.asyncio
async def test_shutdown_scheduling_exception_survives_interrupted_coroutine_cleanup(monkeypatch):
    from orchestrator import db

    config = configured_history(selected(), "http")
    assert config is not None
    host = HistoryRuntime(config, selected())
    state = db.AppState(settings=host.settings, history_runtime=host)
    original = RuntimeError("synthetic scheduling failure")
    calls = []
    factory = db._close_history_state

    class InterruptedClose:
        def __init__(self, value):
            self.value = value

        def close(self):
            self.value.close()
            raise asyncio.CancelledError("synthetic cleanup interruption")

    def wrapped(value):
        call = InterruptedClose(factory(value))
        calls.append(call)
        return call

    def fail(call):
        raise original

    monkeypatch.setattr(db, "_close_history_state", wrapped)
    monkeypatch.setattr(asyncio, "create_task", fail)
    with pytest.raises(RuntimeError) as caught:
        await db.close_app_state(state)
    assert caught.value is original and host._closing
    assert calls[0].value.cr_frame is None
    assert any("CancelledError" in note for note in original.__notes__)
