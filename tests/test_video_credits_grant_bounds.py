from __future__ import annotations

import uuid
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator import db as db_module
from orchestrator.routes import video_credits


def _build_app(settings, dal):
    app = FastAPI()
    app.include_router(video_credits.router)
    app.dependency_overrides[video_credits.get_settings] = lambda: settings
    app.dependency_overrides[db_module.get_app_state] = lambda: SimpleNamespace(
        video_credits_dal=dal
    )
    return app


def _settings(*, admin_key="secret-admin-key", max_grant=100, min_desc=5):
    return SimpleNamespace(
        daemon_admin_api_key=admin_key,
        daemon_max_grant_amount_per_request=max_grant,
        daemon_min_grant_description_length=min_desc,
        daemon_environment="development",
    )


def _fake_dal(success=True, message="ok", captured=None):
    class FakeDAL:
        async def credit_credits(self, user_id, amount, txn_type, description):
            if captured is not None:
                captured["description"] = description
            return SimpleNamespace(success=success, message=message, transaction_id=uuid.uuid4())

    return FakeDAL()


def _post(app, body, token="secret-admin-key"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return TestClient(app).post("/video-credits/grant", headers=headers, json=body)


def test_grant_rejects_zero_amount():
    app = _build_app(_settings(), _fake_dal(success=False))
    r = _post(app, {"user_id": str(uuid.uuid4()), "amount": 0, "description": "refund"})
    assert r.status_code == 422
    assert "positive integer" in r.json()["detail"].lower()


def test_grant_rejects_negative_amount():
    app = _build_app(_settings(), _fake_dal(success=False))
    r = _post(app, {"user_id": str(uuid.uuid4()), "amount": -5, "description": "refund"})
    assert r.status_code == 422


def test_grant_rejects_overbound_amount():
    app = _build_app(_settings(max_grant=100), _fake_dal(success=False))
    r = _post(app, {"user_id": str(uuid.uuid4()), "amount": 101, "description": "refund"})
    assert r.status_code == 422
    assert "100" in r.json()["detail"]


def test_grant_accepts_at_max_boundary():
    app = _build_app(_settings(max_grant=100), _fake_dal(success=True))
    r = _post(app, {"user_id": str(uuid.uuid4()), "amount": 100, "description": "refund"})
    assert r.status_code == 201, r.text


def test_grant_rejects_short_description():
    app = _build_app(_settings(min_desc=5), _fake_dal(success=False))
    r = _post(app, {"user_id": str(uuid.uuid4()), "amount": 10, "description": "ab"})
    assert r.status_code == 422
    assert "5" in r.json()["detail"]


def test_grant_trims_description():
    captured: dict = {}
    app = _build_app(_settings(), _fake_dal(success=True, captured=captured))
    r = _post(app, {"user_id": str(uuid.uuid4()), "amount": 10, "description": "   hello   "})
    assert r.status_code == 201
    assert captured["description"] == "hello"


def test_grant_rejects_without_admin_key():
    app = _build_app(_settings(admin_key=None), _fake_dal(success=False))
    r = _post(app, {"user_id": str(uuid.uuid4()), "amount": 10, "description": "refund"})
    assert r.status_code == 403


def test_grant_rejects_wrong_admin_key():
    app = _build_app(_settings(admin_key="secret-admin-key"), _fake_dal(success=False))
    r = _post(
        app,
        {"user_id": str(uuid.uuid4()), "amount": 10, "description": "refund"},
        token="wrong-key",
    )
    assert r.status_code == 403
