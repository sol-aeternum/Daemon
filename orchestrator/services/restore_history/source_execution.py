"""Original-only source retrieval; the fixture transport never owns history or SQL."""

from __future__ import annotations

import hashlib
import hmac
import json
import inspect
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import httpx

from orchestrator.services.connection_cleanup import retain_pool_cleanup
from orchestrator.services.fetch.models import EXTRACTION_VERSION_V1
from orchestrator.services.fetch.service import FetchService
from orchestrator.services.web_snapshots import WebSnapshot, WebSnapshotStore
from orchestrator.tools.web_fetch import WebFetchTool
from orchestrator.tools.web_search import WebSearchTool, validate_research_search_result
from orchestrator.services.account_control.transaction import ObservationBlocked

from .model import (
    AcceptPrepare,
    Command,
    HistoryError,
    HistoryState,
    SendPossible,
    StopIntent,
    SourcePinPrepare,
    SourceReadPossible,
    _pairs,
    canonical,
)
from .terminal_projection import InboxOwnedIndexHistoryProjection, InboxSourcesHistoryProjection

if TYPE_CHECKING:
    from orchestrator.compute_runtime import ComputeScope, ToolServiceApproval
    from orchestrator.entitlements.models import ResolvedPolicy
    from orchestrator.entitlements.policy import RoutePolicy
    from orchestrator.config import Settings
    from orchestrator.tasks._history_binding import ComputeHistoryBinding
    from orchestrator.tasks.runner import AttemptState
    from .text_execution import HistorySourcesExecution


def source_key_material(key: bytes, history: UUID, *, owned_index: bool = False) -> bytes:
    """Only a fixture-specific derivative reaches the original source owner."""
    domain = (
        b"daemon.inbox.owned-uk-passport-20261128.key.v1\n"
        if owned_index
        else b"daemon.inbox.sources.key.v1\n"
    )
    return hmac.digest(key, domain + history.bytes, "sha256")


def source_mac(
    key: bytes,
    domain: str,
    history: UUID,
    acceptance: UUID,
    task: UUID,
    epoch: int,
    result: UUID,
    *fields: Any,
) -> str:
    material = hmac.digest(
        key, b"daemon.inbox.sources." + domain.encode() + b"\n" + history.bytes, "sha256"
    )
    values = (str(acceptance), str(task), epoch, str(result), *fields)
    framed = b"".join(len(raw).to_bytes(8, "big") + raw for raw in map(canonical, values))
    return hmac.new(material, framed, hashlib.sha256).hexdigest()


def passage_fields(value: dict[str, Any]) -> tuple[Any, ...]:
    return (
        value["url"],
        value["final_url"],
        EXTRACTION_VERSION_V1,
        value["retrieved_at"],
        value["snapshot_id"],
        value["start_char"],
        value["end_char"],
        value["content"],
    )


def validate_report(report: str, values: list[dict[str, Any]]) -> None:
    """Verify citations and verbatim excerpt offsets; the model writes the answer."""
    text, separator, evidence = report.partition("\n\nSources:\n\n")
    if not separator or not 1 <= len(values) <= 2:
        raise ValueError("source_report_refused")
    paragraphs = text.split("\n\n")
    references = set()
    for paragraph in paragraphs:
        claim = re.fullmatch(r"(.+) \[(1|2|1, 2)\]", paragraph)
        if not claim:
            raise ValueError("source_claim_refused")
        prose = claim[1]
        # This profile uses plain prose and numbered references; links and
        # verbatim quotations belong in the validated evidence section only.
        if (
            not prose.strip()
            or any(c in prose for c in '<>[]`"“”\\@')
            or re.search(r"(?:www\.|://)", prose, re.IGNORECASE)
        ):
            raise ValueError("source_claim_refused")
        references.update(int(n) for n in claim[2].split(", "))
    quotes = evidence.split("\n\n")
    if references != set(range(1, len(values) + 1)) or len(quotes) != len(values):
        raise ValueError("source_references_refused")
    for quote, value in zip(quotes, values, strict=True):
        citation = re.fullmatch(
            r"`([^`\r\n]+)` \[Source ([0-9a-f-]{36}) (0|[1-9]\d{0,6}):([1-9]\d{0,6})\]\(<([^<>\s]+)>\)",
            quote,
        )
        if not citation:
            raise ValueError("source_citation_refused")
        excerpt, snapshot, start, end, url = citation.groups()
        left, right = int(start), int(end)
        if (
            snapshot != value["snapshot_id"]
            or url != value["url"]
            or not value["start_char"] <= left < right <= value["end_char"]
            or excerpt != value["content"][left - value["start_char"] : right - value["start_char"]]
        ):
            raise ValueError("source_citation_refused")


