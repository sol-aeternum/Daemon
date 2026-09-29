"""Metered direct web search: wire shape, bounds, refusals and accounting.

Every test here runs against a mocked HTTP transport and a mocked entitlement
ledger. No test dispatches a live, paid provider call.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final, cast

import httpx
import pytest

from orchestrator import compute_runtime as runtime
from orchestrator.entitlements import EntitlementService
from orchestrator.entitlements.errors import (
    AccountSuspended,
    BudgetExceeded,
    ConcurrencyExceeded,
    RateLimitExceeded,
    TrialExhausted,
    UnknownOperation,
)
from orchestrator.entitlements.policy import InferencePolicy, parse_inference_policy
from orchestrator.tools import web_search
from orchestrator.tools.web_search import WebSearchTool

ROOT = Path(__file__).resolve().parents[1]
_UNSET: Final[object] = object()

BRAVE_KEY = "brave-subscription-token"
TAVILY_KEY = "tvly-test-key"
QUERY = "galaxy s26 ultra battery life"

#: Ceilings deliberately above each pinned price, so a test can tell a confirmed
#: settlement (the pinned price) from a conservative one (the whole hold).
BRAVE_CEILING = 7_000
TAVILY_CEILING = 10_000

REVIEW: dict[str, Any] = {
    "reviewer": "test-operator",
    "reviewed_at": "2026-09-29T00:00:00Z",
    "review_expires_at": "2030-01-01T00:00:00Z",
    "evidence": ["tests/test_metered_web_search.py"],
}

BRAVE_PAYLOAD: dict[str, Any] = {
    "type": "search",
    "query": {"original": QUERY, "more_results_available": True},
    "mixed": {"type": "mixed", "main": [{"type": "web", "index": 0, "all": False}]},
    "web": {
        "type": "search",
        "results": [
            {
                "title": "First result",
                "url": "https://example.com/first",
                "description": "First snippet",
                "page_age": "2026-09-01",
                "language": "en",
                "family_friendly": True,
                "type": "search_result",
                "subtype": "generic",
                "is_source_local": False,
                "is_source_both": False,
                "is_live": True,
                "meta_url": {"scheme": "https", "hostname": "example.com", "pathname": "/first"},
                "profile": {"name": "Example", "url": "https://example.com/"},
            },
            {
                "title": "Second result",
                "url": "https://example.com/second",
                "description": "s" * 900,
            },
        ],
    },
    "news": {"type": "news", "results": [{"title": "Ignored", "url": "https://news.test/ignored"}]},
    "videos": {"type": "videos", "results": [{"title": "Ignored", "url": "https://v.test/one"}]},
}

TAVILY_PAYLOAD: dict[str, Any] = {
    "query": QUERY,
    "answer": "",
    "images": [],
    "response_time": 1.23,
    "request_id": "123e4567-e89b-12d3-a456-426614174111",
    "results": [
        {
            "title": "First result",
            "url": "https://example.com/first",
            "content": "First snippet",
            "score": 0.81,
            "raw_content": None,
            "id": "a3f9c2-04",
        },
        {"title": "Second result", "url": "https://example.com/second", "content": "s" * 900},
    ],
    "usage": {"credits": 1},
}

#: The shape both providers must normalize to, snippet bound included.
NORMALIZED: list[dict[str, str]] = [
    {"title": "First result", "url": "https://example.com/first", "description": "First snippet"},
    {"title": "Second result", "url": "https://example.com/second", "description": "s" * 300},
]


# --------------------------------------------------------------------------- #
# policy fixtures
# --------------------------------------------------------------------------- #
def _service_entry(
    service_id: str,
    provider: str,
    *,
    approved: bool = True,
    availability: str = "verified",
    ceiling: int | None = BRAVE_CEILING,
    review: Any = _UNSET,
    review_mode: str | None = None,
    service: str = "web_search",
    unit: str = "call",
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "service_id": service_id,
        "service": service,
        "provider": provider,
        "unit": unit,
        "approved": approved,
        "availability": availability,
        "ceiling_microusd_per_unit": ceiling,
        "operator_review": REVIEW if review is _UNSET else review,
    }
    if review_mode is not None:
        entry["review_mode"] = review_mode
    return entry


def _document(*services: dict[str, Any]) -> dict[str, Any]:
    return {"version": 1, "routes": [], "tool_services": list(services)}


def _approved_document(**brave_overrides: Any) -> dict[str, Any]:
    """Both search services approved, with Brave's entry overridable."""
    brave: dict[str, Any] = {"ceiling": BRAVE_CEILING, **brave_overrides}
    return _document(
        _service_entry("brave-web-search", "brave", **brave),
        _service_entry("tavily-web-search", "tavily", ceiling=TAVILY_CEILING),
    )


def _install(monkeypatch: pytest.MonkeyPatch, *policies: InferencePolicy) -> Callable[[], int]:
    """Serve each policy once, in order; the last one then persists.

    Two policies model an approval that changes while a call is in flight: the
    tool's own gate sees the first, the pre-dispatch re-check sees the second.
    """
    queue = list(policies)
    loads = {"count": 0}

    def loader() -> InferencePolicy:
        loads["count"] += 1
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(runtime, "load_inference_policy", loader)
    return lambda: loads["count"]


def _approved_policy(monkeypatch: pytest.MonkeyPatch, **brave_overrides: Any) -> Callable[[], int]:
    return _install(monkeypatch, parse_inference_policy(_approved_document(**brave_overrides)))


