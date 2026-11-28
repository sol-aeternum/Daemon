"""Exact disposable public-capture index. No network, configurable provider or fallback."""

from __future__ import annotations

import hashlib
import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Final

import httpx

from orchestrator.compute_runtime import ToolServiceApproval, metered_tool_call
from orchestrator.tools.registry import Tool
from orchestrator.tools.web_search import WebSearchTool, research_search_arguments

from .model import HistoryError
from .terminal_projection import InboxOwnedIndexHistoryProjection

INDEX_ROUTE: Final = "owned-uk-passport-20261128-v1"
MODEL_ROUTE: Final = "deepseek-flash-coreweave-fp8"
MODEL: Final = "openrouter/deepseek/deepseek-v4.1-flash-1017"
MAX_INPUT_BOUND: Final = 32_000
MAX_OUTPUT: Final = 4_000
MAX_HOLD: Final = 14_400


@dataclass(frozen=True, slots=True)
class IndexEntry:
    url: str
    title: str
    description: str
    capture_sha: str
    passage_sha: str


MANIFEST: Final = (
    IndexEntry(
        "https://www.gov.uk/passport-fees",
        "Passport fees - GOV.UK",
        "Official passport fees page.",
        "fe49432932de1e8046d785a8e3b2322a1e2503a1e8db03c25967dc64c33ded84",
        "97996fd316f3c246a2cb14523e746cbabf7579b929a573513ee1a13fcba4c2c5",
    ),
    IndexEntry(
        "https://www.gov.uk/government/organisations/hm-passport-office/about/about-our-services",
        "About our services - HM Passport Office - GOV.UK",
        "Official passport service information page.",
        "d05bc92c316294b69f8ff36c3bbe2881f48074aca51467f05cd418b45c2d31c6",
        "eddc1ce8f75a1ca20f326a5ef87218e3759ac0ae42727288554988a3f58a0ef5",
    ),
)
URLS: Final = tuple(entry.url for entry in MANIFEST)


def index_result(arguments: Any) -> dict[str, Any]:
    query, count = research_search_arguments(arguments)
    if "passport" not in query.casefold() or count < len(MANIFEST):
        raise ValueError("owned_index_query_refused")
    return {
        "query": query,
        "results": [
            {"url": entry.url, "title": entry.title, "description": entry.description}
            for entry in MANIFEST
        ],
        "total_found": len(MANIFEST),
    }


def validate_lookup(arguments: Any, result: Any) -> None:
    if result != index_result(arguments):
        raise ValueError("owned_index_result_refused")


def validate_passage(ordinal: int, url: str, passage: str) -> None:
    if not 1 <= ordinal <= len(MANIFEST):
        raise ValueError("owned_index_passage_refused")
    entry = MANIFEST[ordinal - 1]
    if (
        url != entry.url
        or len(passage) != 1000
        or hashlib.sha256(passage.encode("utf-8")).hexdigest() != entry.passage_sha
    ):
        raise ValueError("owned_index_passage_refused")


def validate_reservation(row: Any, effect_kind: str) -> None:
    identity = (row["provider"], row["route_id"], row["model"])
    usage = json.loads(row["usage"]) if type(row["usage"]) is str else row["usage"]
    if (
        type(usage) is not dict
        or usage.get("estimated_cost")
        or usage.get("recovered_after_timeout")
    ):
        raise ValueError("owned_index_usage_refused")
    if effect_kind == "web_search":
        if (
            identity != ("owned", INDEX_ROUTE, None)
            or row["reserved_microusd"] != 0
            or row["actual_microusd"] != 0
            or usage != {"tool_calls": 1}
        ):
            raise ValueError("owned_index_reservation_refused")
    else:
        if (
            identity != ("openrouter", MODEL_ROUTE, MODEL)
            or not 0 <= row["actual_microusd"] <= row["reserved_microusd"] <= MAX_HOLD
        ):
            raise ValueError("owned_index_reservation_refused")
        prompt, completion = usage.get("input_tokens"), usage.get("output_tokens")
        if (
            set(usage) != {"input_tokens", "output_tokens"}
            or type(prompt) is not int
            or type(completion) is not int
            or not 0 < prompt <= MAX_INPUT_BOUND
            or not 0 < completion <= MAX_OUTPUT
            or row["actual_microusd"]
            != (prompt * 300_000 + completion * 1_200_000 + 999_999) // 1_000_000
        ):
            raise ValueError("owned_index_usage_refused")


def validate_capture(url: str, body: bytes) -> None:
    if url not in URLS or type(body) is not bytes:
        raise HistoryError("owned_index_capture_refused")
    if hashlib.sha256(body).hexdigest() != MANIFEST[URLS.index(url)].capture_sha:
        raise HistoryError("owned_index_capture_refused")


@dataclass(frozen=True, slots=True)
class OwnedIndexCorpus:
    captures: tuple[bytes, bytes]

    def __post_init__(self) -> None:
        if type(self.captures) is not tuple or len(self.captures) != len(MANIFEST):
            raise HistoryError("owned_index_capture_refused")
        for url, body in zip(URLS, self.captures, strict=True):
            validate_capture(url, body)

    async def respond(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method != "GET" or url not in URLS:
            raise HistoryError("owned_index_capture_refused")
        body = self.captures[URLS.index(url)]
        validate_capture(url, body)
        return httpx.Response(
            200, content=body, headers={"Content-Type": "text/html; charset=utf-8"}
        )


class OwnedIndexSearchTool(Tool):
    """Private native search tool; the only zero-priced approval owner."""

    name = "web_search"
    description = "Look up titles and URLs in the disposable, owned two-entry passport index."
    parameters = WebSearchTool.parameters

    def __init__(self, projection: InboxOwnedIndexHistoryProjection) -> None:
        if type(projection) is not InboxOwnedIndexHistoryProjection:
            raise HistoryError("owned_index_refused")
        self._projection = projection
        self._approval = ToolServiceApproval(INDEX_ROUTE, self.name, "owned", "call", 0, 0)

    async def _lookup(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return index_result(arguments)

    async def execute(self, **kwargs: Any) -> str:
        arguments = {k: v for k, v in kwargs.items() if v is not None}
        # Refuse malformed requests before acquiring the native hold.
        expected = index_result(arguments)
        deadline = time.monotonic() + 15
        async with metered_tool_call(
            self._approval,
            required_capability="web_research",
            dispatch_aware=True,
            work_deadline=deadline,
            history_tool=self,
        ) as charge:
            if not await charge.prepare_history_dispatch(deadline=deadline):
                raise HistoryError("owned_index_dispatch_refused")
            charge.mark_dispatched()
            async with asyncio.timeout(max(0.0, deadline - time.monotonic())):
                value = await self._lookup(arguments)
            if time.monotonic() >= deadline or value != expected:
                raise HistoryError("owned_index_lookup_refused")
            charge.confirm()
        return json.dumps(value, ensure_ascii=False)