def validate_trace(calls: Any, results: Any) -> list[dict[str, Any]]:
    if (
        type(calls) is not list
        or type(results) is not list
        or not 2 <= len(calls) == len(results) <= 3
    ):
        raise ValueError("source_trace_refused")
    if any(type(item) is not dict for item in (*calls, *results)):
        raise ValueError("source_trace_refused")
    first, search = calls[0], results[0]
    if (
        set(first) != {"name", "arguments"}
        or set(search)
        not in (
            {"name", "result", "outcome"},
            {"name", "result", "outcome", "source_receipt"},
        )
        or first["name"] != "web_search"
        or search["name"] != "web_search"
        or search["outcome"] != "succeeded"
    ):
        raise ValueError("source_trace_refused")
    validate_research_search_result(first["arguments"], search["result"])
    available = {item["url"] for item in search["result"]["results"]}
    values = []
    for call, result in zip(calls[1:], results[1:], strict=True):
        if set(call) != {"name", "arguments"} or set(result) != {"name", "result", "outcome"}:
            raise ValueError("source_trace_refused")
        value = result["result"]
        if (
            type(value) is not dict
            or set(value)
            != {
                "snapshot_id",
                "url",
                "final_url",
                "title",
                "retrieved_at",
                "expires_at",
                "content",
                "content_length",
                "total_chars",
                "start_char",
                "end_char",
                "next_start_char",
                "complete",
                "has_more",
            }
            or call["name"] != "web_fetch"
            or result["name"] != "web_fetch"
            or result["outcome"] != "succeeded"
            or call["arguments"] != {"url": value["url"]}
            or type(value["url"]) is not str
            or value["url"] not in available
            or any(
                value["url"] == prior["url"] or value["snapshot_id"] == prior["snapshot_id"]
                for prior in values
            )
            or value["url"] != value["final_url"]
            or type(value["content"]) is not str
            or type(value["start_char"]) is not int
            or type(value["end_char"]) is not int
            or type(value["total_chars"]) is not int
            or type(value["content_length"]) is not int
            or value["start_char"] != 0
            or value["end_char"] != len(value["content"])
            or not 1 <= value["end_char"] <= 1000
            or value["content_length"] != value["end_char"]
            or value["end_char"] != min(value["total_chars"], 1000)
            or value["complete"] is not (value["end_char"] == value["total_chars"])
            or value["has_more"] is not (value["end_char"] < value["total_chars"])
            or value["next_start_char"] != (value["end_char"] if value["has_more"] else None)
        ):
            raise ValueError("source_trace_refused")
        values.append(value)
    return values