# --------------------------------------------------------------------------- #
# ledger and scope fixtures
# --------------------------------------------------------------------------- #
class _Ledger:
    """A fake entitlement service: records accounting, touches no database."""

    def __init__(
        self,
        *,
        capabilities: tuple[str, ...] = ("chat", "web_research"),
        reserve_error: Exception | None = None,
        settle_error: Exception | None = None,
        release_error: Exception | None = None,
        max_open: int | None = None,
    ) -> None:
        self.capabilities = set(capabilities)
        self.reserve_error = reserve_error
        self.settle_error = settle_error
        self.release_error = release_error
        self.max_open = max_open
        self.attempts: list[dict[str, Any]] = []
        self.reserves: list[dict[str, Any]] = []
        self.reservations: list[Any] = []
        self.settled: list[tuple[Any, int, dict[str, Any] | None]] = []
        self.released: list[Any] = []
        self.open: list[Any] = []

    async def reconcile_expired_reservations(self, user_id: Any, *, before: Any) -> int:
        return 0

    async def resolve(self, user_id: Any) -> Any:
        return SimpleNamespace(capabilities=set(self.capabilities))

    async def reserve(self, user_id: Any, amount: int, **kwargs: Any) -> Any:
        self.attempts.append({"user_id": user_id, "amount": amount, **kwargs})
        if self.reserve_error is not None:
            raise self.reserve_error
        if self.max_open is not None and len(self.open) >= self.max_open:
            raise ConcurrencyExceeded(open_reservations=len(self.open), ceiling=self.max_open)
        reservation = SimpleNamespace(id=uuid.uuid4(), reserved_microusd=amount)
        self.reserves.append({"user_id": user_id, "amount": amount, **kwargs})
        self.reservations.append(reservation)
        self.open.append(reservation)
        return reservation

    async def settle(
        self, reservation: Any, amount: int, *, usage: dict[str, Any] | None = None
    ) -> Any:
        if self.settle_error is not None:
            raise self.settle_error
        self.settled.append((reservation, amount, usage))
        self.open = [item for item in self.open if item is not reservation]
        return SimpleNamespace(applied=True)

    async def release(self, reservation: Any, *, usage: dict[str, Any] | None = None) -> Any:
        if self.release_error is not None:
            raise self.release_error
        self.released.append(reservation)
        self.open = [item for item in self.open if item is not reservation]
        return SimpleNamespace(applied=True)

    @property
    def amounts(self) -> list[int]:
        return [entry[1] for entry in self.settled]

    @property
    def usages(self) -> list[dict[str, Any] | None]:
        return [entry[2] for entry in self.settled]


@asynccontextmanager
async def _scope(
    ledger: _Ledger,
    *,
    operation: str = "chat",
    extended: bool = False,
    background: bool = False,
    expected_period: str | None = None,
    user_id: uuid.UUID | None = None,
) -> AsyncIterator[runtime.ComputeScope]:
    """One real compute scope over the fake ledger, as ``account_compute`` builds."""
    scope = runtime.ComputeScope(
        user_id or uuid.uuid4(),
        cast(EntitlementService, ledger),
        operation=operation,
        extended=extended,
        background=background,
    )
    scope.expected_period = expected_period
    token = runtime._scope.set(scope)
    try:
        yield scope
    finally:
        runtime._scope.reset(token)


# --------------------------------------------------------------------------- #
# transport fixtures
# --------------------------------------------------------------------------- #
class _Recorded:
    """A transport handler wrapper that records every request it was asked for."""

    def __init__(self, handler: Callable[[httpx.Request], Any]) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> Any:
        self.requests.append(request)
        return self.handler(request)

    @property
    def count(self) -> int:
        return len(self.requests)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


def _json_response(
    payload: Any, *, status: int = 200, headers: dict[str, str] | None = None
) -> httpx.Response:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return httpx.Response(
        status, content=body, headers={"content-type": "application/json", **(headers or {})}
    )


def _payload_transport(payload: Any, *, status: int = 200) -> _Recorded:
    return _Recorded(lambda request: _json_response(payload, status=status))


def _key_for(provider: str) -> str:
    return TAVILY_KEY if provider == "tavily" else BRAVE_KEY


def _tool(transport: _Recorded, *, provider: str = "brave", api_key: Any = _UNSET) -> WebSearchTool:
    """A tool holding the selected provider's test credential unless told otherwise."""
    return WebSearchTool(
        api_key=_key_for(provider) if api_key is _UNSET else api_key,
        provider=provider,
        _transport=httpx.MockTransport(transport),
    )


async def _search(
    ledger: _Ledger,
    transport: _Recorded,
    *,
    provider: str = "brave",
    api_key: Any = _UNSET,
    query: Any = QUERY,
    **arguments: Any,
) -> str:
    """One search inside a real account scope over the fake ledger."""
    tool = _tool(transport, provider=provider, api_key=api_key)
    async with _scope(ledger):
        return await tool.execute(query=query, **arguments)


# --------------------------------------------------------------------------- #
# wire shape and normalization
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_brave_dispatches_one_pinned_get_with_the_subscription_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    transport = _payload_transport(BRAVE_PAYLOAD)

    result: dict[str, Any] = json.loads(await _search(ledger, transport, num_results=3))

    assert transport.count == 1
    request = transport.last
    assert request.method == "GET"
    assert str(request.url).startswith(web_search.BRAVE_ENDPOINT)
    assert dict(request.url.params) == {
        "q": QUERY,
        "count": "3",
        "offset": "0",
        "safesearch": "moderate",
    }
    assert request.headers["x-subscription-token"] == BRAVE_KEY
    assert request.headers["accept"] == "application/json"
    # The credential travels only in its own header, never as a bearer token.
    assert "authorization" not in request.headers
    assert result["results"] == NORMALIZED[:3]


@pytest.mark.asyncio
async def test_tavily_dispatches_one_pinned_basic_post_without_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    transport = _payload_transport(TAVILY_PAYLOAD)

    result: dict[str, Any] = json.loads(
        await _search(ledger, transport, provider="tavily", num_results=10)
    )

    assert transport.count == 1
    request = transport.last
    assert request.method == "POST"
    assert str(request.url) == web_search.TAVILY_ENDPOINT
    assert request.headers["authorization"] == f"Bearer {TAVILY_KEY}"
    assert request.headers["content-type"] == "application/json"
    assert "x-subscription-token" not in request.headers
    assert json.loads(request.content) == {
        "query": QUERY,
        "search_depth": "basic",
        "max_results": 10,
        "include_answer": False,
        "include_raw_content": False,
        "auto_parameters": False,
        "include_usage": True,
    }
    assert result["results"] == NORMALIZED


