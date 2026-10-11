"""Fictional, loopback-only HTTP-cache + auth-upstream fixture; no DB, Redis
or providers.

The cache-clear handler is the actual production auth router, not a fixture
implementation of Clear-Site-Data. The P0.2 identity/refresh/logout routes are
SYNTHETIC upstream models: the production client coordinator, the actual Next
auth proxy and the real Chromium cookie jar run for real; only these upstream
responses are simulated. Their contracts are frozen by
`.sisyphus/plans/p02-definitive-rejection-astra-decision.md`:

- the audited email rejection detail is emitted exactly and with NO Set-Cookie;
- successful identity/refresh outcomes rotate the fictional cookie normally;
- the simulated logout deletes the cookie exactly like the real endpoint does;
- nothing here invents additional allowlisted rejections or unwinds completed
  upstream effects: the receipt events are append-only.
"""

import asyncio
import json
import os
import time

os.environ["DAEMON_ALLOWED_ORIGINS"] = "http://127.0.0.1:3142"
os.environ["DAEMON_PUBLIC_ORIGIN"] = "http://127.0.0.1:3142"
os.environ["DAEMON_ALLOWED_HOSTS"] = "127.0.0.1"
# litellm must not implicitly load a developer .env here.
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.routing import Route

from orchestrator.main import (
    CORS_ALLOW_HEADERS,
    CORS_ALLOW_METHODS,
    CORS_EXPOSE_HEADERS,
    app as production_app,
)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3142"],
    allow_credentials=True,
    allow_methods=list(CORS_ALLOW_METHODS),
    allow_headers=list(CORS_ALLOW_HEADERS),
    expose_headers=list(CORS_EXPOSE_HEADERS),
)
state = {"upgraded": False, "hits": 0, "clears": 0, "fail": False, "mode": "ok"}


@app.middleware("http")
async def record_clear(request: Request, call_next):
    if request.url.path == "/daemon/v1/auth/cache/clear":
        state["clears"] += 1
        assert "authorization" not in request.headers
        if state["fail"]:
            return Response(status_code=503)
        mode = state["mode"]
        if mode == "redirect":
            return Response(status_code=307, headers={"Location": "/daemon/v1/auth/cache/clear"})
        if mode == "stall":
            # Longer than the production client's finite 10-second deadline,
            # shorter than test/teardown deadlines. No assertion timeout change.
            await asyncio.sleep(11)
        response = await call_next(request)
        if mode == "missing-no-store":
            del response.headers["Cache-Control"]
        return response
    return await call_next(request)


# ---------------------------------------------------------------------------
# P0.2 synthetic identity/refresh/logout upstream. Routes are registered
# BEFORE the production mount, so exactly these three auth paths are
# simulated; every other path (notably cache/clear) still runs actual
# production code. Test controls live only under /__fixture.
# ---------------------------------------------------------------------------

# Mounted-surface path on the outer app -> planner key.
UPSTREAM_ROUTES = {
    "/daemon/v1/auth/email/complete": "email-complete",
    "/daemon/v1/auth/refresh": "refresh",
    "/daemon/v1/auth/logout": "logout",
}
COMPLETION_PATH = UPSTREAM_ROUTES["/daemon/v1/auth/email/complete"]
REFRESH_PATH = UPSTREAM_ROUTES["/daemon/v1/auth/refresh"]
LOGOUT_PATH = UPSTREAM_ROUTES["/daemon/v1/auth/logout"]
ROUTE_BEHAVIORS = {
    COMPLETION_PATH: frozenset(
        {
            "success",
            "success-expiring",
            "reject",
            "reject-delayed",
            "reject-unknown-detail",
            "reject-status-5xx",
            "success-malformed-body",
            "hold-success",
            "drop",
        }
    ),
    REFRESH_PATH: frozenset({"refresh-success", "hold-refresh-success", "drop"}),
    LOGOUT_PATH: frozenset({"signout", "hold-signout", "drop"}),
}
_route_plans: dict[str, list[str]] = {}
_route_events: list[list] = []
_hold_states: list[tuple[asyncio.Event, str]] = []


def _plan_reset() -> None:
    _route_plans.clear()
    _route_events.clear()
    for held, _route in list(_hold_states):
        held.set()
    _hold_states.clear()


def _event(route: str, behavior: str, phase: str) -> None:
    _route_events.append([route, behavior, phase, round(time.monotonic() * 1000)])


def _pop_plan(route_key: str) -> str | None:
    queue = _route_plans.setdefault(route_key, [])
    if not queue:
        return None
    behavior = queue.pop(0)
    return behavior


def _owner_label(body_bytes: bytes) -> str:
    # The fictional harness marks the owner in the challenge code; the
    # fixture never inspects other fields, and token strings stay fictional.
    try:
        value = json.loads(body_bytes or b"{}")
        code = value.get("code") if isinstance(value, dict) else None
    except ValueError:
        code = None
    if code in {"fictional-owner-a", "fictional-owner-b"}:
        return code.rsplit("-", 1)[-1]
    raise AssertionError("fixture sign-in body lacks a fictional owner marker")