class SourceRun:
    def __init__(
        self,
        owner: HistorySourcesExecution,
        original: AttemptState,
    ):
        assert owner.effects is not None
        self._owner, self._original = owner, original
        self._inputs = owner._source_inputs
        self.claim, self.effects = original.claim, owner.effects
        self.key, self.transport = owner.source_key, owner.source_transport
        self.search: str | None = None
        self.selected: list[str] = []
        self.call_ids: list[str] = []
        self.search_id: str | None = None
        self.search_call: dict[str, Any] | None = None
        self.selection_calls: list[dict[str, Any]] = []
        self.pins: list[tuple[UUID, SourcePinPrepare, dict[str, Any]]] = []
        self.pending: tuple[UUID, SourceReadPossible, str] | None = None
        self.transport_entered = False
        self.deadline = 0.0
        self.synthesis_checked = False
        self.report: str | None = None
        self.tool = SourceReadTool(self, owner.settings)
        self._original_tool = self.tool
        self._snapshot_store = self.tool._store
        self._fetch_service = self.tool._fetch_service

    @property
    def owned_index(self) -> bool:
        return type(self.effects._projection) is InboxOwnedIndexHistoryProjection

    def validate_binding(self, binding: ComputeHistoryBinding) -> None:
        """Only the retained original's exact collaborators constitute a binding."""
        from .owned_index import INDEX_ROUTE, OwnedIndexSearchTool
        from .text_execution import HistorySourcesExecution
        from orchestrator.compute_runtime import ToolServiceApproval

        owner, original, inputs = self._owner, self._original, self._inputs
        identity, tool = binding.identity, binding.research_tool
        if (
            type(owner) is not HistorySourcesExecution
            or owner._source_inputs is not inputs
            or not any(state is original for state in owner._original_attempts.values())
            or original.history_binding is not binding
            or binding.source_run is not self
            or original.claim is not self.claim
            or binding.effects is not self.effects
            or owner.effects is not self.effects
            or owner.projection is not self.effects._projection
            or owner.worker is not binding.composition
            or binding.research_model_transport is not inputs.model_transport
            or not inspect.iscoroutinefunction(inputs.model_transport)
            or tool is not inputs.search_tool
            or self.key is not inputs.source_key
            or self.transport is not inputs.source_transport
            or type(self.transport) is not httpx.MockTransport
            or self.transport.handler is not inputs.source_handler
            or self.tool is not self._original_tool
            or self.tool.run is not self
            or self.tool._store is not self._snapshot_store
            or self.tool._fetch_service is not self._fetch_service
            or self.tool._user_id != self.claim.user_id
            or self.tool._conversation_id != self.claim.conversation_id
            or identity.account_id != self.claim.user_id
            or identity.generation != self.claim.origin.generation
            or identity.task_id != self.claim.task_id
            or identity.epoch != self.claim.epoch
        ):
            raise ObservationBlocked("history_binding_unavailable")
        if self.owned_index:
            if (
                type(tool) is not OwnedIndexSearchTool
                or tool._projection is not self.effects._projection
                or tool._approval is not inputs.search_approval
                or tool._approval
                != ToolServiceApproval(INDEX_ROUTE, "web_search", "owned", "call", 0, 0)
            ):
                raise ObservationBlocked("history_binding_unavailable")
        elif (
            type(self.effects._projection) is not InboxSourcesHistoryProjection
            or type(tool) is not WebSearchTool
            or type(tool._transport) is not httpx.MockTransport
            or tool._transport is not inputs.search_transport
            or tool._pacer is not inputs.search_pacer
            or tool._pacer is None
            or tool.provider != "brave"
            or inputs.search_approval is not None
        ):
            raise ObservationBlocked("history_binding_unavailable")

    def owned_capability(self) -> SourceRun | None:
        """Expose the owned-only policy through the validated original scope."""
        self.scope()
        return self if self.owned_index else None

    def owns_search_approval(self, tool: Any, approval: ToolServiceApproval) -> bool:
        return (
            self.owned_index
            and tool is self._inputs.search_tool
            and approval is self._inputs.search_approval
        )

    def pin_completion(self, route_id: str | None, params: dict[str, Any]) -> str:
        from .owned_index import MAX_OUTPUT, MODEL, MODEL_ROUTE
        from orchestrator.compute_runtime import ComputeUnavailable

        if route_id not in (None, MODEL_ROUTE) or not params.get("stream"):
            raise ComputeUnavailable("route_unavailable", "Approved inference route unavailable")
        params["model"] = MODEL
        params["max_tokens"] = MAX_OUTPUT
        return MODEL_ROUTE

    async def require_budget(self, scope: ComputeScope, policy: ResolvedPolicy) -> None:
        from .owned_index import MAX_HOLD
        from orchestrator.compute_runtime import ComputeUnavailable

        binding = scope.history_binding
        if (
            self.scope() is not scope
            or binding is None
            or scope.profile != "routine"
            or scope.operation != "chat"
            or scope.extended
            or scope.background
            or scope.account_allow_premium
            or scope.outstanding
            or scope.streams
            or scope.retirement_holds is None
            or scope.completion_seq not in (0, 1, 2)
            or (scope.expected_period is not None and scope.expected_period != policy.period_key)
            or await binding.composition.pool.fetchval(
                "SELECT EXISTS(SELECT 1 FROM public.entitlement_reservations "
                "WHERE user_id=$1 AND status='open')",
                scope.user_id,
            )
            is not False
        ):
            raise ComputeUnavailable("account_unavailable", "Owned-index account unavailable")
        remaining_hold = MAX_HOLD * (3 - scope.completion_seq)
        if remaining_hold > min(
            policy.remaining_for(False),
            250_000 - policy.period_spent_microusd - policy.period_reserved_microusd,
        ):
            raise ComputeUnavailable("budget_exceeded", "Owned-index budget unavailable")
        self.scope()
        scope.expected_period = policy.period_key

    def require_input_bound(self, bound: int) -> None:
        from .owned_index import MAX_INPUT_BOUND
        from orchestrator.compute_runtime import ComputeUnavailable

        if bound > MAX_INPUT_BOUND:
            raise ComputeUnavailable("context_limit", "Request exceeds the context limit")

    def require_route(self, bound: int, output: int, route: RoutePolicy) -> None:
        from .owned_index import MAX_HOLD, MAX_OUTPUT
        from orchestrator.compute_runtime import ComputeUnavailable

        if (
            bound > MAX_HOLD
            or output != MAX_OUTPUT
            or route.price_ceiling is None
            or route.price_ceiling.microusd_per_1m_prompt != 300_000
            or route.price_ceiling.microusd_per_1m_completion != 1_200_000
            or route.endpoint != "https://openrouter.ai/api/v1"
            or route.route_class != "routine"
            or route.transport.provider_only != ("coreweave/fp8",)
            or route.transport.provider_order != ("coreweave/fp8",)
            or route.transport.allow_fallbacks is not False
            or route.transport.multi_provider
        ):
            raise ComputeUnavailable("route_unavailable", "Approved inference route unavailable")

    def scope(self) -> ComputeScope:
        from orchestrator.compute_runtime import current_scope

        scope = current_scope()
        binding = scope.history_binding
        if binding is None or binding.source_run is not self or binding.effects is not self.effects:
            raise HistoryError("source_scope_refused")
        scope.require_inference_effect()
        binding.state.require_send()
        return scope

    def mac(self, domain: str, acceptance: UUID, *fields: Any) -> str:
        return source_mac(
            self.key,
            domain,
            self.effects._projection.expected.history_id,
            acceptance,
            self.claim.task_id,
            self.claim.epoch,
            self.claim.result_message_id,
            *fields,
        )

    def accept_search(self, call: dict[str, Any], result: str) -> None:
        validate_research_search_result(
            json.loads(call["function"]["arguments"]), json.loads(result)
        )
        if self.owned_index:
            from .owned_index import validate_lookup

            validate_lookup(json.loads(call["function"]["arguments"]), json.loads(result))
        self.search = result
        self.search_id = call["id"]
        self.search_call = json.loads(canonical(call))

    def select(self, calls: list[dict[str, Any]]) -> None:
        self.scope()
        if self.search is None or self.selected or not 1 <= len(calls) <= 2:
            raise HistoryError("source_selection_refused")
        available = {item["url"] for item in json.loads(self.search)["results"]}
        urls, ids = [], []
        service = self.tool._fetch_service
        assert service is not None
        for call in calls:
            args = json.loads(call["function"]["arguments"], object_pairs_hook=_pairs)
            url = args.get("url") if type(args) is dict else None
            ident = call["id"]
            if (
                type(args) is not dict
                or set(args) != {"url"}
                or type(url) is not str
                or url not in available
                or url in urls
                or str(httpx.URL(url)) != url
                or httpx.URL(url).fragment
                or not service._is_supported_url(url)
                or service._is_blocked_domain(url)
                or service._is_youtube_url(url)
                or call["function"]["name"] != "web_fetch"
                or type(ident) is not str
                or not 1 <= len(ident) <= 128
                or ident == self.search_id
                or ident in ids
            ):
                raise HistoryError("source_selection_refused")
            urls.append(url)
            ids.append(ident)
        if self.owned_index:
            from .owned_index import URLS

            if tuple(urls) != URLS:
                raise HistoryError("source_selection_refused")
        self.selected, self.call_ids = urls, ids
        self.selection_calls = json.loads(canonical(calls))

    def verify_context(self, messages: list[dict[str, Any]], *, final: bool) -> None:
        from orchestrator.tools.completion import _unwrap_tool_result

        expected = [(self.search_id, "web_search", self.search)]
        if self.search_call is None:
            raise HistoryError("source_context_refused")
        expected_calls = [[self.search_call]]
        if final:
            if len(self.pins) != len(self.selected) or not self.pins or self.pending is not None:
                raise HistoryError("source_context_refused")
            expected.extend(
                (ident, "web_fetch", json.dumps(value, ensure_ascii=False))
                for ident, (_, _, value) in zip(self.call_ids, self.pins, strict=True)
            )
            expected_calls.append(self.selection_calls)
        actual = [
            (m.get("tool_call_id"), m.get("name"), _unwrap_tool_result(m["content"]))
            for m in messages
            if m.get("role") == "tool"
        ]
        actual_calls = [
            m["tool_calls"]
            for m in messages
            if m.get("role") == "assistant" and m.get("tool_calls")
        ]
        if actual != expected or actual_calls != expected_calls:
            raise HistoryError("source_context_refused")
        if final:
            self.synthesis_checked = True

    def validate_report(self, content: str, finish: dict[str, Any]) -> None:
        if (
            not self.synthesis_checked
            or finish.get("state") != "confirmed"
            or finish.get("reason") != "stop"
        ):
            raise HistoryError("source_report_refused")
        validate_report(content, [value for _, _, value in self.pins])
        self.report = content

    async def terminal_receipt(
        self, conn: Any, fields: dict[str, Any], content: str, history: HistoryState
    ) -> None:
        from orchestrator.compute_runtime import current_scope
        from orchestrator.entitlements.models import ReservationStatus

        from orchestrator.tools.web_search import BRAVE_FIXED_MICROUSD

        scope = current_scope()
        binding = scope.history_binding
        if binding is None or binding.source_run is not self or binding.effects is not self.effects:
            raise HistoryError("source_terminal_refused")
        values = validate_trace(fields["tool_calls"], fields["tool_results"])
        if (
            content != self.report
            or not self.synthesis_checked
            or values != [value for _, _, value in self.pins]
            or fields["tool_results"][0]["result"] != json.loads(self.search or "")
            or len(scope.settled) != 4
            or scope.outstanding
            or scope.streams
            or len(scope.retirement_holds or ()) != 4
        ):
            raise HistoryError("source_terminal_refused")
        sends = [
            e
            for e in history.entries
            if isinstance(e.command.data, SendPossible)
            and e.command.data.task_id == self.claim.task_id
        ]
        owned = self.effects._owned_sends.get(id(binding.state))
        if (
            len(sends) != 4
            or owned is None
            or owned[0] is not binding.state
            or owned[1] != {e.command.record_id for e in sends}
        ):
            raise HistoryError("source_terminal_refused")
        holds = scope.retirement_holds
        assert holds is not None
        rows = await conn.fetch(
            "SELECT id,user_id,scope_id,provider,route_id,model,status,reserved_microusd,actual_microusd,overage_microusd,period_key,usage "
            "FROM public.entitlement_reservations WHERE scope_id=$1",
            binding.identity.financial_scope_id,
        )
        by_id = {r["id"]: r for r in rows}
        if set(by_id) != {
            e.command.data.reservation_id for e in sends if isinstance(e.command.data, SendPossible)
        }:
            raise HistoryError("source_terminal_refused")
        for hold, entry in zip(holds, sends, strict=True):
            sent = entry.command.data
            assert isinstance(sent, SendPossible)
            if hold.settlement is None or not hold.settlement.done() or hold.settlement.cancelled():
                raise HistoryError("source_terminal_refused")
            settlement = hold.settlement.result()
            row = by_id[sent.reservation_id]
            if self.owned_index:
                from .owned_index import validate_reservation

                validate_reservation(row, sent.effect_kind)
            if (
                not settlement.applied
                or settlement.status is not ReservationStatus.SETTLED
                or settlement.overage_microusd
                or settlement.reservation.id != sent.reservation_id
                or settlement.reservation.id != hold.reservation.id
                or settlement.reservation.user_id != self.claim.user_id
                or settlement.period_key != hold.reservation.period_key
                or scope.settled.get(sent.reservation_id) != settlement.actual_microusd
                or row["user_id"] != self.claim.user_id
                or row["status"] != "settled"
                or row["overage_microusd"] != 0
                or row["actual_microusd"] != settlement.actual_microusd
                or row["period_key"] != settlement.period_key
                or not 0 <= row["actual_microusd"] <= row["reserved_microusd"]
                or (
                    sent.effect_kind == "web_search"
                    and (
                        (
                            not self.owned_index
                            and (
                                (row["provider"], row["route_id"], row["model"])
                                != ("brave", "brave-web-search", None)
                                or settlement.actual_microusd != BRAVE_FIXED_MICROUSD
                            )
                        )
                        or hold.stream is not None
                    )
                )
                or (
                    sent.effect_kind == "completion"
                    and (
                        row["provider"] != "openrouter"
                        or not row["route_id"]
                        or not row["model"]
                        or hold.stream is None
                        or hold.stream.state != "eof"
                        or hold.stream.cleanup_blocked
                        or hold.stream.cleanup_outcome != "close_returned"
                    )
                )
            ):
                raise HistoryError("source_terminal_refused")
        entries = [
            e
            for e in history.entries
            if isinstance(e.command.data, SourcePinPrepare)
            and e.command.data.task_id == self.claim.task_id
        ]
        if [(e.command.record_id, e.command.data) for e in entries] != [
            (ident, pin) for ident, pin, _ in self.pins
        ]:
            raise HistoryError("source_terminal_refused")
        receipt = self.mac(
            "report",
            self.pins[0][1].acceptance_record_id,
            content,
            [[str(ident), pin.passage_commitment] for ident, pin, _ in self.pins],
            fields["tool_calls"],
            fields["tool_results"],
        )
        fields["tool_results"] = [
            dict(fields["tool_results"][0], source_receipt=receipt),
            *fields["tool_results"][1:],
        ]

    async def mark(self, url: str) -> None:
        scope = self.scope()
        ordinal = len(self.pins) + 1
        if (
            self.pending is not None
            or ordinal > len(self.selected)
            or self.selected[ordinal - 1] != url
        ):
            raise HistoryError("source_read_refused")
        self.deadline = time.monotonic() + 15
        self.transport_entered = False
        effects = self.effects
        projection = effects._projection
        assert isinstance(projection, InboxSourcesHistoryProjection)

        async def prepare(limit):
            async with (
                effects._history.operation(deadline=limit) as operation,
                retain_pool_cleanup(),
            ):
                history = await effects._parity(operation, limit)
                entries = [
                    e
                    for e in history.entries
                    if getattr(e.command.data, "task_id", None) == self.claim.task_id
                ]
                accepted = next(e for e in entries if isinstance(e.command.data, AcceptPrepare))
                sends = [e for e in entries if isinstance(e.command.data, SendPossible)]
                owned = effects._owned_sends.get(id(scope.inference_invocation))
                if (
                    len(sends) != 3
                    or owned is None
                    or set(e.command.record_id for e in sends) != owned[1]
                ):
                    raise HistoryError("source_read_refused")
                data = SourceReadPossible(
                    self.claim.user_id,
                    self.claim.origin.generation,
                    self.claim.task_id,
                    self.claim.epoch,
                    accepted.command.record_id,
                    ordinal,
                    len(self.selected),
                    sends[1].command.record_id,
                    sends[2].command.record_id,
                    self.mac(
                        "selection",
                        accepted.command.record_id,
                        json.loads(self.search or ""),
                        url,
                        "article",
                        EXTRACTION_VERSION_V1,
                    ),
                )
                command = Command(uuid4(), data)
                async with effects._tasks._worker(
                    self.claim.task_id, self.claim.epoch, self.claim
                ) as (conn, _):
                    prepared = await operation.append(command)
                    if (
                        not prepared.created
                        or not (
                            await projection.stage_source(conn, prepared.entry, deadline=limit)
                        ).inserted
                    ):
                        raise HistoryError("source_read_refused")
                return command.record_id, data, url

        self.pending = await effects._run(prepare, self.deadline)
        self.scope()

    async def dispatch(self, request: httpx.Request) -> httpx.Response:
        scope = self.scope()
        if (
            self.pending is None
            or self.transport_entered
            or time.monotonic() >= self.deadline
            or request.method != "GET"
            or str(request.url) != self.pending[2]
            or "cookie" in request.headers
            or "authorization" in request.headers
        ):
            raise HistoryError("source_transport_refused")
        pending = self.pending
        binding = scope.history_binding
        assert binding is not None

        async def recheck(limit: float) -> bool:
            effects = self.effects
            async with (
                effects._history.operation(deadline=limit) as operation,
                retain_pool_cleanup(),
            ):
                history = await effects._parity(operation, limit)
                if any(
                    isinstance(e.command.data, StopIntent)
                    and e.command.data.task_id == self.claim.task_id
                    for e in history.entries
                ):
                    return False
                if history.entries[-1].command != Command(pending[0], pending[1]):
                    raise HistoryError("source_transport_refused")
                effects._worker_history(history, self.claim)
                async with effects._tasks._worker(
                    self.claim.task_id, self.claim.epoch, self.claim
                ) as (conn, _):
                    await binding.recovery.require_open_in_transaction(conn, binding.identity)
                return True

        admitted = await self.effects._run(recheck, self.deadline)
        self.scope()
        # No await between this process-local negative latch, permit consumption
        # and entry to the exact transport. A concurrently staged Stop owns H
        # and sets the latch before awaiting either durability domain.
        if (
            not admitted
            or self.claim.task_id in self.effects._source_stops
            or self.pending != pending
            or self.transport_entered
            or time.monotonic() >= self.deadline
        ):
            raise HistoryError("source_transport_stopped")
        self.transport_entered = True
        response = await self.transport.handle_async_request(request)
        if self.owned_index:
            from .owned_index import validate_capture

            if response.status_code != 200:
                raise HistoryError("owned_index_capture_refused")
            validate_capture(str(request.url), response.content)
        return response

    async def publish(
        self,
        store: WebSnapshotStore,
        native_create: Callable[..., Awaitable[WebSnapshot]],
        user: UUID,
        conversation: UUID,
        **kwargs: Any,
    ) -> WebSnapshot:
        self.scope()
        if self.pending is None or not self.transport_entered:
            raise HistoryError("source_pin_refused")
        possible_id, possible, url = self.pending
        content = kwargs["content"]
        passage = content[:1000]
        if not passage or kwargs["source_url"] != url or kwargs["final_url"] != url:
            raise HistoryError("source_pin_refused")
        if self.owned_index:
            from .owned_index import validate_passage

            validate_passage(possible.ordinal, url, passage)
        now = datetime.now(timezone.utc)
        effects = self.effects
        projection = effects._projection
        assert isinstance(projection, InboxSourcesHistoryProjection)

        async def stage(limit):
            async with (
                effects._history.operation(deadline=limit) as operation,
                retain_pool_cleanup(),
            ):
                history = await effects._parity(operation, limit)
                if history.entries[-1].command != Command(possible_id, possible):
                    raise HistoryError("source_pin_refused")
                staged = []

                async def hook(conn, snapshot_id):
                    self.scope()
                    row = await conn.fetchrow(
                        "SELECT * FROM ONLY public.web_snapshots WHERE id=$1 AND user_id=$2 AND conversation_id=$3 AND expires_at>clock_timestamp()",
                        snapshot_id,
                        user,
                        conversation,
                    )
                    if row is None:
                        raise HistoryError("source_pin_refused")
                    snapshot = store._snapshot_from_row(row)
                    if (
                        snapshot.user_id != user
                        or snapshot.conversation_id != conversation
                        or snapshot.content != content
                        or snapshot.source_url != url
                        or snapshot.final_url != url
                        or snapshot.retrieved_at != now
                        or snapshot.extract_mode != "article"
                        or snapshot.extraction_version != EXTRACTION_VERSION_V1
                    ):
                        raise HistoryError("source_pin_refused")
                    value = json.loads(self.tool._section(snapshot, 0, 1000))
                    if (
                        value.get("content") != passage
                        or value.get("start_char") != 0
                        or value.get("end_char") != len(passage)
                    ):
                        raise HistoryError("source_pin_refused")
                    key = self.mac(
                        "selection",
                        possible.acceptance_record_id,
                        json.loads(self.search or ""),
                        url,
                        "article",
                        EXTRACTION_VERSION_V1,
                    )
                    await effects._tasks.pin_page(conn, self.claim, key, snapshot_id)
                    event = await conn.fetchrow(
                        "SELECT e.seq,e.payload_ciphertext FROM task_events e JOIN tasks t ON t.id=e.task_id AND t.event_seq=e.seq WHERE e.task_id=$1 AND e.kind='page_refreshed'",
                        self.claim.task_id,
                    )
                    if effects._tasks._open(event["payload_ciphertext"]) != {
                        "key": key,
                        "snapshot_id": str(snapshot_id),
                        "epoch": self.claim.epoch,
                    }:
                        raise HistoryError("source_pin_refused")
                    data = SourcePinPrepare(
                        possible.account_id,
                        possible.account_generation,
                        possible.task_id,
                        possible.epoch,
                        possible.acceptance_record_id,
                        possible.ordinal,
                        possible_id,
                        snapshot_id,
                        event["seq"],
                        self.mac("passage", possible.acceptance_record_id, *passage_fields(value)),
                    )
                    command = Command(uuid4(), data)
                    prepared = await operation.append(command)
                    if (
                        not prepared.created
                        or not (
                            await projection.stage_source(conn, prepared.entry, deadline=limit)
                        ).inserted
                    ):
                        raise HistoryError("source_pin_refused")
                    self.scope()
                    staged.append((command.record_id, data, value))

                snapshot = await native_create(
                    user, conversation, **kwargs, now=now, on_created=hook, _retain_connection=True
                )
                if len(staged) != 1:
                    raise HistoryError("source_pin_refused")
                return snapshot, staged[0]

        snapshot, pin = await effects._run(stage, self.deadline)
        self.pins.append(pin)
        self.pending = None
        return snapshot


