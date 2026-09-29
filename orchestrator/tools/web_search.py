"""Direct web search: one operator-selected provider, metered per call.

Search is a paid external service, so it is treated like any other billable
dispatch and not like a free local helper:

* **One provider, no fallback.** The operator selects ``brave`` or ``tavily`` in
  deployment configuration. Selection and a configured credential are never a
  dispatch permission: the central inference policy must also approve that
  provider's ``web_search`` service, and a denied provider is never silently
  replaced by the other one.
* **Reserved before I/O.** Every call holds the operator's approved per-call
  ceiling on the caller's existing account compute scope before any byte leaves
  the process, so budget, rate and concurrency ceilings apply to search exactly
  as they do to inference. A confirmed call settles its pinned provider price;
  a timeout, cancellation, transport or HTTP failure, or an out-of-contract
  body settles the whole hold, which is the ledger's existing conservative
  unknown-usage contract.
* **Bounded.** One call returns at most :data:`MAX_RESULTS` results inside a
  :data:`SEARCH_DEADLINE_S` wall deadline, with a bounded query, a bounded
  response body and bounded output fields. Redirects and transport retries are
  disabled, so a call is one request to one pinned endpoint.
* **Sanitized.** Refusals and failures return a fixed message. Provider error
  text is never read, logged or returned, and neither the query, the credential
  nor the request URL is ever interpolated into a message or a log record:
  exception text from an HTTP client carries the URL, and the URL carries the
  query.

Results are evidence, not instructions: they keep their source URLs and are
fenced as untrusted tool output by the caller.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

import httpx
from typing_extensions import override

from orchestrator.compute_runtime import (
    ComputeUnavailable,
    ToolServiceApproval,
    approved_tool_service,
    metered_tool_call,
)
from orchestrator.tools.registry import Tool

logger = logging.getLogger(__name__)

#: Inference-policy tool service identity shared by every search adapter. The
#: policy entry must name exactly these, so a repurposed entry cannot authorize
#: a different service, provider or billing unit.
SEARCH_SERVICE: Final[str] = "web_search"
SEARCH_UNIT: Final[str] = "call"

#: Account capability a search dispatch requires. Granted by every shipped plan;
#: checked so a plan that ever withdraws it refuses before any reservation.
SEARCH_CAPABILITY: Final[str] = "web_research"

#: Product bounds for one call: at most ten results, five by default.
MAX_RESULTS: Final[int] = 10
DEFAULT_RESULTS: Final[int] = 5

#: One wall-clock deadline for the whole provider round trip, reads included.
SEARCH_DEADLINE_S: Final[float] = 30.0

#: Tool-level query bounds, applied before the provider's own wire limit. The
#: encoded bound is independent and tighter than 2000 four-byte characters, so a
#: query cannot be large on the wire merely by being multi-byte.
MAX_QUERY_CHARS: Final[int] = 2000
MAX_QUERY_BYTES: Final[int] = 4096

#: Decoded response cap. Read as a stream and aborted at the cap, so a bloated
#: or hostile body is never buffered whole.
MAX_RESPONSE_BYTES: Final[int] = 1_048_576

#: Output bounds, so ten results stay a small, predictable tool payload.
MAX_TITLE_CHARS: Final[int] = 200
MAX_SNIPPET_CHARS: Final[int] = 300
MAX_URL_CHARS: Final[int] = 2048

#: Pinned per-call prices, in integer microusd, from each provider's published
#: price list. Each policy entry's ``ceiling_microusd_per_unit`` must cover its
#: provider's price, so the hold always covers the settlement.
#: Brave Search: USD 5 per 1,000 calls (https://brave.com/search/api/).
BRAVE_FIXED_MICROUSD: Final[int] = 5_000
#: Tavily: one basic-search credit at USD 0.008 pay-as-you-go
#: (https://docs.tavily.com/documentation/api-credits).
TAVILY_FIXED_MICROUSD: Final[int] = 8_000

#: Provider query wire limits, checked locally so a request the provider would
#: reject is never dispatched (and never paid for). Brave publishes 600
#: characters / 75 words today and 400 / 50 historically; Tavily publishes 400
#: characters. Both were checked on 30 September 2026, and the stricter
#: published bound is the one enforced.
BRAVE_MAX_QUERY_CHARS: Final[int] = 400
BRAVE_MAX_QUERY_WORDS: Final[int] = 50
TAVILY_MAX_QUERY_CHARS: Final[int] = 400

BRAVE_ENDPOINT: Final[str] = "https://api.search.brave.com/res/v1/web/search"
TAVILY_ENDPOINT: Final[str] = "https://api.tavily.com/search"

# Fixed, user- and model-safe messages. Nothing dynamic is ever concatenated
# into them, which is what keeps a query, a credential or a provider body out of
# a tool result.
_NOT_CONFIGURED: Final[str] = "Search provider is not configured"
_NO_CREDENTIAL: Final[str] = "Search credential is not configured"
_SERVICE_UNAVAILABLE: Final[str] = "Approved search service unavailable"
_INVALID_QUERY: Final[str] = "Search query must be a non-empty string"
_QUERY_TOO_LONG: Final[str] = "Search query is too long"
_INVALID_COUNT: Final[str] = f"num_results must be an integer between 1 and {MAX_RESULTS}"
_TIMEOUT: Final[str] = "Search provider timed out"
_UNREACHABLE: Final[str] = "Search provider could not be reached"
_REJECTED: Final[str] = "Search provider rejected the request"
_CREDENTIAL_REJECTED: Final[str] = "Search provider rejected the configured credential"
_LIMITED: Final[str] = "Search provider refused under a rate or billing limit"
_HTTP_ERROR: Final[str] = "Search provider returned an error"
_INVALID_RESPONSE: Final[str] = "Search provider returned an invalid response"
_TOO_LARGE: Final[str] = "Search response was too large"
_FAILED: Final[str] = "Search request failed"


class _SearchFailure(Exception):
    """A sanitized search failure whose message is safe to show a model.

    ``category`` is a closed-vocabulary label for logs only. Neither field ever
    carries query text, a credential, a URL or provider response text.
    """

    def __init__(self, message: str, *, category: str) -> None:
        self.message = message
        self.category = category
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class _WireRequest:
    """One pinned provider request."""

    method: str
    url: str
    headers: dict[str, str]
    params: dict[str, Any] | None = None
    json_body: dict[str, Any] | None = None


_WireBuilder = Callable[[str, str, str, int], _WireRequest]
_EntriesExtractor = Callable[[Mapping[str, Any]], list[Any]]
_UnitsReader = Callable[[Mapping[str, Any]], int | None]


@dataclass(frozen=True, slots=True)
class _SearchAdapter:
    """One provider's fixed endpoint, pinned price, wire limits and shapes.

    Both adapters normalize to the same output — ``title``, ``url`` and
    ``description`` — so switching provider changes nothing downstream.
    """

    provider: str
    service_id: str
    endpoint: str
    fixed_microusd: int
    max_query_chars: int
    max_query_words: int | None
    snippet_field: str
    build: _WireBuilder
    entries: _EntriesExtractor
    #: Provider-reported units for this call, when the provider reports any.
    reported_units: _UnitsReader | None


def _brave_wire(endpoint: str, credential: str, query: str, count: int) -> _WireRequest:
    """The existing Brave web-search GET, with the subscription token header."""
    return _WireRequest(
        method="GET",
        url=endpoint,
        headers={"X-Subscription-Token": credential, "Accept": "application/json"},
        params={"q": query, "count": count, "offset": 0, "safesearch": "moderate"},
    )


def _tavily_wire(endpoint: str, credential: str, query: str, count: int) -> _WireRequest:
    """One Tavily basic search: no answer, no raw content, no auto parameters."""
    return _WireRequest(
        method="POST",
        url=endpoint,
        headers={
            "Authorization": f"Bearer {credential}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json_body={
            "query": query,
            # Basic depth is the one-credit, pinned-price call. Generated
            # answers, raw page content and provider-chosen parameters stay off,
            # so nothing can silently move this call to a dearer product.
            "search_depth": "basic",
            "max_results": count,
            "include_answer": False,
            "include_raw_content": False,
            "auto_parameters": False,
            "include_usage": True,
        },
    )


def _brave_entries(payload: Mapping[str, Any]) -> list[Any]:
    """``web.results``. The section is nullable, so null means no web results.

    A present section without a result list is contract drift, not an empty
    result set, and is refused rather than silently reported as no results.
    """
    if "web" not in payload:
        raise _SearchFailure(_INVALID_RESPONSE, category="invalid_response")
    web = payload["web"]
    if web is None:
        return []
    if not isinstance(web, dict):
        raise _SearchFailure(_INVALID_RESPONSE, category="invalid_response")
    results = web.get("results")
    if not isinstance(results, list):
        raise _SearchFailure(_INVALID_RESPONSE, category="invalid_response")
    return list(results)


def _tavily_entries(payload: Mapping[str, Any]) -> list[Any]:
    results = payload.get("results")
    if not isinstance(results, list):
        raise _SearchFailure(_INVALID_RESPONSE, category="invalid_response")
    return list(results)


def _tavily_units(payload: Mapping[str, Any]) -> int | None:
    """Credits Tavily says this call consumed, when it reported a usable count."""
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    credits = usage.get("credits")
    if isinstance(credits, bool) or not isinstance(credits, int) or credits < 0:
        return None
    return credits


_ADAPTERS: Final[dict[str, _SearchAdapter]] = {
    "brave": _SearchAdapter(
        provider="brave",
        service_id="brave-web-search",
        endpoint=BRAVE_ENDPOINT,
        fixed_microusd=BRAVE_FIXED_MICROUSD,
        max_query_chars=BRAVE_MAX_QUERY_CHARS,
        max_query_words=BRAVE_MAX_QUERY_WORDS,
        snippet_field="description",
        build=_brave_wire,
        entries=_brave_entries,
        reported_units=None,
    ),
    "tavily": _SearchAdapter(
        provider="tavily",
        service_id="tavily-web-search",
        endpoint=TAVILY_ENDPOINT,
        fixed_microusd=TAVILY_FIXED_MICROUSD,
        max_query_chars=TAVILY_MAX_QUERY_CHARS,
        max_query_words=None,
        snippet_field="content",
        build=_tavily_wire,
        entries=_tavily_entries,
        reported_units=_tavily_units,
    ),
}


def _refusal(message: str) -> str:
    """A sanitized tool result carrying one fixed message."""
    return json.dumps({"error": message})


def _bounded_query(adapter: _SearchAdapter, value: Any) -> str:
    """A validated query, or a refusal. Runs before any reservation or dispatch.

    Over-long input is refused rather than truncated: silently shortening a
    query changes what was asked, and dispatching a query the provider
    documents as too long would pay for a rejection.
    """
    if not isinstance(value, str):
        raise _SearchFailure(_INVALID_QUERY, category="invalid_query")
    query = value.strip()
    if not query:
        raise _SearchFailure(_INVALID_QUERY, category="invalid_query")
    try:
        encoded_size = len(query.encode("utf-8"))
    except UnicodeError:
        raise _SearchFailure(_INVALID_QUERY, category="invalid_query") from None
    if len(query) > MAX_QUERY_CHARS or encoded_size > MAX_QUERY_BYTES:
        raise _SearchFailure(_QUERY_TOO_LONG, category="query_too_long")
    if len(query) > adapter.max_query_chars or (
        adapter.max_query_words is not None and len(query.split()) > adapter.max_query_words
    ):
        raise _SearchFailure(_QUERY_TOO_LONG, category="query_too_long")
    return query


def _bounded_count(value: Any) -> int:
    """The requested result count, clamped to the product bounds."""
    if value is None:
        return DEFAULT_RESULTS
    if isinstance(value, bool):
        raise _SearchFailure(_INVALID_COUNT, category="invalid_num_results")
    if isinstance(value, int):
        count = value
    elif isinstance(value, float) and value.is_integer():
        count = int(value)
    elif isinstance(value, str):
        try:
            count = int(value.strip())
        except ValueError:
            raise _SearchFailure(_INVALID_COUNT, category="invalid_num_results") from None
    else:
        raise _SearchFailure(_INVALID_COUNT, category="invalid_num_results")
    return min(MAX_RESULTS, max(1, count))


def _bounded_text(value: Any, limit: int) -> str:
    """Provider display text as a bounded string; anything else becomes empty."""
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _public_url(value: Any) -> str | None:
    """A bounded public http(s) result URL, or None.

    Only http and https survive, with a real host and no embedded credentials,
    so a provider (or a page it indexed) cannot push a ``javascript:``,
    ``file:`` or credential-bearing URL into the transcript. Whitespace and
    control characters are refused rather than normalized.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > MAX_URL_CHARS:
        return None
    if any(
        character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
        for character in candidate
    ):
        return None
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    if not parts.hostname or parts.username or parts.password:
        return None
    return candidate