@pytest.mark.asyncio
async def test_both_providers_normalize_to_the_same_bounded_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    payloads = {"brave": BRAVE_PAYLOAD, "tavily": TAVILY_PAYLOAD}
    outputs: dict[str, dict[str, Any]] = {}
    for provider, payload in payloads.items():
        ledger = _Ledger()
        transport = _payload_transport(payload)
        outputs[provider] = json.loads(await _search(ledger, transport, provider=provider))

    assert outputs["brave"] == outputs["tavily"]
    assert set(outputs["brave"]) == {"query", "results", "total_found"}
    assert outputs["brave"]["query"] == QUERY
    assert outputs["brave"]["total_found"] == 2
    for entry in outputs["brave"]["results"]:
        assert set(entry) == {"title", "url", "description"}
        assert len(entry["description"]) <= web_search.MAX_SNIPPET_CHARS
    # Brave's non-web sections are never folded into the results.
    assert all("news.test" not in entry["url"] for entry in outputs["brave"]["results"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("asked", "wire", "returned"),
    [(None, 5, 5), (1, 1, 1), (10, 10, 10), (0, 1, 1), (-4, 1, 1), (50, 10, 10), ("7", 7, 7)],
)
async def test_result_count_is_clamped_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, asked: Any, wire: int, returned: int
) -> None:
    _approved_policy(monkeypatch)
    payload = copy.deepcopy(BRAVE_PAYLOAD)
    payload["web"]["results"] = [
        {"title": f"Result {index}", "url": f"https://example.com/{index}", "description": "d"}
        for index in range(20)
    ]
    ledger = _Ledger()
    transport = _payload_transport(payload)
    arguments = {} if asked is None else {"num_results": asked}

    result: dict[str, Any] = json.loads(await _search(ledger, transport, **arguments))

    assert transport.count == 1
    assert transport.last.url.params["count"] == str(wire)
    assert len(result["results"]) == returned == result["total_found"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry",
    [
        {"title": "Script", "url": "javascript:alert(1)"},
        {"title": "File", "url": "file:///etc/passwd"},
        {"title": "Data", "url": "data:text/html,payload"},
        {"title": "FTP", "url": "ftp://example.com/x"},
        {"title": "Credentials", "url": "https://user:pass@example.com/"},
        {"title": "Username", "url": "https://user@example.com/"},
        {"title": "Space", "url": "https://example.com/a b"},
        {"title": "Tab", "url": "https://example.com/a\tb"},
        {"title": "Nul", "url": "https://example.com/a\x00b"},
        {"title": "No scheme", "url": "example.com/kept"},
        {"title": "Scheme relative", "url": "//example.com/x"},
        {"title": "Relative", "url": "/only/a/path"},
        {"title": "Empty", "url": ""},
        {"title": "Blank", "url": "   "},
        {"title": "Null", "url": None},
        {"title": "Wrong type", "url": 17},
        {"title": "List", "url": ["https://example.com/x"]},
        {"title": "Missing"},
        {"title": "Over-long URL", "url": "https://example.com/" + "p" * 2100},
        "not-an-object",
        None,
        17,
    ],
)
async def test_an_unusable_result_entry_is_dropped_not_emitted(
    monkeypatch: pytest.MonkeyPatch, entry: Any
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    kept = {"title": "Kept", "url": "https://example.com/kept", "description": "snippet"}
    transport = _payload_transport({"web": {"results": [kept, entry]}})

    result: dict[str, Any] = json.loads(await _search(ledger, transport))

    assert result["results"] == [kept]
    assert result["total_found"] == 1
    # The call still delivered a priced unit, so it settles at the pinned price.
    assert ledger.amounts == [web_search.BRAVE_FIXED_MICROUSD]


@pytest.mark.asyncio
async def test_missing_title_and_non_string_snippet_are_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    transport = _payload_transport(
        {
            "web": {
                "results": [
                    {"title": None, "url": "https://example.com/a", "description": 12345},
                    {"url": "https://example.com/b", "description": ["nested"]},
                    {"title": "  padded  ", "url": "  https://example.com/c  "},
                ]
            }
        }
    )

    result: dict[str, Any] = json.loads(await _search(ledger, transport))

    assert result["results"] == [
        # A missing title keeps the historic placeholder; a non-string snippet empties.
        {"title": "No title", "url": "https://example.com/a", "description": ""},
        {"title": "No title", "url": "https://example.com/b", "description": ""},
        {"title": "padded", "url": "https://example.com/c", "description": ""},
    ]
    assert result["total_found"] == 3


@pytest.mark.asyncio
async def test_title_snippet_and_url_bounds_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    longest_url = "https://example.com/" + "p" * (web_search.MAX_URL_CHARS - 20)
    payload = {
        "web": {
            "results": [
                {
                    "title": "t" * 500,
                    "url": longest_url,
                    "description": "  padded snippet  ",
                },
                {"title": "Too long", "url": longest_url + "q", "description": "d"},
            ]
        }
    }
    ledger = _Ledger()
    transport = _payload_transport(payload)

    result: dict[str, Any] = json.loads(await _search(ledger, transport))

    assert len(longest_url) <= web_search.MAX_URL_CHARS
    assert result["total_found"] == 1
    assert result["results"][0]["title"] == "t" * web_search.MAX_TITLE_CHARS
    assert result["results"][0]["url"] == longest_url
    assert result["results"][0]["description"] == "padded snippet"


# --------------------------------------------------------------------------- #
# pricing and accounting
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "ceiling", "fixed"),
    [("brave", BRAVE_CEILING, 5_000), ("tavily", TAVILY_CEILING, 8_000)],
)
async def test_confirmed_call_reserves_the_ceiling_and_settles_the_pinned_price(
    monkeypatch: pytest.MonkeyPatch, provider: str, ceiling: int, fixed: int
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    payload = BRAVE_PAYLOAD if provider == "brave" else TAVILY_PAYLOAD
    transport = _payload_transport(payload)

    result: dict[str, Any] = json.loads(await _search(ledger, transport, provider=provider))

    assert result["total_found"] == 2
    assert [entry["amount"] for entry in ledger.reserves] == [ceiling]
    assert ledger.amounts == [fixed]
    assert ledger.usages == [{"tool_calls": 1}]
    assert ledger.released == []


@pytest.mark.asyncio
async def test_reservation_carries_the_parent_scope_identity_and_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    transport = _payload_transport(BRAVE_PAYLOAD)
    tool = _tool(transport)
    user_id = uuid.uuid4()

    async with _scope(
        ledger, operation="research", background=True, expected_period="2026-09", user_id=user_id
    ) as scope:
        await tool.execute(query=QUERY)

    assert ledger.reserves[0] == {
        "user_id": user_id,
        "amount": BRAVE_CEILING,
        "operation": "research",
        "provider": "brave",
        "route_id": "brave-web-search",
        "premium": False,
        "extended": False,
        "extended_run": False,
        "background": True,
        "scope_id": scope.scope_id,
        "expected_period": "2026-09",
    }
    assert ledger.settled[0][0] is ledger.reservations[0]
    assert ledger.open == []
    assert scope.outstanding == {}


@pytest.mark.asyncio
async def test_reservation_omits_the_period_option_when_the_scope_has_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    transport = _payload_transport(BRAVE_PAYLOAD)

    await _search(ledger, transport)

    assert "expected_period" not in ledger.reserves[0]


@pytest.mark.asyncio
async def test_extended_scope_takes_one_extended_run_and_charges_its_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    transport = _payload_transport(BRAVE_PAYLOAD)
    tool = _tool(transport)

    async with _scope(ledger, extended=True) as scope:
        await tool.execute(query=QUERY)
        assert scope.extended_started is True
        await tool.execute(query=QUERY)

    assert [entry["extended"] for entry in ledger.reserves] == [True, True]
    # Only the first call of one extended run consumes the run allowance.
    assert [entry["extended_run"] for entry in ledger.reserves] == [True, False]
    assert ledger.amounts == [web_search.BRAVE_FIXED_MICROUSD] * 2


@pytest.mark.asyncio
async def test_policy_ceiling_below_the_pinned_price_refuses_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch, ceiling=web_search.BRAVE_FIXED_MICROUSD - 1)
    ledger = _Ledger()
    transport = _payload_transport(BRAVE_PAYLOAD)
    tool = _tool(transport)

    async with _scope(ledger):
        result: dict[str, Any] = json.loads(await tool.execute(query=QUERY))

    assert result == {"error": web_search._SERVICE_UNAVAILABLE}
    assert transport.count == 0
    assert ledger.reserves == []
    assert ledger.settled == []
    assert tool.available() is False


