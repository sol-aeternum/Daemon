"""#731: the unselected by-key route uses the shared unbound-H predicate per request."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
import uuid

from fastapi import HTTPException, Response
import pytest

from orchestrator.routes import tasks as routes
from orchestrator.services.restore_history import runtime


def state() -> Any:
    return cast(Any, SimpleNamespace(db_pool=object(), history_observation=None))


def auth() -> Any:
    return cast(Any, SimpleNamespace(user_id=uuid.uuid4()))


@pytest.mark.asyncio
@pytest.mark.parametrize("compatible", [True, False])
async def test_by_key_native_answer_requires_compatible_history(monkeypatch, compatible):
    check = AsyncMock(return_value=compatible)
    store = SimpleNamespace(task_for_key=AsyncMock(return_value=None))
    monkeypatch.setattr(routes, "history_unselected_compatible", check)
    monkeypatch.setattr(routes, "task_store", Mock(return_value=store))
    app_state = state()
    with pytest.raises(HTTPException) as raised:
        await routes.get_task_by_key("stable", Response(), app_state=app_state, auth=auth())
    check.assert_awaited_once_with(app_state.db_pool)
    if compatible:
        assert raised.value.status_code == 404
        store.task_for_key.assert_awaited_once()
    else:
        assert raised.value.status_code == 503
        assert cast(Any, raised.value.detail)["code"] == "history_unavailable"
        store.task_for_key.assert_not_awaited()


@pytest.mark.asyncio
async def test_by_key_check_failure_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        routes, "history_unselected_compatible", AsyncMock(side_effect=OSError("catalog"))
    )
    with pytest.raises(HTTPException) as raised:
        await routes.get_task_by_key("stable", Response(), app_state=state(), auth=auth())
    assert raised.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, 1, "t", False])
async def test_only_exact_true_is_compatible(value):
    db = SimpleNamespace(fetchval=AsyncMock(return_value=value))
    assert await runtime.history_unselected_compatible(cast(Any, db)) is False


@pytest.mark.asyncio
async def test_predicate_binds_the_six_immutable_triggers_and_the_fence_body():
    db = SimpleNamespace(fetchval=AsyncMock(return_value=True))
    assert await runtime.history_unselected_compatible(cast(Any, db)) is True
    sql, triggers, fence = db.fetchval.await_args.args
    assert sql is runtime.UNSELECTED_HISTORY_SQL
    assert len(set(triggers)) == 6
    assert fence == runtime.FENCE_BODY_SHA256
    assert all(f"pg_relation_size(h.{t},'main')=0" in sql for t in "bap")


def test_pinned_fence_body_matches_migration_073_and_h_shape_check():
    import hashlib
    from pathlib import Path

    from orchestrator.services.restore_history import acceptance, native_fence

    sql = (Path(__file__).parents[1] / "migrations" / "073_history_native_fence.sql").read_text()
    body = sql.split("$$")[1]
    assert hashlib.sha256(body.encode()).hexdigest() == runtime.FENCE_BODY_SHA256
    assert f"pg_try_advisory_xact_lock({native_fence.FENCE_KEY[0]}, 1)" in body
    assert native_fence.FENCE_KEY == (1145588051, 1)
    assert f"='{runtime.FENCE_BODY_SHA256}'" in acceptance.CATALOG