async def _send_json(send, status, payload: dict, cookie: str | None) -> None:
    headers = [
        (b"content-type", b"application/json"),
        (b"cache-control", b"no-store"),
    ]
    if cookie is not None:
        headers.append((b"set-cookie", cookie.encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": json.dumps(payload).encode()})


async def _run_behavior(
    send, behavior: str, route_key: str, body_bytes: bytes, cookie: str
) -> None:
    """Deliver one staged response for `behavior`; receipts are append-only."""

    async def emit(kind, status=None, headers=None, body=None, wait: float | None = None):
        _event(route_key, behavior, f"step-{kind}")
        if kind == "wait":
            assert wait is not None
            await asyncio.sleep(wait)
        elif kind == "start":
            await send({"type": "http.response.start", "status": status, "headers": headers})
        elif kind == "body":
            await send({"type": "http.response.body", "body": body})

    if behavior in {"success", "hold-success", "success-expiring"}:
        assert route_key == COMPLETION_PATH
        owner = _owner_label(body_bytes)
        expiry = 1800 if behavior in {"success", "hold-success"} else 31.5
        payload = {
            "access_token": f"fictional-owner-{owner}",
            "expires_at": time.time() + expiry,
        }
        await emit(
            "start",
            status=200,
            headers=[
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
                (
                    b"set-cookie",
                    (
                        f"daemon_refresh=fictional-refresh-{owner}; "
                        f"HttpOnly; SameSite=Strict; Path=/"
                    ).encode(),
                ),
            ],
        )
        await emit("body", body=json.dumps(payload).encode())
    elif behavior in {"refresh-success", "hold-refresh-success"}:
        owner = next(
            (
                owner
                for owner in ("a", "b")
                if f"daemon_refresh=fictional-refresh-{owner}" in cookie
            ),
            None,
        )
        assert owner is not None, "refresh must carry a fictional existing owner cookie"
        payload = {"access_token": f"fictional-rotated-{owner}", "expires_in": 1800}
        await emit(
            "start",
            status=200,
            headers=[
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
                (
                    b"set-cookie",
                    f"daemon_refresh=fictional-refresh-{owner}; HttpOnly; SameSite=Strict; Path=/".encode(),
                ),
            ],
        )
        await emit("body", body=json.dumps(payload).encode())
    elif behavior in {"signout", "hold-signout"}:
        # Simulated logout contract shape: 204 plus the cookie deleted.
        await emit(
            "start",
            status=204,
            headers=[
                (b"cache-control", b"no-store"),
                (b"set-cookie", b"daemon_refresh=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"),
            ],
        )
        await emit("body", body=b"")
    elif behavior == "reject":
        # Exact audited detail; NO Set-Cookie header at all.
        await emit(
            "start",
            status=401,
            headers=[
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
            ],
        )
        await emit("body", body=json.dumps({"detail": "code_invalid_or_expired"}).encode())
    elif behavior == "reject-delayed":
        # Delayed response AND delayed body: the coordinator must withhold
        # pending-epoch exposure across both stages of the same attempt.
        await emit("wait", wait=1.2)
        await emit(
            "start",
            status=401,
            headers=[
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
            ],
        )
        await emit("wait", wait=1.2)
        await emit("body", body=json.dumps({"detail": "code_invalid_or_expired"}).encode())
    elif behavior == "reject-unknown-detail":
        # Deliberately NOT an audited production detail; preservation must
        # never be granted from this response.
        await emit(
            "start",
            status=401,
            headers=[
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
            ],
        )
        await emit(
            "body",
            body=json.dumps({"detail": "fictional_detail_outside_production_contract"}).encode(),
        )
    elif behavior == "reject-status-5xx":
        await emit("start", status=503, headers=[(b"content-type", b"text/plain")])
        await emit("body", body=b"fictional upstream unavailable")
    elif behavior == "success-malformed-body":
        # Successful HTTP status with a real Set-Cookie but a corrupted
        # body: the client must refuse to adopt it and stay blocked.
        await emit(
            "start",
            status=200,
            headers=[
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
                (
                    b"set-cookie",
                    b"daemon_refresh=fictional-refresh-incomplete; HttpOnly; SameSite=Strict; Path=/",
                ),
            ],
        )
        await emit("wait", wait=0.8)
        await emit("body", body=b"not-json{")
    else:
        raise AssertionError(f"behavior staged outside the handler: {behavior}")


class SyntheticAuthUpstream:
    """Raw ASGI upstream for one fixed route, consuming the test plan FIFO.

    Each arriving attempt pops exactly one planned behavior. A 'hold-*'
    behavior parks the attempt after draining the request body and before
    any response byte is emitted, until POST /__fixture/release; the parked
    attempt then completes from the SAME behavior key. Unplanned arrivals
    fail loudly with 501 rather than passing silently.
    """

    async def __call__(self, scope, receive, send):
        route_key = UPSTREAM_ROUTES[scope["path"]]
        # Reserve synchronously BEFORE reading a body: concurrent upstream
        # arrivals must not peek at and later pop each other's FIFO behavior.
        behavior = _pop_plan(route_key)
        if behavior is None or behavior not in ROUTE_BEHAVIORS.get(route_key, frozenset()):
            _event(route_key, str(behavior), "unplanned-arrival")
            await send(
                {
                    "type": "http.response.start",
                    "status": 501,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {"type": "http.response.body", "body": b'{"error":"fixture upstream not planned"}'}
            )
            return
        if behavior == "drop":
            _event(route_key, behavior, "dropping-connection")
            # Malformed ASGI header -> h11 raises before any socket write ->
            # uvicorn force-closes the transport -> the proxy's Node fetch
            # throws and the actual production retry list runs.
            await send(
                {"type": "http.response.start", "status": 200, "headers": [(b"bad\nheader", b"x")]}
            )
            return
        body_bytes = b""
        while True:
            message = await receive()
            if message.get("type") not in {"http.request", "http.disconnect"}:
                break
            body_bytes += message.get("body", b"") or b""
            if not message.get("more_body"):
                break
        if behavior.startswith("hold"):
            _event(route_key, behavior, "held")
            # Park BEFORE any response byte; the test releases explicitly.
            held = asyncio.Event()
            _hold_states.append((held, route_key))
            try:
                await held.wait()
            finally:
                if (held, route_key) in _hold_states:
                    _hold_states.remove((held, route_key))
            _event(route_key, behavior, "released")
        _event(route_key, behavior, "attempt-started")
        cookie = dict(scope["headers"]).get(b"cookie", b"").decode()
        await _run_behavior(send, behavior, route_key, body_bytes, cookie)


for _path, _key in UPSTREAM_ROUTES.items():
    # Route accepts an ASGI callable object; FastAPI's convenience add_route
    # annotation only describes request -> response endpoints.
    app.router.routes.append(Route(_path, SyntheticAuthUpstream(), methods=["POST"], name=_key))


@app.get("/__fixture/status")
async def status():
    return JSONResponse(state, headers={"Cache-Control": "no-store"})


@app.post("/__fixture/reset")
async def reset():
    state.update(upgraded=False, hits=0, clears=0, fail=False, mode="ok")
    _plan_reset()
    return Response(status_code=204)


@app.get("/__fixture/upstream/status")
async def upstream_status():
    return JSONResponse(
        {
            "plans": dict(_route_plans),
            "events": _route_events,
            "outstandingHolds": sum(1 for held, _route in _hold_states if not held.is_set()),
        },
        headers={"Cache-Control": "no-store"},
    )


@app.post("/__fixture/upstream/plan")
async def upstream_plan(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        return Response(status_code=400)
    for route, behaviors in body.items():
        if route not in UPSTREAM_ROUTES.values() or not isinstance(behaviors, list):
            return Response(status_code=400)
        for behavior in behaviors:
            if not isinstance(behavior, str) or behavior not in ROUTE_BEHAVIORS.get(
                route, frozenset()
            ):
                return Response(status_code=400)
        _route_plans.setdefault(route, []).extend(behaviors)
    return Response(status_code=204)


@app.post("/__fixture/upstream/release")
async def upstream_release():
    for held, _route in list(_hold_states):
        held.set()
    _hold_states.clear()
    return Response(status_code=204)


@app.post("/__fixture/upgrade")
async def upgrade():
    state["upgraded"] = True
    return Response(status_code=204)


@app.post("/__fixture/fail/{value}")
async def fail(value: int):
    state["fail"] = bool(value)
    return Response(status_code=204)


@app.post("/__fixture/mode/{mode}")
async def set_mode(mode: str):
    if mode not in {"ok", "missing-no-store", "redirect", "stall"}:
        return Response(status_code=400)
    state["mode"] = mode
    return Response(status_code=204)


@app.get("/daemon/generated-files/{filename}")
async def artifact(filename: str):
    state["hits"] += 1
    if state["upgraded"]:
        return Response("FICTIONAL_NEW_OWNER_B", headers={"Cache-Control": "private, no-store"})
    return Response("FICTIONAL_OLD_OWNER_A", headers={"Cache-Control": "private, max-age=3600"})


# Mount the actual production app, including its CORS/security/auth middleware.
# Mounted application lifespan is deliberately NOT started: this endpoint must
# work without a DB, Redis, provider configuration or account/session state.
# The synthetic routes above win before the mounted catch-all route.
app.mount("/daemon", production_app)