def _normalized_entry(entry: Any, snippet_field: str) -> dict[str, str] | None:
    """One provider result as ``title``/``url``/``description``, or None.

    An entry without a usable public URL is dropped rather than emitted, so an
    unusable result can never reach the model as a citation.
    """
    if not isinstance(entry, dict):
        return None
    url = _public_url(entry.get("url"))
    if url is None:
        return None
    title = _bounded_text(entry.get("title"), MAX_TITLE_CHARS)
    return {
        "title": title or "No title",
        "url": url,
        "description": _bounded_text(entry.get(snippet_field), MAX_SNIPPET_CHARS),
    }


def _normalized_results(
    adapter: _SearchAdapter, payload: Mapping[str, Any], count: int
) -> list[dict[str, str]]:
    entries = adapter.entries(payload)
    results: list[dict[str, str]] = []
    for entry in entries[:count]:
        normalized = _normalized_entry(entry, adapter.snippet_field)
        if normalized is not None:
            results.append(normalized)
    return results


def _status_failure(status: int) -> tuple[str, str]:
    """A fixed message and log label for one upstream status code."""
    category = f"http_{status}"
    if status in (401, 403):
        return _CREDENTIAL_REJECTED, category
    if status in (402, 429, 432, 433):
        return _LIMITED, category
    if 400 <= status < 500:
        return _REJECTED, category
    return _HTTP_ERROR, category


