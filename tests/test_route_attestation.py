"""Monitored inference route approvals and their ZDR attestation (no network, no DB)."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from orchestrator.entitlements import attestation
from orchestrator.entitlements.errors import PolicyError
from orchestrator.entitlements.policy import RoutePolicy, parse_inference_policy

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
MIGRATION = ROOT / "migrations" / "043_inference_route_attestations.sql"
ROLLBACK = ROOT / "migrations" / "rollback" / "043_inference_route_attestations.down.sql"


def _production() -> dict[str, Any]:
    return json.loads((ROOT / "config" / "inference_policy.production.json").read_text())


def _monitored_policy(
    *, retains: bool = False, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The production Luna route, converted to a monitored approval."""
    doc = _production()
    route = copy.deepcopy(next(r for r in doc["routes"] if r["route_id"] == "luna-azure-eu"))
    route.pop("approval_expires_at", None)
    route["operator_review"].pop("review_expires_at", None)
    route["operator_review"]["reviewed_at"] = "2026-10-03T00:00:00Z"
    route["approval_mode"] = "monitored"
    route["zdr_baseline"] = {
        "provider_slug": "azure",
        "data_policy": {"training": False, "retainsPrompts": retains},
    }
    route.update(extra or {})
    doc["routes"] = [route]
    doc["default_route_id"] = None
    return doc


def _route(**kwargs: Any) -> RoutePolicy:
    return parse_inference_policy(_monitored_policy(**kwargs)).routes["luna-azure-eu"]


def _zdr(*entries: tuple[str, str]) -> dict[str, Any]:
    return {"data": [{"model_id": model, "tag": tag} for model, tag in entries]}


def _providers(**policy: Any) -> dict[str, Any]:
    base = {"training": False, "retainsPrompts": False}
    base.update(policy)
    return {"data": [{"slug": "azure", "dataPolicy": base}, {"slug": "other", "dataPolicy": {}}]}


LISTED = _zdr(("openai/gpt-6-luna", "azure/eu"))


@pytest.fixture(autouse=True)
def _reset_snapshot() -> Iterator[None]:
    previous = attestation.snapshot()
    yield
    attestation.set_snapshot(previous)


def _attest(route: RoutePolicy, *, at: datetime = NOW, revoked: bool = False) -> None:
    key = (route.route_id, attestation.baseline_fingerprint(route))
    attestation.set_snapshot(
        attestation.AttestationSnapshot(
            loaded=True,
            attested_at={key: at},
            revoked=frozenset({key}) if revoked else frozenset(),
        )
    )


# --------------------------------------------------------------------------- policy


def test_monitored_route_has_no_calendar_expiry_but_needs_a_current_attestation() -> None:
    route = _route()
    policy = parse_inference_policy(_monitored_policy())
    attestation.set_snapshot(attestation.AttestationSnapshot())
    assert route.rejection_reasons(policy.requirements, now=NOW) == ("zdr_attestation_unknown",)
    _attest(route)
    far_future = NOW + timedelta(days=365)
    _attest(route, at=far_future)
    assert route.is_approved(policy.requirements, now=far_future)
    assert "approval_expired" not in route.rejection_reasons(policy.requirements, now=far_future)


def test_stale_attestation_fails_closed_after_72_hours() -> None:
    route = _route()
    policy = parse_inference_policy(_monitored_policy())
    _attest(route, at=NOW)
    assert route.is_approved(policy.requirements, now=NOW + timedelta(hours=71))
    assert route.rejection_reasons(policy.requirements, now=NOW + timedelta(hours=73)) == (
        "zdr_attestation_stale",
    )


def test_revocation_is_sticky_for_the_approved_baseline() -> None:
    route = _route()
    policy = parse_inference_policy(_monitored_policy())
    _attest(route, revoked=True)
    assert route.rejection_reasons(policy.requirements, now=NOW) == ("zdr_attestation_revoked",)
    # Re-approval is a new baseline (here, a new review date), never automatic.
    reapproved = parse_inference_policy(
        _monitored_policy(
            extra={
                "operator_review": {
                    **_monitored_policy()["routes"][0]["operator_review"],
                    "reviewed_at": "2026-10-04T00:00:00Z",
                }
            }
        )
    ).routes["luna-azure-eu"]
    assert attestation.baseline_fingerprint(reapproved) != attestation.baseline_fingerprint(route)