@pytest.mark.asyncio
async def test_reported_extra_credits_settle_truthfully_and_stop_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    payload = copy.deepcopy(TAVILY_PAYLOAD)
    payload["usage"] = {"credits": 3}
    transport = _payload_transport(payload)

    with pytest.raises(runtime.ComputeUnavailable) as caught:
        await _search(ledger, transport, provider="tavily")

    assert caught.value.code == "tool_price_exceeded"
    assert ledger.amounts == [3 * web_search.TAVILY_FIXED_MICROUSD]
    assert ledger.amounts[0] > TAVILY_CEILING
    assert ledger.usages == [{"tool_calls": 1, "provider_units": 3}]
    assert ledger.open == []


@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [{"credits": 1}, {"credits": 0}, {}, "nope"])
async def test_absent_or_unusable_usage_still_settles_the_pinned_price(
    monkeypatch: pytest.MonkeyPatch, usage: Any
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    payload = copy.deepcopy(TAVILY_PAYLOAD)
    payload["usage"] = usage
    transport = _payload_transport(payload)

    await _search(ledger, transport, provider="tavily")

    assert ledger.amounts == [web_search.TAVILY_FIXED_MICROUSD]
    assert ledger.usages == [{"tool_calls": 1}]


@pytest.mark.asyncio
async def test_missing_usage_section_still_settles_the_pinned_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    payload = copy.deepcopy(TAVILY_PAYLOAD)
    del payload["usage"]
    transport = _payload_transport(payload)

    await _search(ledger, transport, provider="tavily")

    assert ledger.amounts == [web_search.TAVILY_FIXED_MICROUSD]


def test_deployment_policy_covers_the_pinned_search_prices() -> None:
    """The operator's ceilings must cover each adapter's pinned price."""
    deployment = parse_inference_policy(
        json.loads((ROOT / "config/inference_policy.production.json").read_text())
    )
    portable = parse_inference_policy(
        json.loads((ROOT / "config/inference_policy.json").read_text())
    )
    pinned = {
        "brave-web-search": ("brave", web_search.BRAVE_FIXED_MICROUSD),
        "tavily-web-search": ("tavily", web_search.TAVILY_FIXED_MICROUSD),
    }
    for service_id, (provider, fixed) in pinned.items():
        entry = deployment.tool_service(service_id)
        assert entry is not None
        assert entry.service == "web_search"
        assert entry.provider == provider
        assert entry.unit == "call"
        assert entry.ceiling_microusd_per_unit is not None
        assert entry.ceiling_microusd_per_unit >= fixed > 0
        # The portable default stays deny-by-default for both providers.
        assert portable.tool_service(service_id) is not None
        assert not portable.is_tool_service_approved(service_id)


# --------------------------------------------------------------------------- #
# refusals: zero network, zero charge
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("api_key", [None, "", "   "])
async def test_missing_credential_refuses_without_network_or_reservation(
    monkeypatch: pytest.MonkeyPatch, api_key: str | None
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    transport = _payload_transport(BRAVE_PAYLOAD)
    tool = WebSearchTool(
        api_key=api_key, provider="brave", _transport=httpx.MockTransport(transport)
    )

    async with _scope(ledger):
        result: dict[str, Any] = json.loads(await tool.execute(query=QUERY))

    assert result == {"error": web_search._NO_CREDENTIAL}
    assert transport.count == 0
    assert ledger.reserves == []
    assert ledger.settled == []
    assert tool.available() is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"approved": False},
        {"availability": "unverified"},
        {"availability": "degraded"},
        {"ceiling": None},
        {"review": None},
        {"review": {**REVIEW, "review_expires_at": "2026-09-28T00:00:00Z"}},
        {"review": {**REVIEW, "review_expires_at": None}},
        {"review": {**REVIEW, "evidence": []}},
        {"review": {**REVIEW, "reviewer": None}},
        {"service": "web_research"},
        {"unit": "request"},
    ],
)
async def test_unqualified_service_refuses_without_network_or_reservation(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any]
) -> None:
    _install(monkeypatch, parse_inference_policy(_approved_document(**overrides)))
    ledger = _Ledger()
    transport = _payload_transport(BRAVE_PAYLOAD)
    tool = _tool(transport)

    async with _scope(ledger):
        result: dict[str, Any] = json.loads(await tool.execute(query=QUERY))

    assert result == {"error": web_search._SERVICE_UNAVAILABLE}
    assert transport.count == 0
    assert ledger.reserves == []
    assert ledger.settled == []
    assert tool.available() is False