async def _bounded_body(response: httpx.Response) -> bytes:
    """At most :data:`MAX_RESPONSE_BYTES` of decoded body, aborted at the cap.

    The declared length is checked first so an obviously oversized body is
    refused before it is read; an unparsable length proves nothing, and the
    streaming cap still binds.
    """
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            oversized = int(declared) > MAX_RESPONSE_BYTES
        except ValueError:
            oversized = False
        if oversized:
            raise _SearchFailure(_TOO_LARGE, category="response_too_large")
    buffer = bytearray()
    async for chunk in response.aiter_bytes():
        if len(buffer) + len(chunk) > MAX_RESPONSE_BYTES:
            raise _SearchFailure(_TOO_LARGE, category="response_too_large")
        buffer.extend(chunk)
    return bytes(buffer)


def _decoded_json(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except ValueError:
        raise _SearchFailure(_INVALID_RESPONSE, category="invalid_response") from None
    if not isinstance(payload, dict):
        raise _SearchFailure(_INVALID_RESPONSE, category="invalid_response")
    return payload


def _reported_units(adapter: _SearchAdapter, payload: Mapping[str, Any]) -> int | None:
    """Provider-reported units, when the response contains a valid count."""
    reader = adapter.reported_units
    if reader is None:
        return None
    return reader(payload)


class WebSearchTool(Tool):
    """``web_search``: one budgeted call to the operator-selected provider."""

    name: str = "web_search"
    description: str = (
        "Search the public web through the deployment's approved search provider and return "
        "result titles, URLs and snippets. Send minimal useful search terms, not whole "
        "conversations, documents or memory records. Each call is budgeted; a failed call is "
        "not retried automatically."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search query terms (at most "
                    f"{min(adapter.max_query_chars for adapter in _ADAPTERS.values())} characters)"
                ),
            },
            "num_results": {
                "type": "integer",
                "description": f"Number of search results to return (1-{MAX_RESULTS})",
                "default": DEFAULT_RESULTS,
                "minimum": 1,
                "maximum": MAX_RESULTS,
            },
        },
        "required": ["query"],
    }

    def __init__(
        self,
        api_key: str | None = None,
        *,
        provider: str = "brave",
        _transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # The credential and the operator's provider selection are read from
        # Settings by the registry factory and passed in here — do not call
        # os.environ in a tool.
        self.api_key = api_key
        self.provider = provider
        #: Test seam. Production always builds the pinned no-retry transport.
        self._transport = _transport

    # ------------------------------------------------------------ availability
    def _adapter(self) -> _SearchAdapter | None:
        """The selected provider's adapter, or None if the selection is unknown.

        Matched exactly, never normalized: the operator's selection is a
        validated setting, so an unexpected value is a configuration error that
        fails closed instead of being guessed at.
        """
        provider = self.provider
        if not isinstance(provider, str):
            return None
        return _ADAPTERS.get(provider)

    def _credential(self) -> str | None:
        """The selected provider's credential, or None if it is not configured."""
        api_key = self.api_key
        if not isinstance(api_key, str):
            return None
        return api_key.strip() or None

    def _service(self) -> ToolServiceApproval | None:
        """The approved service for the selected provider, or None.

        Two independent facts, both required: the operator selected a provider
        whose credential is configured, and the central inference policy
        currently approves that provider's search service with a ceiling that
        covers its pinned price.
        """
        adapter = self._adapter()
        if adapter is None or self._credential() is None:
            return None
        try:
            return approved_tool_service(
                service_id=adapter.service_id,
                service=SEARCH_SERVICE,
                provider=adapter.provider,
                unit=SEARCH_UNIT,
                fixed_microusd=adapter.fixed_microusd,
            )
        except ComputeUnavailable:
            return None

    def available(self) -> bool:
        """Whether the registry may advertise this tool.

        This is an advertisement filter, never a dispatch permission:
        :meth:`execute` re-checks the same service gate before every call, so a
        registered schema cannot authorize a provider that is unapproved, was
        revoked since registration, or has no credential.
        """
        return self._service() is not None

    # ---------------------------------------------------------------- dispatch
    @override
    async def execute(self, **kwargs: Any) -> str:
        adapter = self._adapter()
        credential = self._credential()
        if adapter is None:
            return _refusal(_NOT_CONFIGURED)
        if credential is None:
            return _refusal(_NO_CREDENTIAL)

        query = ""
        results: list[dict[str, str]] = []
        try:
            # Argument validation first: a refusal here costs nothing, reserves
            # nothing and touches no network.
            query = _bounded_query(adapter, kwargs.get("query"))
            count = _bounded_count(kwargs.get("num_results"))
            service = self._service()
            if service is None:
                return _refusal(_SERVICE_UNAVAILABLE)
            async with metered_tool_call(service, required_capability=SEARCH_CAPABILITY) as charge:
                payload = await self._dispatch(adapter, credential, query, count)
                results = _normalized_results(adapter, payload, count)
                units = _reported_units(adapter, payload)
                if units is not None and units > 1:
                    # Known usage is not an unknown outcome: record it fully.
                    # The ledger suspends an account if this exceeds its hold.
                    charge.confirm(units=units)
                    logger.warning(
                        "Search provider reported unexpected units",
                        extra={"search_provider": adapter.provider},
                    )
                    raise ComputeUnavailable(
                        "tool_price_exceeded", "Search provider reported unexpected billable units"
                    )
                else:
                    charge.confirm()
        except ComputeUnavailable:
            # Budget, capability, scope, approval and settlement refusals stop
            # the operation instead of becoming model-visible retry bait.
            raise
        except _SearchFailure as failure:
            logger.warning(
                "Search call failed",
                extra={
                    "search_provider": adapter.provider,
                    "search_failure": failure.category,
                },
            )
            return _refusal(failure.message)
        return json.dumps({"query": query, "results": results, "total_found": len(results)})

    async def _dispatch(
        self, adapter: _SearchAdapter, credential: str, query: str, count: int
    ) -> dict[str, Any]:
        """One bounded provider round trip, classified into sanitized failures.

        Never retried: a second attempt would be a second paid call this layer
        did not reserve. Cancellation propagates, so a cancelled search is
        settled conservatively by the reservation holder rather than reported as
        a provider timeout.
        """
        try:
            return await asyncio.wait_for(
                self._round_trip(adapter, credential, query, count),
                timeout=SEARCH_DEADLINE_S,
            )
        except _SearchFailure:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise _SearchFailure(_TIMEOUT, category="timeout") from None
        except httpx.TransportError:
            raise _SearchFailure(_UNREACHABLE, category="connection_failed") from None
        except httpx.HTTPError:
            raise _SearchFailure(_UNREACHABLE, category="http_error") from None
        except Exception:
            # Deliberately no exception text and no traceback: an HTTP client
            # message embeds the request URL, and the URL carries the query.
            raise _SearchFailure(_FAILED, category="unspecified") from None

    async def _round_trip(
        self, adapter: _SearchAdapter, credential: str, query: str, count: int
    ) -> dict[str, Any]:
        wire = adapter.build(adapter.endpoint, credential, query, count)
        async with httpx.AsyncClient(
            # No transport retries and no redirects: exactly one request to the
            # pinned endpoint, so the reserved cost covers what was sent.
            transport=self._transport or httpx.AsyncHTTPTransport(retries=0),
            follow_redirects=False,
            timeout=httpx.Timeout(SEARCH_DEADLINE_S),
        ) as client:
            request = client.build_request(
                wire.method,
                wire.url,
                headers=wire.headers,
                params=wire.params,
                json=wire.json_body,
            )
            response = await client.send(request, stream=True)
            try:
                if response.status_code != 200:
                    # A non-200 body is never read: it is provider text that may
                    # echo the query, and the status alone is enough to refuse.
                    message, category = _status_failure(response.status_code)
                    raise _SearchFailure(message, category=category)
                body = await _bounded_body(response)
            finally:
                await response.aclose()
        return _decoded_json(body)