def test_monitored_review_still_needs_a_dated_named_sign_off() -> None:
    route = _route()
    policy = parse_inference_policy(_monitored_policy())
    _attest(route)
    before_review = datetime(2026, 10, 2, tzinfo=timezone.utc)
    assert "operator_review_date_invalid" in route.rejection_reasons(
        policy.requirements, now=before_review
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"approval_expires_at": "2026-10-17T00:00:00Z"},
        {"zdr_baseline": {"provider_slug": "google-vertex", "data_policy": {"training": False}}},
        {"zdr_baseline": {"provider_slug": "azure", "data_policy": {}}},
        {"zdr_baseline": {"provider_slug": "azure", "data_policy": {"invented": True}}},
        {"zdr_baseline": None},
        {"approval_mode": "forever"},
    ],
)
def test_invalid_monitored_definitions_are_refused(extra: dict[str, Any]) -> None:
    with pytest.raises(PolicyError):
        parse_inference_policy(_monitored_policy(extra=extra))


def test_expiring_routes_keep_their_dates_and_refuse_a_baseline() -> None:
    doc = _production()
    route = doc["routes"][0]
    assert route.get("approval_mode", "expiring") in {"expiring", "monitored"}
    expiring = copy.deepcopy(doc)
    for item in expiring["routes"]:
        item.pop("approval_mode", None)
        item.pop("zdr_baseline", None)
        item["approval_expires_at"] = "2026-10-17T00:00:00Z"
        item["operator_review"]["review_expires_at"] = "2026-10-17T00:00:00Z"
    policy = parse_inference_policy(expiring)
    assert all(r.approval_mode == "expiring" for r in policy.routes.values())
    expiring["routes"][0]["zdr_baseline"] = {
        "provider_slug": "x",
        "data_policy": {"training": False},
    }
    with pytest.raises(PolicyError):
        parse_inference_policy(expiring)


def test_the_attestation_requirement_cannot_be_disabled() -> None:
    doc = _monitored_policy()
    doc["requirements"]["require_zdr_attestation"] = False
    with pytest.raises(PolicyError):
        parse_inference_policy(doc)


# --------------------------------------------------------------------------- evaluation


def test_unchanged_listing_and_policy_attest() -> None:
    [check] = attestation.evaluate([_route()], LISTED, _providers())
    assert (check.outcome, check.reasons) == ("attested", ())
    assert check.observed == {
        "zdr_listed": True,
        "data_policy": {"training": False, "retainsPrompts": False},
    }


def test_dated_model_revision_counts_as_listed() -> None:
    [check] = attestation.evaluate(
        [_route()], _zdr(("openai/gpt-6-luna-20260922", "azure/eu")), _providers()
    )
    assert check.outcome == "attested"


def test_leaving_the_zdr_listing_revokes() -> None:
    for listing in (
        _zdr(("openai/gpt-6-luna", "azure/us")),
        _zdr(("openai/gpt-6-lunar", "azure/eu")),
    ):
        [check] = attestation.evaluate([_route()], listing, _providers())
        assert (check.outcome, check.reasons) == ("revoked", ("left_zdr_listing",))


def test_a_provider_data_policy_change_revokes() -> None:
    [check] = attestation.evaluate([_route()], LISTED, _providers(retainsPrompts=True))
    assert (check.outcome, check.reasons) == (
        "revoked",
        ("provider_policy_changed:retainsPrompts",),
    )
    [check] = attestation.evaluate([_route()], LISTED, _providers(training=True))
    assert check.reasons == ("provider_policy_changed:training",)


def test_a_retention_policy_recorded_at_approval_is_not_a_change() -> None:
    # Approved knowing the provider retains prompts (as for Grok on xai/zdr/us).
    [check] = attestation.evaluate([_route(retains=True)], LISTED, _providers(retainsPrompts=True))
    assert check.outcome == "attested"


@pytest.mark.parametrize(
    ("zdr_payload", "providers_payload", "reason"),
    [
        ({"data": []}, _providers(), "metadata_malformed"),
        ({"unexpected": True}, _providers(), "metadata_malformed"),
        (LISTED, {"data": "not-a-list"}, "metadata_malformed"),
        (LISTED, {"data": [{"slug": "other", "dataPolicy": {}}]}, "provider_policy_missing"),
    ],
)
def test_unreadable_metadata_records_a_failed_check_and_revokes_nothing(
    zdr_payload: object, providers_payload: object, reason: str
) -> None:
    [check] = attestation.evaluate([_route()], zdr_payload, providers_payload)
    assert (check.outcome, check.reasons) == ("check_failed", (reason,))


def test_expiring_routes_are_not_checked() -> None:
    doc = _production()
    for item in doc["routes"]:
        item.pop("approval_mode", None)
        item.pop("zdr_baseline", None)
        item["approval_expires_at"] = "2026-10-17T00:00:00Z"
        item["operator_review"]["review_expires_at"] = "2026-10-17T00:00:00Z"
    routes = parse_inference_policy(doc).routes.values()
    assert attestation.evaluate(routes, LISTED, _providers()) == []


# --------------------------------------------------------------------------- snapshot


