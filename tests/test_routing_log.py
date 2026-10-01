"""Server-side routing telemetry: allowlist, isolation and turn reconstruction."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from orchestrator import compute_runtime as runtime
from orchestrator import model_routing, routing_log
from orchestrator.entitlements import EntitlementService
from orchestrator.entitlements.models import ReservationStatus
from test_model_routing import (
    DEEPSEEK,
    FLASH,
    TrackedStream,
    accepted_pair,
    accepted_single,
    dispatch_fixture,
    named_route,
)

SECRET_TEXT = "PRIVATE-MESSAGE-CONTENT-7f3a"


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())

    def records(self) -> list[dict[str, Any]]:
        prefix = routing_log.PREFIX + " "
        return [json.loads(line[len(prefix) :]) for line in self.lines if line.startswith(prefix)]


@pytest.fixture
def collected() -> Iterator[_Collector]:
    collector = _Collector()
    logger = logging.getLogger(routing_log.LOGGER_NAME)
    logger.addHandler(collector)
    try:
        yield collector
    finally:
        logger.removeHandler(collector)


def _events(records: list[dict[str, Any]]) -> list[str]:
    return [record["event"] for record in records]


def test_unknown_events_and_fields_are_rejected() -> None:
    with pytest.raises(routing_log.RoutingLogError):
        routing_log.build_record("message", {})
    with pytest.raises(routing_log.RoutingLogError):
        routing_log.build_record("attempt", {"content": "hello"})
    with pytest.raises(routing_log.RoutingLogError):
        routing_log.build_record("attempt", {"model": {"nested": "value"}})
    with pytest.raises(routing_log.RoutingLogError):
        routing_log.build_record("candidates", {"exclusions": {"budget": "many"}})


def test_values_are_bounded_scalars() -> None:
    record = routing_log.build_record(
        "candidates",
        {
            "route_ids": ["r"] * (routing_log.MAX_LIST_LENGTH + 5),
            "profile": "x" * 1_000,
            "exclusions": {"budget": 2},
        },
    )
    assert len(record["route_ids"]) == routing_log.MAX_LIST_LENGTH  # type: ignore[arg-type]
    assert len(record["profile"]) == routing_log.MAX_STRING_LENGTH  # type: ignore[arg-type]
    assert record["exclusions"] == {"budget": 2}


def test_emit_never_raises_and_drops_invalid_records(collected: _Collector) -> None:
    routing_log.emit("attempt", content=SECRET_TEXT)
    routing_log.emit("no-such-event")
    assert collected.records() == []


def test_logger_owns_an_info_handler_and_does_not_propagate() -> None:
    logger = logging.getLogger(routing_log.LOGGER_NAME)
    assert logger.level == logging.INFO
    assert logger.propagate is False
    assert any(getattr(handler, "_daemon_routing", False) for handler in logger.handlers)
    # Re-configuring is idempotent: no duplicate handlers.
    routing_log._configure()  # pyright: ignore[reportPrivateUsage]
    owned = [h for h in logger.handlers if getattr(h, "_daemon_routing", False)]
    assert len(owned) == 1


def _reservations(count: int) -> list[Any]:
    return [SimpleNamespace(id=uuid.uuid4()) for _ in range(count)]


@pytest.mark.asyncio
async def test_preoutput_fallback_turn_can_be_reconstructed(
    monkeypatch: pytest.MonkeyPatch, collected: _Collector
) -> None:
    failed, successful = TrackedStream(fail_before_output=True), TrackedStream()

    async def send(**kwargs: Any) -> Any:
        return failed if kwargs["model"] == DEEPSEEK else successful

    routes = [named_route(DEEPSEEK, price=0), named_route(FLASH)]
    with dispatch_fixture(
        monkeypatch,
        routes,
        routing=accepted_pair(DEEPSEEK, FLASH),
        provider=AsyncMock(side_effect=send),
    ) as (service, _, scope):
        holds = _reservations(2)
        service.reserve = AsyncMock(side_effect=holds)
        service.reconcile_expired_reservations = AsyncMock(return_value=0)
        monkeypatch.setattr(runtime, "EntitlementService", lambda pool: service)
        async with runtime.account_compute(
            object(), scope.user_id, auto_route=True, request_id="req_test"
        ) as account:
            with model_routing.routing_context("routine"):
                stream = await runtime.guarded_completion(
                    messages=[{"role": "user", "content": SECRET_TEXT}], stream=True
                )
                assert len([chunk async for chunk in stream]) == 1

    records = collected.records()
    assert _events(records) == [
        "scope_open",
        "candidates",
        "attempt",
        "attempt_outcome",
        "settlement",
        "attempt",
        "attempt_outcome",
        "settlement",
        "scope_close",
    ]
    scope_ids = {record.get("scope_id") for record in records}
    assert scope_ids == {str(account.scope_id)}
    candidates = records[1]
    assert candidates["route_ids"] == [DEEPSEEK, FLASH]
    first_attempt, first_outcome, first_settlement = records[2], records[3], records[4]
    assert first_attempt["attempt_index"] == 0
    assert first_attempt["reservation_id"] == str(holds[0].id)
    assert first_outcome["outcome"] == "failed"
    assert first_outcome["next_action"] == "fallback"
    assert first_outcome["output_released"] is False
    assert first_settlement["reservation_id"] == str(holds[0].id)
    assert first_settlement["path"] == "stream_end"
    assert first_settlement["estimated"] is True
    assert records[5]["attempt_index"] == 1
    assert records[5]["reservation_id"] == str(holds[1].id)
    assert records[6]["outcome"] == "completed"
    close = records[-1]
    assert close["exit"] == "normal"
    assert close["request_id"] == "req_test"
    assert close["attempts"] == 2
    assert close["completions"] == 1
    joined = "\n".join(collected.lines)
    assert SECRET_TEXT not in joined
    assert "api_key" not in joined
    assert str(scope.user_id) not in joined


@pytest.mark.asyncio
async def test_attempt_records_requested_preset_and_sent_effort(
    monkeypatch: pytest.MonkeyPatch, collected: _Collector
) -> None:
    with dispatch_fixture(monkeypatch, [named_route(FLASH)], routing=accepted_single(FLASH)) as (
        service,
        _,
        _scope,
    ):
        service.reserve = AsyncMock(side_effect=_reservations(1))
        with model_routing.routing_context("routine"):
            await runtime.guarded_completion(
                messages=[{"role": "user", "content": "hello"}], reasoning_effort="high"
            )
    attempt = next(record for record in collected.records() if record["event"] == "attempt")
    assert attempt["requested_effort"] == "high"
    assert attempt["sent_effort"] == "high"
    assert "preset_effort" in attempt
    settlement = next(r for r in collected.records() if r["event"] == "settlement")
    assert settlement["path"] == "completed"


@pytest.mark.asyncio
async def test_denied_request_records_exclusion_reasons(
    monkeypatch: pytest.MonkeyPatch, collected: _Collector
) -> None:
    with dispatch_fixture(
        monkeypatch, [named_route(FLASH)], routing=accepted_single(FLASH), budget=0
    ) as (service, provider, _scope):
        with model_routing.routing_context("routine"):
            with pytest.raises(runtime.ComputeUnavailable):
                await runtime.guarded_completion(messages=[{"role": "user", "content": "hi"}])
        service.reserve.assert_not_awaited()
        provider.assert_not_awaited()
    candidates = next(r for r in collected.records() if r["event"] == "candidates")
    assert candidates["candidate_count"] == 0
    assert candidates["exclusions"] == {"budget": 1}


@pytest.mark.asyncio
async def test_cancelled_turn_records_cancellation_and_one_settlement(
    monkeypatch: pytest.MonkeyPatch, collected: _Collector
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    upstream = TrackedStream()

    async def slow_close() -> None:
        entered.set()
        await release.wait()

    upstream.aclose = AsyncMock(side_effect=slow_close)
    with dispatch_fixture(
        monkeypatch,
        [named_route(FLASH)],
        routing=accepted_single(FLASH),
        provider=AsyncMock(return_value=upstream),
    ) as (service, _, scope):
        service.reserve = AsyncMock(side_effect=_reservations(1))
        service.reconcile_expired_reservations = AsyncMock(return_value=0)
        monkeypatch.setattr(runtime, "EntitlementService", lambda pool: service)

        async def consume() -> None:
            async with runtime.account_compute(object(), scope.user_id, auto_route=True):
                stream = await runtime.guarded_completion(
                    messages=[{"role": "user", "content": "hello"}], stream=True
                )
                async for _ in stream:
                    pass

        task = asyncio.create_task(consume())
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
    records = collected.records()
    assert sum(1 for r in records if r["event"] == "settlement") == 1
    assert records[-1]["event"] == "scope_close"
    assert records[-1]["exit"] == "cancelled"


class _Connection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def fetch(self, *_args: Any) -> list[dict[str, Any]]:
        return self.rows


class _Pool:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    @asynccontextmanager
    async def acquire(self):  # type: ignore[no-untyped-def]
        yield _Connection(self.rows)


@pytest.mark.asyncio
async def test_expiry_recovery_settlement_is_recorded(collected: _Collector) -> None:
    reservation_id, scope_id = uuid.uuid4(), uuid.uuid4()
    pool = _Pool([{"id": reservation_id, "reserved_microusd": 500, "scope_id": scope_id}])
    service = EntitlementService(pool)  # type: ignore[arg-type]
    service.settle = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            applied=True,
            status=ReservationStatus.SETTLED,
            actual_microusd=500,
            overage_microusd=0,
        )
    )
    recovered = await service.reconcile_expired_reservations(
        uuid.uuid4(), before=service.now() - runtime.timedelta(seconds=1)
    )
    assert recovered == 1
    (record,) = collected.records()
    assert record["event"] == "settlement"
    assert record["path"] == "expiry_recovery"
    assert record["reservation_id"] == str(reservation_id)
    assert record["scope_id"] == str(scope_id)
    assert record["actual"] == 500