class SourceSnapshotStore(WebSnapshotStore):
    def __init__(self, run: SourceRun, settings: Settings):
        self.run = run
        super().__init__(run.effects._tasks._pool, run.effects._tasks._enc, settings)

    async def create(self, user_id: UUID, conversation_id: UUID, **kwargs: Any) -> WebSnapshot:
        if kwargs.pop("on_created", None) is not None:
            raise HistoryError("source_pin_refused")
        return await self.run.publish(self, super().create, user_id, conversation_id, **kwargs)


class SourceReadTool(WebFetchTool):
    description = (
        "Read a selected search URL once, retaining the first 1000 characters of the article."
    )
    parameters = {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
        "additionalProperties": False,
    }

    def __init__(self, run: SourceRun, settings: Settings):
        self.run = run
        super().__init__(
            SourceSnapshotStore(run, settings), run.claim.user_id, run.claim.conversation_id
        )
        self._fetch_service = FetchService(_source_transport=httpx.MockTransport(run.dispatch))

    async def execute(self, **kwargs: Any) -> str:
        if set(kwargs) != {"url"}:
            raise HistoryError("source_read_refused")
        await self.run.mark(kwargs["url"])
        value = await super().execute(url=kwargs["url"], force_refresh=True, max_chars=1000)
        if self.run.pending is not None or json.loads(value) != self.run.pins[-1][2]:
            raise HistoryError("source_read_unavailable")
        return value