def test_snapshot_from_rows_keeps_baselines_separate() -> None:
    route = _route()
    key = attestation.baseline_fingerprint(route)
    snap = attestation.snapshot_from_rows(
        [
            {
                "route_id": route.route_id,
                "baseline_sha256": key,
                "outcome": "attested",
                "checked_at": NOW,
            },
            {
                "route_id": route.route_id,
                "baseline_sha256": "0" * 64,
                "outcome": "revoked",
                "checked_at": NOW,
            },
        ]
    )
    attestation.set_snapshot(snap)
    policy = parse_inference_policy(_monitored_policy())
    # A revocation of an older baseline does not touch the current one.
    assert route.is_approved(policy.requirements, now=NOW)


class _Conn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.inserted: list[tuple[Any, ...]] = []

    async def fetch(self, _sql: str) -> list[dict[str, Any]]:
        return self.rows

    async def execute(self, _sql: str, *args: Any) -> None:
        self.inserted.append(args)
        self.rows.append(
            {"route_id": args[0], "baseline_sha256": args[1], "outcome": args[2], "checked_at": NOW}
        )

    def transaction(self) -> Any:
        conn = self

        class _Tx:
            async def __aenter__(self) -> _Conn:
                return conn

            async def __aexit__(self, *_: object) -> None:
                return None

        return _Tx()


class _Pool:
    def __init__(self) -> None:
        self.conn = _Conn([])

    def acquire(self) -> Any:
        conn = self.conn

        class _Ctx:
            async def __aenter__(self) -> _Conn:
                return conn

            async def __aexit__(self, *_: object) -> None:
                return None

        return _Ctx()


class _Response:
    def __init__(self, payload: object, *, status: int = 200) -> None:
        self.content = json.dumps(payload).encode()
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError("http error")


class _Client:
    def __init__(
        self, zdr_payload: object, providers_payload: object, *, fail: bool = False
    ) -> None:
        self.payloads = {
            attestation.ZDR_LISTING_URL: zdr_payload,
            attestation.PROVIDERS_URL: providers_payload,
        }
        self.fail = fail
        self.urls: list[str] = []

    async def get(self, url: str, timeout: float) -> _Response:
        self.urls.append(url)
        if self.fail:
            raise TimeoutError("unreachable")
        return _Response(self.payloads[url])


@pytest.mark.asyncio
async def test_run_check_records_attests_and_refreshes_the_snapshot() -> None:
    pool, route = _Pool(), _route()
    client = _Client(LISTED, _providers())
    counts = await attestation.run_check(pool, client, [route])
    assert counts == {"routes": 1, "attested": 1, "check_failed": 0, "revoked": 0}
    assert client.urls == [attestation.ZDR_LISTING_URL, attestation.PROVIDERS_URL]
    [row] = pool.conn.inserted
    assert row[0] == route.route_id and row[2] == "attested" and len(row[5]) == 64
    policy = parse_inference_policy(_monitored_policy())
    assert route.rejection_reasons(policy.requirements, now=NOW) == ()


@pytest.mark.asyncio
async def test_unreachable_metadata_records_failure_without_revoking() -> None:
    pool, route = _Pool(), _route()
    counts = await attestation.run_check(pool, _Client(None, None, fail=True), [route])
    assert counts["check_failed"] == 1 and counts["revoked"] == 0
    assert pool.conn.inserted[0][2:4] == ("check_failed", ["metadata_unavailable"])


@pytest.mark.asyncio
async def test_revocation_closes_the_route_after_the_check() -> None:
    pool, route = _Pool(), _route()
    await attestation.run_check(pool, _Client(LISTED, _providers()), [route])
    await attestation.run_check(pool, _Client(LISTED, _providers(training=True)), [route])
    policy = parse_inference_policy(_monitored_policy())
    assert route.rejection_reasons(policy.requirements, now=NOW) == ("zdr_attestation_revoked",)
    # A later clean check does not silently restore it.
    await attestation.run_check(pool, _Client(LISTED, _providers()), [route])
    assert route.rejection_reasons(policy.requirements, now=NOW) == ("zdr_attestation_revoked",)


# --------------------------------------------------------------------------- migration


def _sql(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("--")
    )


def test_migration_is_additive_and_rollback_drops_only_its_table() -> None:
    sql = _sql(MIGRATION).upper()
    assert "CREATE TABLE IF NOT EXISTS INFERENCE_ROUTE_ATTESTATIONS" in sql
    for forbidden in ("ALTER TABLE", "DROP ", "UPDATE ", "DELETE "):
        assert forbidden not in sql
    down = _sql(ROLLBACK).upper()
    assert "DROP TABLE IF EXISTS INFERENCE_ROUTE_ATTESTATIONS" in down
    assert "ALTER TABLE" not in down
