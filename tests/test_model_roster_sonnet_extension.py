"""Offline, no-provider regressions for the opt-in Sonnet roster arm."""

from __future__ import annotations

import hashlib
import json
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from scripts import model_roster_live as live
from scripts import model_roster_pilot as pilot


EXTENSION = "sonnet-v1"
MODEL = "openrouter/anthropic/claude-sonnet-5"
FIXTURE_SHA256 = "4b63156db698da082e249ff7537724ceabf028392ecd01421551534c21dbaad4"


@pytest.fixture
def fixtures() -> pilot.FixtureSet:
    assert hashlib.sha256(pilot.DEFAULT_FIXTURES_PATH.read_bytes()).hexdigest() == FIXTURE_SHA256
    return pilot.load_fixtures(pilot.DEFAULT_FIXTURES_PATH)


def _results(fixtures: pilot.FixtureSet, *, extension: str | None) -> dict[str, Any]:
    document: dict[str, Any] = {
        "artifact_version": pilot.RESULTS_ARTIFACT_VERSION,
        "fixtures_sha256": fixtures.sha256,
        "attempts": [],
    }
    if extension is not None:
        document["extension"] = extension
    return document


def _write(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "results.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _score_cli(tmp_path: Path, document: dict[str, Any], *, extension: str | None) -> int:
    argv = [
        "score",
        "--results",
        str(_write(tmp_path, document)),
        "--format",
        "json",
        "--out",
        str(tmp_path / "report.json"),
    ]
    if extension is not None:
        argv += ["--extension", extension]
    return pilot.main(argv)


def test_default_plan_remains_208_after_extension_selection(fixtures: pilot.FixtureSet) -> None:
    before: dict[str, Any] = pilot.build_plan(fixtures, pilot.DEFAULT_FIXTURES_PATH)
    assert "extension" not in before
    assert pilot.resolve_catalog() == pilot.CANDIDATES
    assert pilot.extension_model_id() is None
    assert pilot.planned_attempt_ceiling() == 208
    assert before["counts"]["planned_attempts"] == 208

    sonnet = pilot.build_plan(fixtures, pilot.DEFAULT_FIXTURES_PATH, extension=EXTENSION)
    assert pilot.build_plan(fixtures, pilot.DEFAULT_FIXTURES_PATH) == before
    assert "extension" not in pilot.build_plan(fixtures)
    assert len(pilot.CANDIDATES) == 5
    assert sonnet["fixtures"] == before["fixtures"]
    assert sonnet["cases"] == before["cases"]
    assert sonnet["thresholds"] == before["thresholds"]
    assert sonnet["dispatch"] == before["dispatch"]
    assert sonnet["qualification"] == before["qualification"]
    assert sonnet["authorization"] == before["authorization"]


def test_sonnet_is_48_disjoint_attempts_over_frozen_cases(fixtures: pilot.FixtureSet) -> None:
    default: dict[str, Any] = pilot.build_plan(fixtures)
    plan: dict[str, Any] = pilot.build_plan(fixtures, extension=EXTENSION)
    assert fixtures.sha256 == FIXTURE_SHA256
    assert pilot.extension_model_id(EXTENSION) == MODEL
    assert pilot.planned_attempt_ceiling(EXTENSION) == 48
    assert plan["fixtures"]["sha256"] == default["fixtures"]["sha256"] == FIXTURE_SHA256
    assert plan["cases"] == default["cases"]
    assert plan["extension"]["id"] == EXTENSION
    assert plan["extension"]["authorized_model_id"] == MODEL
    assert plan["extension"]["fallback_candidates"] == []
    assert plan["candidates"][0]["label"] == "sonnet"
    assert len(plan["candidates"]) == 1
    assert plan["counts"]["planned_attempts"] == 48
    assert plan["counts"]["upper_call_bound"] == 144
    assert plan["counts"]["planned_attempts_by_candidate"] == {"sonnet": 48}
    assert plan["counts"]["planned_attempts_by_workload"] == {
        "orchestration": 16,
        "synthesis": 16,
        "utility": 16,
    }
    default_ids = {row["attempt_id"] for row in default["attempts"]}
    extension_ids = {row["attempt_id"] for row in plan["attempts"]}
    assert len(default_ids) == 208
    assert len(extension_ids) == 48
    assert extension_ids.isdisjoint(default_ids)
    assert extension_ids == {
        f"{case.case_id}-sonnet-r{repeat}" for case in fixtures.cases for repeat in (1, 2)
    }
    assert all(
        row["qualification"] == {"resolved": False, "model_id": None, "endpoint": None}
        for row in plan["attempts"]
    )


@pytest.mark.parametrize(
    ("selector", "declared", "record_id", "match"),
    [
        (None, EXTENSION, None, "results.extension"),
        (EXTENSION, None, None, "results.extension"),
        (EXTENSION, "other-extension", None, "results.extension"),
        (EXTENSION, EXTENSION, "O01-luna-r1", "not a planned attempt id"),
        (None, None, "O01-sonnet-r1", "not a planned attempt id"),
    ],
)
def test_score_rejects_cross_plan_results_and_mixed_ids(
    fixtures: pilot.FixtureSet,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    selector: str | None,
    declared: str | None,
    record_id: str | None,
    match: str,
) -> None:
    document = _results(fixtures, extension=declared)
    if record_id is not None:
        document["attempts"] = [{"attempt_id": record_id}]
    assert _score_cli(tmp_path, document, extension=selector) == pilot.EXIT_REJECTED
    assert match in capsys.readouterr().err
    assert not (tmp_path / "report.json").exists()


def test_sonnet_scoring_is_isolated_and_requires_human_evidence(
    fixtures: pilot.FixtureSet, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    document = _results(fixtures, extension=EXTENSION)
    assert _score_cli(tmp_path, document, extension=EXTENSION) == pilot.EXIT_SCREENING_NOT_MET
    capsys.readouterr()
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["extension"]["authorized_model_id"] == MODEL
    assert report["totals"]["planned_attempts"] == 48
    assert report["totals"]["passes"] == 0
    assert report["totals"]["incomplete"] == 48
    assert {row["candidate_label"] for row in report["attempts"]} == {"sonnet"}
    assert {row["workload"] for row in report["slices"]} == set(pilot.WORKLOADS)

    attempt = pilot.build_attempts(fixtures, pilot.resolve_catalog(EXTENSION))[0]
    document["attempts"] = [
        {
            "attempt_id": attempt.attempt_id,
            "calls_used": 1,
            "latency_seconds": 1.0,
            "semantic_verdict": "pending",
        }
    ]
    assert _score_cli(tmp_path, document, extension=EXTENSION) == pilot.EXIT_SCREENING_NOT_MET
    capsys.readouterr()
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["totals"]["passes"] == 0
    assert report["totals"]["incomplete"] == 48


def test_live_budget_is_bounded_by_selected_plan() -> None:
    assert live.attempt_budget(None, None) == 208
    assert live.attempt_budget(EXTENSION, None) == 48
    assert live.attempt_budget(None, 208) == 208
    assert live.attempt_budget(EXTENSION, 48) == 48
    assert live.attempt_budget(EXTENSION, 1) == 1
    for extension, ceiling in ((None, 208), (EXTENSION, 48)):
        for invalid in (0, ceiling + 1):
            with pytest.raises(live.LiveError, match=f"1..{ceiling}"):
                live.attempt_budget(extension, invalid)


class Route:
    def __init__(self, route_id: str, model: str = MODEL) -> None:
        self.route_id = route_id
        self.model = model
        self.provider = "openrouter"
        self.route_class = "premium"
        self.price_ceiling = object()
        self.max_output_tokens = 4096

    def is_approved(self, requirements: object) -> bool:
        return self.route_id != "unapproved"

    def transport_payload(self, requirements: object) -> dict[str, Any]:
        return {"provider": {"only": ["pinned-provider"]}}


def _inference(*routes: Route) -> SimpleNamespace:
    catalog = {route.route_id: route for route in routes}
    return SimpleNamespace(routes=catalog, route=catalog.get, requirements=object())


def test_extension_pins_accept_only_qualified_exact_model_and_no_default_fallback(
    fixtures: pilot.FixtureSet,
) -> None:
    sonnet = pilot.resolve_catalog(EXTENSION)
    approved = Route("sonnet-route")
    assert live.pinned_routes(
        {"sonnet": approved.route_id}, _inference(approved), fixtures, sonnet
    ) == {"sonnet": approved}
    for pins in ({"luna": "sonnet-route"}, {"sonnet": "sonnet-route", "luna": "sonnet-route"}, {}):
        with pytest.raises(live.LiveError, match="planned candidate subset"):
            live.pinned_routes(pins, _inference(approved), fixtures, sonnet)
    with pytest.raises(live.LiveError, match="planned candidate subset"):
        live.pinned_routes({"sonnet": approved.route_id}, _inference(approved), fixtures)
    with pytest.raises(live.LiveError, match="unqualified"):
        live.pinned_routes(
            {"sonnet": "unapproved"}, _inference(Route("unapproved")), fixtures, sonnet
        )
    with pytest.raises(live.LiveError, match="ambiguous"):
        live.pinned_routes(
            {"sonnet": approved.route_id},
            _inference(approved, Route("competitor")),
            fixtures,
            sonnet,
        )
    with pytest.raises(live.LiveError, match="model|Sonnet|sonnet"):
        live.pinned_routes(
            {"sonnet": "wrong-revision"},
            _inference(Route("wrong-revision", "openrouter/anthropic/claude-sonnet-4")),
            fixtures,
            sonnet,
        )


def test_state_identity_keeps_legacy_default_and_rejects_cross_extension(
    fixtures: pilot.FixtureSet, tmp_path: Path
) -> None:
    default = {
        "fixtures_sha256": fixtures.sha256,
        "period_key": "2026-09",
        "pins": {"luna": "route-luna"},
    }
    sonnet = {**default, "pins": {"sonnet": "route-sonnet"}, "extension": EXTENSION}
    state_path = tmp_path / "state.json"
    with live.locked_state(state_path, default) as state:
        assert "extension" not in state["identity"]
        state["attempts"]["O01-luna-r1"] = {"status": "in_progress", "calls": []}
        live.write_state(state_path, state)
    with live.locked_state(state_path, default) as resumed:
        assert "O01-luna-r1" in resumed["attempts"]
    with pytest.raises(live.LiveError, match="run identity drift"):
        with live.locked_state(state_path, sonnet):
            pytest.fail("Sonnet must never resume a default run")
    extension_path = tmp_path / "extension.json"
    with live.locked_state(extension_path, sonnet) as state:
        assert state["identity"]["extension"] == EXTENSION
        state["attempts"]["O01-sonnet-r1"] = {"status": "in_progress", "calls": []}
        live.write_state(extension_path, state)
    with live.locked_state(extension_path, sonnet) as resumed:
        assert "O01-sonnet-r1" in resumed["attempts"]
    with pytest.raises(live.LiveError, match="run identity drift"):
        with live.locked_state(extension_path, default):
            pytest.fail("Default must never resume a Sonnet run")


@pytest.mark.asyncio
async def test_live_run_records_extension_only_in_opted_in_identity(
    fixtures: pilot.FixtureSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the runner's identity assembly without a database or provider call."""
    commercial_path = tmp_path / "commercial.json"
    inference_path = tmp_path / "inference.json"
    commercial_path.write_text("{}", encoding="utf-8")
    inference_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(live, "policy_paths", lambda: (commercial_path, inference_path))
    policy = SimpleNamespace(
        plan=lambda name: SimpleNamespace(capabilities={live.Capability.PREMIUM_ROUTING})
    )
    monkeypatch.setattr(live, "load_policy", lambda path: policy)
    monkeypatch.setattr(live, "validate_commercial", lambda selected: None)
    routes = (Route("default-route", "openrouter/vendor/default"), Route("sonnet-route"))
    monkeypatch.setattr(live, "load_inference_policy", lambda path: _inference(*routes))
    monkeypatch.setattr(live, "verify_candidate", lambda *args: 1)
    monkeypatch.setattr(live, "ensure_period_window", lambda *args: None)
    dispatch = AsyncMock()
    monkeypatch.setattr(live, "run_attempt", dispatch)
    pool = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(live.asyncpg, "create_pool", AsyncMock(return_value=pool))
    service = SimpleNamespace(resolve=AsyncMock(return_value=object()))
    monkeypatch.setattr(live, "EntitlementService", lambda db: service)
    monkeypatch.setattr(
        live, "validate_database", AsyncMock(return_value="daemon_roster_pilot_test")
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake-local-evaluation-db")
    monkeypatch.setattr(
        live,
        "get_settings",
        lambda: SimpleNamespace(
            database_url="postgresql://fake-local-evaluation-db", openrouter_api_key="fake-key"
        ),
    )

    async def execute(
        extension: str | None, label: str, route_id: str, path: Path
    ) -> dict[str, Any]:
        pins = tmp_path / "pins.json"
        pins.write_text(json.dumps({label: route_id}), encoding="utf-8")
        args = Namespace(
            extension=extension,
            fixtures=pilot.DEFAULT_FIXTURES_PATH,
            pins=pins,
            only_candidate=None,
            account="synthetic-account",
            period="2026-09",
            state=path,
            results=None,
            max_attempts=1,
        )
        await live.run(args)
        return json.loads(path.read_text(encoding="utf-8"))

    default_path = tmp_path / "default-state.json"
    original = await execute(None, "luna", "default-route", default_path)
    assert "extension" not in original["identity"]
    assert original["identity"]["fixtures_sha256"] == FIXTURE_SHA256
    assert original["identity"]["pins"] == {"luna": "default-route"}
    assert (
        "extension" not in (await execute(None, "luna", "default-route", default_path))["identity"]
    )
    sonnet_path = tmp_path / "sonnet-state.json"
    opted_in = await execute(EXTENSION, "sonnet", "sonnet-route", sonnet_path)
    assert opted_in["identity"]["extension"] == EXTENSION
    assert opted_in["identity"]["pins"] == {"sonnet": "sonnet-route"}
    with pytest.raises(live.LiveError, match="run identity drift"):
        await execute(EXTENSION, "sonnet", "sonnet-route", default_path)
    with pytest.raises(live.LiveError, match="run identity drift"):
        await execute(None, "luna", "default-route", sonnet_path)
    assert dispatch.await_count == 3


def test_pending_import_carries_discriminator_and_exact_fixture_provenance(
    fixtures: pilot.FixtureSet, tmp_path: Path
) -> None:
    state = {
        "attempts": {
            "O01-sonnet-r1": {
                "status": "completed",
                "calls": [{"provider_cost_usd": 0.25, "account_charge_microusd": 250000}],
                "latency_seconds": 2.0,
                "schema_valid": None,
            }
        }
    }
    pending = live.pending_results(state, fixtures, EXTENSION)
    assert pending["extension"] == EXTENSION
    assert pending["fixtures_sha256"] == FIXTURE_SHA256
    assert pending["attempts"][0]["semantic_verdict"] == "pending"
    assert pending["attempts"][0]["cost_usd"] == 0.25
    assert "250000 microusd" in pending["attempts"][0]["notes"]
    attempts = pilot.build_attempts(fixtures, pilot.resolve_catalog(EXTENSION))
    parsed = pilot.parse_results(
        _write(tmp_path, pending),
        {a.attempt_id: a for a in attempts},
        fixtures.sha256,
        extension=EXTENSION,
    )
    assert parsed["O01-sonnet-r1"].semantic_verdict == "pending"
    assert live.pending_results({"attempts": {}}, fixtures) == _results(fixtures, extension=None)


def test_unknown_cli_extension_rejected_without_creating_artifacts(
    fixtures: pilot.FixtureSet,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for command in ("plan", "score"):
        out = tmp_path / f"{command}.json"
        argv = [command, "--extension", "unknown", "--out", str(out)]
        if command == "score":
            argv += ["--results", str(_write(tmp_path, _results(fixtures, extension=EXTENSION)))]
        assert pilot.main(argv) == pilot.EXIT_REJECTED
        assert "unknown extension" in capsys.readouterr().err
        assert not out.exists()

    monkeypatch.setattr(
        live,
        "policy_paths",
        lambda: pytest.fail("invalid selector must fail before policy preflight"),
    )
    with pytest.raises(pilot.ArtifactError, match="unknown extension"):
        live.attempt_budget("unknown", None)
    pins = tmp_path / "pins.json"
    pins.write_text("{}", encoding="utf-8")
    state = tmp_path / "invalid-state.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "model_roster_live.py",
            "--extension",
            "unknown",
            "--pins",
            str(pins),
            "--state",
            str(state),
            "--account",
            "00000000-0000-0000-0000-000000000001",
            "--period",
            "2026-09",
        ],
    )
    assert live.main() == 2
    assert "unknown extension" in capsys.readouterr().err
    assert not state.exists()