@pytest.mark.asyncio
async def test_another_providers_entry_never_authorizes_the_selected_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        parse_inference_policy(
            _document(_service_entry("brave-web-search", "tavily", ceiling=BRAVE_CEILING))
        ),
    )
    ledger = _Ledger()
    transport = _payload_transport(BRAVE_PAYLOAD)

    result: dict[str, Any] = json.loads(await _search(ledger, transport))

    assert result == {"error": web_search._SERVICE_UNAVAILABLE}
    assert transport.count == 0
    assert ledger.reserves == []


@pytest.mark.asyncio
async def test_an_absent_service_entry_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, parse_inference_policy(_document()))
    ledger = _Ledger()
    transport = _payload_transport(BRAVE_PAYLOAD)

    result: dict[str, Any] = json.loads(await _search(ledger, transport))

    assert result == {"error": web_search._SERVICE_UNAVAILABLE}
    assert transport.count == 0
    assert ledger.reserves == []


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "Brave", "brave ", "", None, 7, ["brave"]])
async def test_unknown_provider_selection_is_never_available(
    monkeypatch: pytest.MonkeyPatch, provider: Any
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    transport = _payload_transport(BRAVE_PAYLOAD)
    tool = WebSearchTool(
        api_key=BRAVE_KEY, provider=provider, _transport=httpx.MockTransport(transport)
    )

    assert tool.available() is False
    async with _scope(ledger):
        result: dict[str, Any] = json.loads(await tool.execute(query=QUERY))

    assert result == {"error": web_search._NOT_CONFIGURED}
    assert transport.count == 0
    assert ledger.reserves == []


@pytest.mark.asyncio
async def test_denied_budget_raises_a_typed_refusal_and_dispatches_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger(
        reserve_error=BudgetExceeded(requested=BRAVE_CEILING, spent=900, reserved=200, ceiling=1000)
    )
    transport = _payload_transport(BRAVE_PAYLOAD)

    with pytest.raises(runtime.ComputeUnavailable) as caught:
        await _search(ledger, transport)

    assert caught.value.code == "budget_exceeded"
    # The client-facing message carries no ledger numbers and no query.
    assert caught.value.message == "Compute budget for this period is used up"
    assert "900" not in caught.value.message
    assert QUERY not in caught.value.message
    assert transport.count == 0
    assert ledger.settled == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code", "message"),
    [
        (
            RateLimitExceeded(requests_in_window=4242, ceiling=4242),
            "rate_limited",
            "Too many requests; try again shortly",
        ),
        (
            ConcurrencyExceeded(open_reservations=4242, ceiling=4242),
            "concurrency_exceeded",
            "Another request is still running; try again when it finishes",
        ),
        (
            TrialExhausted("4242 SECRET-LEDGER-DETAIL"),
            "trial_exhausted",
            "Trial allowance is used up",
        ),
        (
            AccountSuspended("4242 SECRET-LEDGER-DETAIL"),
            "account_suspended",
            "Account compute is suspended",
        ),
        (
            UnknownOperation("SECRET-LEDGER-DETAIL"),
            "account_unavailable",
            "Account compute unavailable",
        ),
        (
            RuntimeError("4242 SECRET-LEDGER-DETAIL"),
            "account_unavailable",
            "Account compute unavailable",
        ),
    ],
)
async def test_ledger_refusals_keep_their_sanitized_code(
    monkeypatch: pytest.MonkeyPatch, error: Exception, code: str, message: str
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger(reserve_error=error)
    transport = _payload_transport(BRAVE_PAYLOAD)

    with pytest.raises(runtime.ComputeUnavailable) as caught:
        await _search(ledger, transport)

    assert caught.value.code == code
    # A fixed, user-safe message: no ledger detail, no exception text, no query.
    assert caught.value.message == message
    for leaked in ("SECRET-LEDGER-DETAIL", "4242", QUERY, BRAVE_KEY):
        assert leaked not in caught.value.message
    assert transport.count == 0
    assert ledger.settled == []


@pytest.mark.asyncio
async def test_missing_capability_refuses_before_reserving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger(capabilities=("chat",))
    transport = _payload_transport(BRAVE_PAYLOAD)

    with pytest.raises(runtime.ComputeUnavailable) as caught:
        await _search(ledger, transport)

    assert caught.value.code == "capability_unavailable"
    assert transport.count == 0
    assert ledger.reserves == []
    assert ledger.settled == []


@pytest.mark.asyncio
async def test_missing_account_scope_refuses_before_any_accounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    transport = _payload_transport(BRAVE_PAYLOAD)
    tool = _tool(transport)

    with pytest.raises(runtime.ComputeUnavailable) as caught:
        await tool.execute(query=QUERY)

    assert caught.value.code == "account_unavailable"
    assert transport.count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        None,
        "",
        "   ",
        "bad\ud800query",
        17,
        ["a", "list"],
        {"query": "nested"},
        "a" * (web_search.MAX_QUERY_CHARS + 1),
        "a" * (web_search.BRAVE_MAX_QUERY_CHARS + 1),
        " ".join(["word"] * (web_search.BRAVE_MAX_QUERY_WORDS + 1)),
        "\U0001d11e" * 1200,
    ],
)
async def test_malformed_queries_cost_nothing_and_touch_no_network(
    monkeypatch: pytest.MonkeyPatch, query: Any
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    transport = _payload_transport(BRAVE_PAYLOAD)
    tool = _tool(transport)

    async with _scope(ledger):
        result: dict[str, Any] = json.loads(await tool.execute(query=query))

    assert result["error"] in {web_search._INVALID_QUERY, web_search._QUERY_TOO_LONG}
    assert transport.count == 0
    assert ledger.reserves == []
    assert ledger.settled == []


@pytest.mark.asyncio
async def test_multi_byte_queries_are_bounded_by_their_encoded_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    transport = _payload_transport(BRAVE_PAYLOAD)
    tool = _tool(transport)
    # 1200 four-byte characters: inside the character bound, outside the byte one.
    query = "\U0001d11e" * 1200
    assert len(query) <= web_search.MAX_QUERY_CHARS
    assert len(query.encode()) > web_search.MAX_QUERY_BYTES

    async with _scope(ledger):
        result: dict[str, Any] = json.loads(await tool.execute(query=query))

    assert result == {"error": web_search._QUERY_TOO_LONG}
    assert transport.count == 0
    assert ledger.reserves == []


@pytest.mark.asyncio
async def test_tavily_enforces_its_own_documented_query_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    transport = _payload_transport(TAVILY_PAYLOAD)
    query = "q" * (web_search.TAVILY_MAX_QUERY_CHARS + 1)

    result: dict[str, Any] = json.loads(
        await _search(ledger, transport, provider="tavily", query=query)
    )

    assert result == {"error": web_search._QUERY_TOO_LONG}
    assert transport.count == 0
    assert ledger.reserves == []


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [True, False, "many", "", 1.5, float("nan"), {"n": 1}, [3]])
async def test_malformed_result_counts_refuse_without_dispatch(
    monkeypatch: pytest.MonkeyPatch, count: Any
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    transport = _payload_transport(BRAVE_PAYLOAD)
    tool = _tool(transport)

    async with _scope(ledger):
        result: dict[str, Any] = json.loads(await tool.execute(query=QUERY, num_results=count))

    assert result == {"error": web_search._INVALID_COUNT}
    assert transport.count == 0
    assert ledger.reserves == []


@pytest.mark.asyncio
async def test_the_query_is_never_echoed_by_a_refusal(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger(reserve_error=BudgetExceeded(requested=1, spent=1, reserved=0, ceiling=1))
    transport = _payload_transport(BRAVE_PAYLOAD)
    tool = _tool(transport)

    with caplog.at_level("WARNING"):
        with pytest.raises(runtime.ComputeUnavailable):
            async with _scope(ledger):
                await tool.execute(query=QUERY)

    assert QUERY not in caplog.text
    assert BRAVE_KEY not in caplog.text


# --------------------------------------------------------------------------- #
# transport failures: bounded, never retried, conservatively settled
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_redirects_are_never_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    transport = _Recorded(
        lambda request: httpx.Response(
            302, headers={"location": "https://attacker.test/collect"}, content=b"moved"
        )
    )

    result: dict[str, Any] = json.loads(await _search(ledger, transport))

    assert result == {"error": web_search._HTTP_ERROR}
    assert transport.count == 1
    assert ledger.amounts == [BRAVE_CEILING]
    assert ledger.usages == [{"estimated_cost": True}]


@pytest.mark.asyncio
async def test_declared_oversize_response_is_refused_before_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    transport = _Recorded(
        lambda request: _json_response(
            b"{}", headers={"content-length": str(web_search.MAX_RESPONSE_BYTES + 1)}
        )
    )

    result: dict[str, Any] = json.loads(await _search(ledger, transport))

    assert result == {"error": web_search._TOO_LARGE}
    assert transport.count == 1
    assert ledger.amounts == [BRAVE_CEILING]


@pytest.mark.asyncio
async def test_streamed_body_is_aborted_at_the_byte_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    # A lying content-length: the declared check passes and the streaming cap binds.
    oversized = b"{" + b" " * (web_search.MAX_RESPONSE_BYTES + 16)
    transport = _Recorded(
        lambda request: _json_response(oversized, headers={"content-length": "10"})
    )

    result: dict[str, Any] = json.loads(await _search(ledger, transport))

    assert result == {"error": web_search._TOO_LARGE}
    assert transport.count == 1
    assert ledger.amounts == [BRAVE_CEILING]
    assert ledger.usages == [{"estimated_cost": True}]


@pytest.mark.asyncio
async def test_provider_read_timeout_settles_the_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    transport = _Recorded(handler)

    result: dict[str, Any] = json.loads(await _search(ledger, transport))

    assert result == {"error": web_search._TIMEOUT}
    assert transport.count == 1
    assert ledger.amounts == [BRAVE_CEILING]
    assert ledger.usages == [{"estimated_cost": True}]


@pytest.mark.asyncio
async def test_wall_deadline_bounds_a_silent_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _approved_policy(monkeypatch)
    monkeypatch.setattr(web_search, "SEARCH_DEADLINE_S", 0.05)
    ledger = _Ledger()

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return _json_response(BRAVE_PAYLOAD)

    transport = _Recorded(handler)

    result: dict[str, Any] = json.loads(await _search(ledger, transport))

    assert result == {"error": web_search._TIMEOUT}
    assert transport.count == 1
    assert ledger.amounts == [BRAVE_CEILING]


@pytest.mark.asyncio
async def test_connection_failure_is_sanitized_and_never_retried(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url} with {BRAVE_KEY}", request=request)

    transport = _Recorded(handler)

    with caplog.at_level("WARNING"):
        result: dict[str, Any] = json.loads(await _search(ledger, transport))

    assert result == {"error": web_search._UNREACHABLE}
    assert transport.count == 1
    assert ledger.amounts == [BRAVE_CEILING]
    # Neither the tool result nor any log record carries the query, key or URL.
    for text in (json.dumps(result), caplog.text):
        assert QUERY not in text
        assert BRAVE_KEY not in text
        assert "api.search.brave.com" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "message"),
    [
        (400, web_search._REJECTED),
        (401, web_search._CREDENTIAL_REJECTED),
        (402, web_search._LIMITED),
        (403, web_search._CREDENTIAL_REJECTED),
        (404, web_search._REJECTED),
        (408, web_search._REJECTED),
        (413, web_search._REJECTED),
        (422, web_search._REJECTED),
        (429, web_search._LIMITED),
        (432, web_search._LIMITED),
        (433, web_search._LIMITED),
        (451, web_search._REJECTED),
        (500, web_search._HTTP_ERROR),
        (503, web_search._HTTP_ERROR),
    ],
)
async def test_http_statuses_map_to_fixed_messages_without_provider_text(
    monkeypatch: pytest.MonkeyPatch, status: int, message: str
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    # A provider error body echoing the query must never be read or returned.
    body = json.dumps({"detail": {"error": f"rejected {QUERY}"}}).encode()
    transport = _Recorded(lambda request: httpx.Response(status, content=body))

    result: dict[str, Any] = json.loads(await _search(ledger, transport))

    assert result == {"error": message}
    assert QUERY not in json.dumps(result)
    assert transport.count == 1
    assert ledger.amounts == [BRAVE_CEILING]
    assert ledger.usages == [{"estimated_cost": True}]


@pytest.mark.asyncio
async def test_unexpected_transport_exception_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()

    def handler(request: httpx.Request) -> httpx.Response:
        raise ValueError(f"internal detail mentioning {QUERY}")

    transport = _Recorded(handler)

    result: dict[str, Any] = json.loads(await _search(ledger, transport))

    assert result == {"error": web_search._FAILED}
    assert QUERY not in json.dumps(result)
    assert ledger.amounts == [BRAVE_CEILING]


# --------------------------------------------------------------------------- #
# out-of-contract responses
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "payload"),
    [
        ("brave", []),
        ("brave", "not-json"),
        ("brave", 17),
        ("brave", {}),
        ("brave", {"web": 5}),
        ("brave", {"web": {}}),
        ("brave", {"web": {"results": "nope"}}),
        ("brave", {"web": {"results": None}}),
        ("brave", b"{not json"),
        ("brave", b""),
        ("tavily", {}),
        ("tavily", {"results": {}}),
        ("tavily", {"results": None}),
        ("tavily", []),
    ],
)
async def test_out_of_contract_bodies_settle_the_reservation(
    monkeypatch: pytest.MonkeyPatch, provider: str, payload: Any
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    transport = _payload_transport(payload)

    result: dict[str, Any] = json.loads(await _search(ledger, transport, provider=provider))

    assert result == {"error": web_search._INVALID_RESPONSE}
    assert transport.count == 1
    expected = BRAVE_CEILING if provider == "brave" else TAVILY_CEILING
    assert ledger.amounts == [expected]
    assert ledger.usages == [{"estimated_cost": True}]


@pytest.mark.asyncio
async def test_empty_result_set_is_a_delivered_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    transport = _payload_transport({"type": "search", "web": None})

    result: dict[str, Any] = json.loads(await _search(ledger, transport))

    assert result == {"query": QUERY, "results": [], "total_found": 0}
    assert ledger.amounts == [web_search.BRAVE_FIXED_MICROUSD]
    assert ledger.usages == [{"tool_calls": 1}]


@pytest.mark.asyncio
async def test_results_with_no_usable_entry_still_settle_the_pinned_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    transport = _payload_transport(
        {"web": {"results": [{"title": "Bad", "url": "javascript:alert(1)"}]}}
    )

    result: dict[str, Any] = json.loads(await _search(ledger, transport))

    assert result["results"] == []
    assert ledger.amounts == [web_search.BRAVE_FIXED_MICROUSD]


# --------------------------------------------------------------------------- #
# cancellation
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_cancellation_mid_dispatch_settles_the_hold_conservatively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled search is charged the hold, exactly once, by its own scope."""
    _approved_policy(monkeypatch)
    ledger = _Ledger()
    monkeypatch.setattr(runtime, "EntitlementService", lambda pool: ledger)
    entered = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.sleep(30)
        return _json_response(BRAVE_PAYLOAD)

    transport = _Recorded(handler)
    tool = _tool(transport)

    async with runtime.account_compute(object(), uuid.uuid4(), operation="chat") as scope:
        task = asyncio.create_task(tool.execute(query=QUERY))
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        outcome = (await asyncio.gather(task, return_exceptions=True))[0]

    assert isinstance(outcome, asyncio.CancelledError)
    assert transport.count == 1
    assert scope.outstanding == {}
    assert len(ledger.settled) == 1
    assert ledger.amounts == [BRAVE_CEILING]
    assert ledger.usages == [{"estimated_cost": True}]
    assert ledger.released == []


# --------------------------------------------------------------------------- #
# policy re-check around the reservation
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_approval_is_checked_before_reserving_and_again_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads = _approved_policy(monkeypatch)
    ledger = _Ledger()
    transport = _payload_transport(BRAVE_PAYLOAD)

    await _search(ledger, transport)

    # One load for the tool's own gate, one for the pre-dispatch re-check.
    assert loads() == 2


@pytest.mark.asyncio
async def test_revocation_while_reserving_releases_the_hold_and_never_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = parse_inference_policy(_approved_document())
    revoked = parse_inference_policy(_approved_document(approved=False))
    _install(monkeypatch, approved, revoked)
    ledger = _Ledger()
    transport = _payload_transport(BRAVE_PAYLOAD)
    tool = _tool(transport)

    async with _scope(ledger) as scope:
        with pytest.raises(runtime.ComputeUnavailable) as caught:
            await tool.execute(query=QUERY)
        assert scope.outstanding == {}

    assert caught.value.code == "tool_service_unavailable"
    assert transport.count == 0
    assert len(ledger.reserves) == 1
    assert ledger.released == []
    assert ledger.amounts == [0]


@pytest.mark.asyncio
async def test_failed_zero_settlement_retains_known_zero_and_stops_without_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = parse_inference_policy(_approved_document())
    revoked = parse_inference_policy(_approved_document(approved=False))
    _install(monkeypatch, approved, revoked)
    ledger = _Ledger(settle_error=RuntimeError("ledger unavailable"))
    transport = _payload_transport(BRAVE_PAYLOAD)
    tool = _tool(transport)

    async with _scope(ledger) as scope:
        with pytest.raises(runtime.ComputeUnavailable) as caught:
            await tool.execute(query=QUERY)
        assert caught.value.code == "settlement_failed"
        hold = next(iter(scope.outstanding.values()))
        assert hold.actual == 0

    # No provider was dispatched. A ledger failure must not fabricate usage.
    assert ledger.released == []
    assert ledger.amounts == []
    assert transport.count == 0


# --------------------------------------------------------------------------- #
# settlement failures
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_settlement_failure_stops_the_operation_instead_of_returning_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger(settle_error=RuntimeError("ledger unavailable"))
    transport = _payload_transport(BRAVE_PAYLOAD)
    tool = _tool(transport)

    async with _scope(ledger) as scope:
        with pytest.raises(runtime.ComputeUnavailable) as caught:
            await tool.execute(query=QUERY)
        # The unsettled hold stays registered, so the scope's own cleanup still
        # pursues the accounting instead of dropping the charge.
        assert len(scope.outstanding) == 1
        hold = next(iter(scope.outstanding.values()))
        assert hold.actual == web_search.BRAVE_FIXED_MICROUSD

    assert caught.value.code == "settlement_failed"
    assert caught.value.category == runtime.FAILURE_SETTLEMENT_FAILED
    assert caught.value.retryable is False
    assert transport.count == 1


@pytest.mark.asyncio
async def test_settlement_failure_replaces_a_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger(settle_error=RuntimeError("ledger unavailable"))

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    transport = _Recorded(handler)

    with pytest.raises(runtime.ComputeUnavailable) as caught:
        await _search(ledger, transport)

    # Incomplete accounting is never hidden behind a retryable provider error.
    assert caught.value.code == "settlement_failed"
    assert caught.value.retryable is False


# --------------------------------------------------------------------------- #
# scope sharing
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_search_never_opens_a_nested_account_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    _approved_policy(monkeypatch)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("metered search must reuse the caller's account scope")

    monkeypatch.setattr(runtime, "account_compute", forbidden)
    ledger = _Ledger()
    transport = _payload_transport(BRAVE_PAYLOAD)

    result: dict[str, Any] = json.loads(await _search(ledger, transport))

    assert result["total_found"] == 2


@pytest.mark.asyncio
async def test_a_second_call_shares_the_scope_and_its_concurrency_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    ledger = _Ledger(max_open=1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return _json_response(BRAVE_PAYLOAD)

    transport = _Recorded(handler)
    tool = _tool(transport)

    async with _scope(ledger) as scope:
        first = asyncio.create_task(tool.execute(query=QUERY))
        await asyncio.wait_for(entered.wait(), timeout=5)
        # While the first call holds the slot, a second one is refused by the
        # ledger's own concurrency ceiling and never reaches the provider.
        with pytest.raises(runtime.ComputeUnavailable) as caught:
            await tool.execute(query=QUERY)
        release.set()
        done: dict[str, Any] = json.loads(await first)

    assert caught.value.code == "concurrency_exceeded"
    assert transport.count == 1
    assert done["total_found"] == 2
    # Both calls asked the same ledger on the same scope; only one was admitted.
    assert len(ledger.attempts) == 2
    assert len(ledger.reserves) == 1
    assert {entry["scope_id"] for entry in ledger.attempts} == {scope.scope_id}
    assert ledger.amounts == [web_search.BRAVE_FIXED_MICROUSD]


# --------------------------------------------------------------------------- #
# availability predicate
# --------------------------------------------------------------------------- #
def test_available_requires_a_credential_and_an_approved_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _approved_policy(monkeypatch)
    assert WebSearchTool(api_key=BRAVE_KEY, provider="brave").available() is True
    assert WebSearchTool(api_key=TAVILY_KEY, provider="tavily").available() is True
    assert WebSearchTool(api_key=None, provider="brave").available() is False
    assert WebSearchTool(api_key="  ", provider="tavily").available() is False
    assert WebSearchTool(api_key=BRAVE_KEY, provider="searxng").available() is False
    assert WebSearchTool(api_key=BRAVE_KEY).provider == "brave"
    assert WebSearchTool(api_key=BRAVE_KEY).api_key == BRAVE_KEY


def test_available_is_a_sync_predicate_usable_outside_an_account_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registry construction has no scope, no ledger and no running loop."""
    _approved_policy(monkeypatch)
    assert runtime._scope.get() is None
    assert inspect.iscoroutinefunction(WebSearchTool.available) is False
    assert WebSearchTool(api_key=BRAVE_KEY).available() is True


def test_available_follows_the_operators_manual_review_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-expiring manual review is honoured with no adapter special-casing."""
    manual = {
        "reviewer": "deployment-operator",
        "reviewed_at": "2026-09-29T00:00:00Z",
        "review_expires_at": None,
        "evidence": ["docs/SEARCH_SERVICE_APPROVALS.md"],
    }
    _install(
        monkeypatch,
        parse_inference_policy(_approved_document(review=manual, review_mode="manual")),
    )
    assert WebSearchTool(api_key=BRAVE_KEY).available() is True

    # A manual review that also carries an expiry is contradictory: fail closed.
    _install(
        monkeypatch,
        parse_inference_policy(
            _approved_document(
                review={**manual, "review_expires_at": "2030-01-01T00:00:00Z"},
                review_mode="manual",
            )
        ),
    )
    assert WebSearchTool(api_key=BRAVE_KEY).available() is False


@pytest.mark.asyncio
async def test_registration_never_authorizes_a_later_revoked_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = parse_inference_policy(_approved_document())
    revoked = parse_inference_policy(_approved_document(approved=False))
    loads = _install(monkeypatch, approved, revoked)
    ledger = _Ledger()
    transport = _payload_transport(BRAVE_PAYLOAD)
    tool = _tool(transport)

    # The registry's own predicate saw the approval...
    assert tool.available() is True
    assert loads() == 1

    async with _scope(ledger):
        result: dict[str, Any] = json.loads(await tool.execute(query=QUERY))

    # ...and dispatch still enforces the gate on the now-revoked service.
    assert result == {"error": web_search._SERVICE_UNAVAILABLE}
    assert transport.count == 0
    assert ledger.reserves == []
    assert ledger.settled == []
