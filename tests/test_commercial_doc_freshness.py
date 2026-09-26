"""Tests for the commercial-policy half of scripts/check_doc_freshness.py.

The retired five-tier architecture (TierConfig, ``tier_*`` model slots,
``list_available_tiers``) had no T0 source left, so the freshness gate dropped
its baked tier-slot checks. These tests lock in the replacement contract:

  * facts come from ``config/commercial.json`` / ``config/inference_policy.json``
  * checks compare *documentation* against declared policy, and stay silent when
    a document makes no matching structured claim
  * unrelated fact validation (migrations, embeddings, dedup, routes, env vars,
    workload model declarations) is untouched
  * runtime policy validation is not duplicated here
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from scripts.check_doc_freshness import (
    CheckId,
    _check_commercial_capabilities,
    _check_commercial_plans,
    _check_commercial_prices,
    _check_inference_policy,
    _check_legacy_plan_map,
    check_document,
    extract_all_facts,
    get_auto_routing_facts,
    get_commercial_facts,
    get_gated_docs,
    get_inference_policy_facts,
    get_route_facts,
    parse_exceptions,
    repo_root,
)

ROOT = repo_root()
SCRIPT_PATH = ROOT / "scripts" / "check_doc_freshness.py"
COMMERCIAL_JSON = ROOT / "config" / "commercial.json"
INFERENCE_POLICY_JSON = ROOT / "config" / "inference_policy.json"
SUBSCRIPTION_DOC = ROOT / "docs" / "SUBSCRIPTION_ARCHITECTURE.md"
RETIRED_IMAGE_ROUTER = ROOT / "backend" / "image_gen" / "router.py"


@pytest.fixture(scope="module")
def facts() -> dict:
    return extract_all_facts(ROOT)


@pytest.fixture(scope="module")
def commercial(facts: dict) -> dict:
    return facts["commercial"]


@pytest.fixture(scope="module")
def policy(facts: dict) -> dict:
    return facts["inference_policy"]


def _messages(results: list) -> list[str]:
    return [r.message for r in results]


def _ids(results: list) -> set[str]:
    return {
        r.check_id.value if isinstance(r.check_id, CheckId) else str(r.check_id) for r in results
    }


class TestCommercialFactExtraction:
    def test_plans_come_from_commercial_json(self, commercial: dict) -> None:
        assert commercial["plan_ids"] == ["free", "power", "pro"]
        assert commercial["plan_labels"] == {"free": "free", "pro": "pro", "power": "power"}

    def test_legacy_map_excludes_policy_commentary(self, commercial: dict) -> None:
        """`legacy_plan_map.notes` is prose, not a name mapping."""
        legacy = commercial["legacy_plan_map"]
        assert legacy["max"] == "power"
        assert legacy["starter"] == "pro"
        assert "notes" not in legacy
        assert all(v in commercial["plan_ids"] for v in legacy.values())

    def test_capability_and_operation_vocabularies_are_separate(self, commercial: dict) -> None:
        caps = set(commercial["capability_vocabulary"])
        ops = set(commercial["operation_vocabulary"])
        assert "chat" in caps and "chat" in ops
        # Every capability granted by a plan is part of the capability vocabulary.
        granted = {c for caps_list in commercial["capabilities"].values() for c in caps_list}
        assert granted and granted <= caps
        # Operations are billable units, not gated product capabilities, so the
        # two vocabularies must not be interchangeable.
        assert ops - caps, "operations leaked into the capability vocabulary"
        assert caps - ops, "capabilities leaked into the operation vocabulary"

    def test_display_prices_are_read_as_minor_units(self, commercial: dict) -> None:
        assert commercial["display"]["pro"] == {"currency": "AUD", "amount_minor": "2900"}
        assert commercial["display"]["power"] == {"currency": "AUD", "amount_minor": "9900"}
        assert commercial["display"]["free"] == {"currency": "AUD", "amount_minor": "0"}

    def test_missing_policy_file_yields_no_facts(self, tmp_path: Path) -> None:
        assert get_commercial_facts(tmp_path) == {}
        assert get_inference_policy_facts(tmp_path) == {}

    def test_malformed_policy_json_yields_no_facts(self, tmp_path: Path) -> None:
        """A broken policy file is a runtime concern, not a doc-freshness fact."""
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "commercial.json").write_text("{not json", encoding="utf-8")
        assert get_commercial_facts(tmp_path) == {}

    def test_inference_policy_facts_derive_requirements(self, policy: dict) -> None:
        assert policy["route_ids"] == {"legacy-openrouter-unverified": False}
        assert policy["service_ids"] == {"brave-web-search": False, "voyage-embeddings": False}
        assert policy["default_route_id"] is None
        flags = dict(policy["required_flags"])
        assert flags["zdr"] is True
        assert flags["data_collection"] == "deny"
        assert flags["allow_fallbacks"] is False
        assert flags["require_parameters"] is True
        assert "only" in flags and "order" in flags and "max_price" in flags

    def test_facts_do_not_import_runtime_policy_validation(self) -> None:
        """The linter reads JSON, it does not reuse runtime policy validators."""
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any(name.startswith("orchestrator") for name in imported)


class TestRetiredTierChecksAreGone:
    def test_tier_check_ids_removed(self) -> None:
        values = {member.value for member in CheckId}
        for retired in (
            "tier_model",
            "tier_video_provider",
            "tier_image_provider",
            "tier_price",
        ):
            assert retired not in values

    def test_commercial_check_ids_added(self) -> None:
        values = {member.value for member in CheckId}
        for added in (
            "commercial_plan",
            "commercial_capability",
            "commercial_price",
            "legacy_plan_map",
            "inference_policy",
        ):
            assert added in values

    def test_tier_extraction_helpers_removed(self) -> None:
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        for retired in (
            "def get_tier_facts",
            "def get_tier_prices",
            "def _check_tier_defaults",
            "def _check_tier_prices",
            "_TIER_MODEL_RE",
            "_TIER_NAME_MAP",
            "_TIER_PRICE_RE",
            'facts.get("tier_defaults"',
            'facts.get("tier_prices"',
        ):
            assert retired not in source, f"{retired} still present in the freshness gate"

    def test_no_five_tier_names_are_baked_into_the_gate(self) -> None:
        """No hardcoded FREE/STARTER/PRO/MAX/BYOK plan vocabulary remains."""
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        assert not re.search(r"FREE\|STARTER\|PRO\|MAX\|BYOK", source)
        # The retired runtime API is only named in the removal note, never called.
        assert "list_available_tiers(" not in source

    def test_workload_model_declaration_support_preserved(self) -> None:
        auto = get_auto_routing_facts(ROOT)
        assert auto["auto_fast_model"]
        assert auto["auto_reasoning_model"]
        assert CheckId.AUTO_FAST_MODEL in CheckId
        assert CheckId.AUTO_REASONING_MODEL in CheckId


class TestRouteExtraction:
    def test_retired_image_router_path_is_not_hardcoded(self) -> None:
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        assert "image_gen/router.py" not in source
        assert not RETIRED_IMAGE_ROUTER.exists(), (
            "backend/image_gen/router.py is deleted; the gate must discover mounted "
            "routers instead of pinning that path"
        )

    def test_supported_image_routes_still_gated(self) -> None:
        """orchestrator/routes/images.py is the supported 410 surface."""
        routes = get_route_facts(ROOT)["routes"]
        assert "/api/images/models" in routes.get("GET", [])
        assert "/api/images/generate" in routes.get("POST", [])
        assert "/api/images/{image_id}" in routes.get("GET", [])
        assert "/api/images/{image_id}/metadata" in routes.get("GET", [])

    def test_mounted_routers_are_discovered_from_main(self, tmp_path: Path) -> None:
        (tmp_path / "orchestrator" / "routes").mkdir(parents=True)
        (tmp_path / "orchestrator" / "main.py").write_text(
            "app.include_router(extra.router)\n", encoding="utf-8"
        )
        (tmp_path / "orchestrator" / "routes" / "extra.py").write_text(
            'router = APIRouter(prefix="/api/extra")\n\n@router.get("/thing")\n',
            encoding="utf-8",
        )
        routes = get_route_facts(tmp_path)["routes"]
        assert routes["GET"] == ["/api/extra/thing"]


class TestNoClaimNoFinding:
    """Each new check must be a no-op on prose that makes no matching claim."""

    DOC = """# Memory layers

| Tier | Description | Injection |
|---|---|---|
| L0 | summary | always |

The extraction prompt skips general knowledge. Deployment configuration lives in
`orchestrator/config.py`; migrations and thresholds are gated elsewhere.
"""

    def test_all_commercial_checks_silent(self, commercial: dict, policy: dict) -> None:
        assert _check_commercial_plans(self.DOC, commercial) == []
        assert _check_commercial_capabilities(self.DOC, commercial) == []
        assert _check_commercial_prices(self.DOC, commercial) == []
        assert _check_legacy_plan_map(self.DOC, commercial) == []
        assert _check_inference_policy(self.DOC, policy) == []

    def test_narrative_enumerations_are_not_plans(self, commercial: dict) -> None:
        doc = (
            "# Feature matrix\n\n"
            "| Feature | Web | Backend dependency |\n|---|---|---|\n"
            "| **BYOK** | - | - |\n"
            "| **Council/Studio** | - | - |\n\n"
            "Centralized Free/Pro/Power policy and usage-based trial.\n"
        )
        assert _check_commercial_plans(doc, commercial) == []

    def test_bold_headings_are_not_plans(self, commercial: dict) -> None:
        doc = "# Roadmap\n\n**Council/Studio** and **BYOK** are placeholders.\n"
        assert _check_commercial_plans(doc, commercial) == []


class TestCommercialPlanCheck:
    def test_real_subscription_doc_passes(self, commercial: dict) -> None:
        assert (
            _check_commercial_plans(SUBSCRIPTION_DOC.read_text(encoding="utf-8"), commercial) == []
        )

    def test_aligned_plan_list_passes(self, commercial: dict) -> None:
        doc = "The durable commercial plans are **Free**, **Pro**, and **Power**.\n"
        assert _check_commercial_plans(doc, commercial) == []

    def test_aligned_slash_list_passes(self, commercial: dict) -> None:
        doc = "The commercial model is Free / Pro / Power with a separate trial.\n"
        assert _check_commercial_plans(doc, commercial) == []

    def test_legacy_presentation_in_plan_list_fails(self, commercial: dict) -> None:
        doc = "The durable commercial plans are **Free**, **Starter**, and **Max**.\n"
        results = _check_commercial_plans(doc, commercial)
        assert _ids(results) == {CheckId.COMMERCIAL_PLAN.value}
        joined = " ".join(_messages(results))
        assert "starter" in joined and "max" in joined
        assert "retired legacy name" in joined

    def test_invented_plan_fails(self, commercial: dict) -> None:
        doc = "Commercial plans are Free, Pro, and Enterprise.\n"
        results = _check_commercial_plans(doc, commercial)
        assert _ids(results) == {CheckId.COMMERCIAL_PLAN.value}
        assert "enterprise" in _messages(results)[0]

    def test_missing_declared_plan_fails(self, commercial: dict) -> None:
        doc = "Commercial plans are Free and Pro.\n"
        results = _check_commercial_plans(doc, commercial)
        assert _ids(results) == {CheckId.COMMERCIAL_PLAN.value}
        assert "power" in _messages(results)[0]

    def test_plan_table_missing_row_fails(self, commercial: dict) -> None:
        doc = (
            "| Plan | Display price | Capabilities |\n|---|---|---|\n"
            "| **Free** | A$0 | `chat` |\n"
            "| **Pro** | A$29 | `web_research` |\n"
        )
        results = _check_commercial_plans(doc, commercial)
        assert _ids(results) == {CheckId.COMMERCIAL_PLAN.value}
        assert "power" in _messages(results)[0]

    def test_plan_table_unknown_row_fails(self, commercial: dict) -> None:
        doc = (
            "| Plan | Display price |\n|---|---|\n"
            "| **Free** | A$0 |\n| **Pro** | A$29 |\n| **Power** | A$99 |\n"
            "| **Platinum** | A$199 |\n"
        )
        results = _check_commercial_plans(doc, commercial)
        assert _ids(results) == {CheckId.COMMERCIAL_PLAN.value}
        assert "platinum" in _messages(results)[0]


class TestCommercialCapabilityCheck:
    def test_aligned_capability_table_passes(self, commercial: dict) -> None:
        doc = (
            "| Plan | Capabilities |\n|---|---|\n"
            "| **Free** | `chat` |\n"
            "| **Pro** | `chat`, `web_research`, `image_generation` |\n"
            "| **Power** | `chat`, `video_generation` |\n"
        )
        assert _check_commercial_capabilities(doc, commercial) == []

    def test_capability_not_granted_to_plan_fails(self, commercial: dict) -> None:
        paid_only = sorted(
            set(commercial["capabilities"].get("power", []))
            - set(commercial["capabilities"].get("free", []))
        )
        assert paid_only, "expected at least one capability that Free does not hold"
        borrowed = paid_only[0]
        doc = (
            "| Plan | Capabilities |\n|---|---|\n"
            f"| **Free** | `chat`, `{borrowed}` |\n"
            "| **Pro** | `chat` |\n"
            "| **Power** | `chat` |\n"
        )
        results = _check_commercial_capabilities(doc, commercial)
        assert _ids(results) == {CheckId.COMMERCIAL_CAPABILITY.value}
        assert "free" in _messages(results)[0]
        assert borrowed in _messages(results)[0]

    def test_free_claiming_paid_capability_fails(self, commercial: dict) -> None:
        doc = (
            "| Plan | Capabilities |\n|---|---|\n"
            "| **Free** | `chat`, `video_generation` |\n"
            "| **Pro** | `chat` |\n"
            "| **Power** | `chat` |\n"
        )
        results = _check_commercial_capabilities(doc, commercial)
        assert _ids(results) == {CheckId.COMMERCIAL_CAPABILITY.value}
        assert "free" in _messages(results)[0]

    def test_labelled_capability_list_fails_on_unknown(self, commercial: dict) -> None:
        doc = "capabilities: `chat`, `telepathy`\n"
        results = _check_commercial_capabilities(doc, commercial)
        assert _ids(results) == {CheckId.COMMERCIAL_CAPABILITY.value}
        assert "telepathy" in _messages(results)[0]

    def test_labelled_capability_list_passes_when_declared(self, commercial: dict) -> None:
        assert (
            _check_commercial_capabilities("capabilities: chat, web_research\n", commercial) == []
        )

    def test_operation_vocabulary_is_checked_separately(self, commercial: dict) -> None:
        operation = sorted(commercial["operation_vocabulary"])[0]
        capability = sorted(commercial["capability_vocabulary"])[0]
        doc = f"- operations: `chat`, `{operation}`, `telepathy`\n"
        results = _check_commercial_capabilities(doc, commercial)
        assert _ids(results) == {CheckId.COMMERCIAL_CAPABILITY.value}
        assert "telepathy" in _messages(results)[0]
        # An operation id is not a capability id: the two vocabularies differ.
        assert _check_commercial_capabilities(f"operations: {operation}\n", commercial) == []
        assert _check_commercial_capabilities(f"operations: {capability}\n", commercial) != []

    def test_mid_sentence_label_is_prose_not_a_claim(self, commercial: dict) -> None:
        """'for vector operations: `embedding::vector`' is not a policy claim."""
        doc = "Use `pgvector` for vector operations: `embedding::vector` casting\n"
        assert _check_commercial_capabilities(doc, commercial) == []

    def test_prose_without_label_is_not_gated(self, commercial: dict) -> None:
        doc = "Paid plans buy greater capacity and access to expensive workloads.\n"
        assert _check_commercial_capabilities(doc, commercial) == []


class TestCommercialPriceCheck:
    def test_aligned_price_table_passes(self, commercial: dict) -> None:
        doc = (
            "| Plan | Display price |\n|---|---|\n"
            "| **Free** | A$0 /mo |\n| **Pro** | A$29 /mo |\n| **Power** | A$99 /mo |\n"
        )
        assert _check_commercial_prices(doc, commercial) == []

    def test_wrong_amount_fails(self, commercial: dict) -> None:
        doc = (
            "| Plan | Display price |\n|---|---|\n"
            "| **Pro** | A$19 /mo |\n| **Power** | A$99 /mo |\n"
        )
        results = _check_commercial_prices(doc, commercial)
        assert _ids(results) == {CheckId.COMMERCIAL_PRICE.value}
        assert "expected A$29.00" in _messages(results)[0]

    def test_wrong_currency_fails(self, commercial: dict) -> None:
        """Display prices are AUD minor units and are never derived from USD."""
        doc = "| Plan | Display price |\n|---|---|\n| **Pro** | $29 /mo |\n"
        results = _check_commercial_prices(doc, commercial)
        assert _ids(results) == {CheckId.COMMERCIAL_PRICE.value}
        assert "AUD" in _messages(results)[0]

    def test_bold_plan_prose_price_fails(self, commercial: dict) -> None:
        doc = "**Power** is A$79 per month for heavy workloads.\n"
        results = _check_commercial_prices(doc, commercial)
        assert _ids(results) == {CheckId.COMMERCIAL_PRICE.value}
        assert "power" in _messages(results)[0]

    def test_unrelated_money_is_not_gated(self, commercial: dict) -> None:
        doc = "| Plan | Note |\n|---|---|\n| **Pro** | A$0.10 per second of video |\n"
        assert _check_commercial_prices(doc, commercial) == []


class TestLegacyPlanMapCheck:
    def test_aligned_mapping_passes(self, commercial: dict) -> None:
        doc = (
            "Legacy commercial names map deterministically: Starter -> Pro, "
            "Pro -> Pro, Max -> Power, Free -> Free.\n"
        )
        assert _check_legacy_plan_map(doc, commercial) == []

    def test_real_subscription_doc_passes(self, commercial: dict) -> None:
        assert (
            _check_legacy_plan_map(SUBSCRIPTION_DOC.read_text(encoding="utf-8"), commercial) == []
        )

    def test_wrong_target_fails(self, commercial: dict) -> None:
        doc = "Legacy names map deterministically: Starter -> Pro, Max -> Pro.\n"
        results = _check_legacy_plan_map(doc, commercial)
        assert _ids(results) == {CheckId.LEGACY_PLAN_MAP.value}
        assert "max" in _messages(results)[0]
        assert "power" in _messages(results)[0]

    def test_unknown_legacy_source_fails(self, commercial: dict) -> None:
        doc = "Legacy names map deterministically: Platinum -> Power.\n"
        results = _check_legacy_plan_map(doc, commercial)
        assert _ids(results) == {CheckId.LEGACY_PLAN_MAP.value}
        assert "platinum" in _messages(results)[0]

    def test_plan_to_plan_arrow_is_not_a_legacy_mapping(self, commercial: dict) -> None:
        doc = "Upgrades are mapped as Free -> Pro -> Power for existing accounts.\n"
        assert _check_legacy_plan_map(doc, commercial) == []

    def test_prose_without_mapping_keyword_is_not_gated(self, commercial: dict) -> None:
        doc = "The retired Starter tier no longer exists in configuration.\n"
        assert _check_legacy_plan_map(doc, commercial) == []


class TestInferencePolicyCheck:
    def test_real_subscription_doc_passes(self, policy: dict) -> None:
        assert _check_inference_policy(SUBSCRIPTION_DOC.read_text(encoding="utf-8"), policy) == []

    def test_undeclared_route_id_fails(self, policy: dict) -> None:
        doc = "Deployment uses `route_id: openrouter-cheap-and-good` for chat.\n"
        results = _check_inference_policy(doc, policy)
        assert _ids(results) == {CheckId.INFERENCE_POLICY.value}
        assert "openrouter-cheap-and-good" in _messages(results)[0]

    def test_undeclared_service_id_fails(self, policy: dict) -> None:
        doc = "The search dependency is `service_id: google-web-search`.\n"
        results = _check_inference_policy(doc, policy)
        assert _ids(results) == {CheckId.INFERENCE_POLICY.value}
        assert "google-web-search" in _messages(results)[0]

    def test_declared_route_id_passes(self, policy: dict) -> None:
        doc = "Deployment uses `route_id: legacy-openrouter-unverified` for chat.\n"
        assert _check_inference_policy(doc, policy) == []

    def test_approved_claim_on_unapproved_route_fails(self, policy: dict) -> None:
        doc = "The route legacy-openrouter-unverified is approved for chat traffic.\n"
        results = _check_inference_policy(doc, policy)
        assert _ids(results) == {CheckId.INFERENCE_POLICY.value}
        assert "approved=false" in _messages(results)[0]

    def test_unapproved_statement_passes(self, policy: dict) -> None:
        doc = "The route legacy-openrouter-unverified is not approved yet.\n"
        assert _check_inference_policy(doc, policy) == []

    def test_default_route_claim_without_policy_default_fails(self, policy: dict) -> None:
        doc = "default_route_id: legacy-openrouter-unverified\n"
        results = _check_inference_policy(doc, policy)
        assert _ids(results) == {CheckId.INFERENCE_POLICY.value}
        assert "no default route" in _messages(results)[0]

    def test_transport_flag_omission_fails(self, policy: dict) -> None:
        doc = (
            "The outbound request must pin the reviewed provider:\n\n"
            "```json\n"
            '{\n  "provider": {\n    "only": ["p"],\n    "order": ["p"],\n'
            '    "allow_fallbacks": false,\n    "require_parameters": true,\n'
            '    "data_collection": "deny"\n  }\n}\n'
            "```\n"
        )
        results = _check_inference_policy(doc, policy)
        assert _ids(results) == {CheckId.INFERENCE_POLICY.value}
        assert any("provider.zdr" in m for m in _messages(results))

    def test_transport_flag_value_drift_fails(self, policy: dict) -> None:
        doc = (
            "```json\n"
            '{\n  "provider": {\n    "only": ["p"],\n    "order": ["p"],\n'
            '    "allow_fallbacks": true,\n    "require_parameters": true,\n'
            '    "data_collection": "deny",\n    "zdr": true,\n'
            '    "max_price": {"prompt": 0, "completion": 0}\n  }\n}\n'
            "```\n"
        )
        results = _check_inference_policy(doc, policy)
        assert _ids(results) == {CheckId.INFERENCE_POLICY.value}
        assert any("allow_fallbacks" in m for m in _messages(results))

    def test_max_price_requires_prompt_and_completion(self, policy: dict) -> None:
        doc = (
            "```json\n"
            '{\n  "provider": {\n    "only": ["p"],\n    "order": ["p"],\n'
            '    "allow_fallbacks": false,\n    "require_parameters": true,\n'
            '    "data_collection": "deny",\n    "zdr": true,\n'
            '    "max_price": {"prompt": 0}\n  }\n}\n'
            "```\n"
        )
        results = _check_inference_policy(doc, policy)
        assert _ids(results) == {CheckId.INFERENCE_POLICY.value}
        assert any("max_price.completion" in m for m in _messages(results))

    def test_fully_pinned_request_passes(self, policy: dict) -> None:
        doc = (
            "```json\n"
            '{\n  "provider": {\n    "only": ["p"],\n    "order": ["p"],\n'
            '    "allow_fallbacks": false,\n    "require_parameters": true,\n'
            '    "data_collection": "deny",\n    "zdr": true,\n'
            '    "max_price": {"prompt": 0, "completion": 0}\n  }\n}\n'
            "```\n"
        )
        assert _check_inference_policy(doc, policy) == []

    def test_mermaid_diagram_is_not_json(self, policy: dict) -> None:
        doc = "```mermaid\nflowchart TD\n    A --> B\n    B --> C\n```\n"
        assert _check_inference_policy(doc, policy) == []


class TestExceptionSuppression:
    """New checks must honour the existing exception mechanism."""

    def _write(self, tmp_path: Path, body: str) -> Path:
        doc = tmp_path / "SUBSCRIPTION_ARCHITECTURE.md"
        doc.write_text(body, encoding="utf-8")
        return doc

    def test_active_exception_suppresses_finding(self, tmp_path: Path, facts: dict) -> None:
        tomorrow = date.today() + timedelta(days=1)
        doc = self._write(
            tmp_path,
            "Commercial plans are Free, Pro, and Ultra.\n"
            f"<!-- DOC_FRESHNESS_EXCEPTION: commercial_plan expires={tomorrow} "
            'reason="plan rename lands with the launch announcement" -->\n',
        )
        exceptions, malformed = parse_exceptions(
            doc.read_text(encoding="utf-8").splitlines(), str(doc)
        )
        assert not malformed
        findings, active = check_document(doc, facts, exceptions, date.today())
        commercial_findings = [f for f in findings if f.check_id == "commercial_plan"]
        assert commercial_findings == []
        assert [e for e in active if e.suppressed_finding]

    def test_expired_exception_still_reports(self, tmp_path: Path, facts: dict) -> None:
        yesterday = date.today() - timedelta(days=1)
        doc = self._write(
            tmp_path,
            "Commercial plans are Free, Pro, and Ultra.\n"
            f"<!-- DOC_FRESHNESS_EXCEPTION: commercial_plan expires={yesterday} "
            'reason="already stale" -->\n',
        )
        exceptions, _ = parse_exceptions(doc.read_text(encoding="utf-8").splitlines(), str(doc))
        findings, _active = check_document(doc, facts, exceptions, date.today())
        assert any(f.check_id == "commercial_plan" for f in findings)


class TestGateIntegration:
    def test_gated_docs_include_subscription_architecture(self) -> None:
        names = {p.name for p in get_gated_docs(ROOT)}
        assert "SUBSCRIPTION_ARCHITECTURE.md" in names

    def test_policy_sources_are_registered_as_t0(self) -> None:
        sot = (ROOT / "docs" / "SOURCES_OF_TRUTH.md").read_text(encoding="utf-8")
        assert "config/commercial.json" in sot
        assert "config/inference_policy.json" in sot

    def test_fail_mode_passes_for_subscription_doc(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--mode",
                "fail",
                "--files",
                str(SUBSCRIPTION_DOC),
            ],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    def test_no_commercial_findings_across_gated_docs(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--mode", "report", "--format", "json"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        report = json.loads(result.stdout)
        commercial_ids = {
            "commercial_plan",
            "commercial_capability",
            "commercial_price",
            "legacy_plan_map",
            "inference_policy",
        }
        offenders = [f for f in report["findings"] if f["check_id"] in commercial_ids]
        assert offenders == [], f"unexpected commercial findings: {offenders}"
        assert report["checked_sources"]["commercial"]["plan_ids"]
        assert report["checked_sources"]["inference_policy"]["route_ids"]

    def test_unrelated_fact_validation_still_runs(self) -> None:
        """Migrations, dedup, video providers, routes and env vars stay gated."""
        facts = extract_all_facts(ROOT)
        assert facts["migrations"]["count"] > 0
        assert facts["migrations"]["latest"]
        assert facts["embeddings"]["document_model"]
        assert facts["providers"]["video_providers"]
        assert facts["routes"]["routes"]
        assert facts["env_vars"]["env_vars"]
        assert facts["docker"]["service_count"] > 0
        assert facts["subagents"]
        assert facts["auto_routing"]["auto_fast_model"]

    def test_migration_drift_still_fires(self, tmp_path: Path) -> None:
        facts = extract_all_facts(ROOT)
        wrong = facts["migrations"]["count"] + 7
        doc = tmp_path / "SUBSCRIPTION_ARCHITECTURE.md"
        doc.write_text(f"The schema has {wrong:02d} migrations applied.\n", encoding="utf-8")
        findings, _ = check_document(doc, facts, [], date.today())
        assert any(f.check_id == "migration_count" for f in findings)

    def test_dedup_threshold_drift_still_fires(self, tmp_path: Path) -> None:
        facts = extract_all_facts(ROOT)
        doc = tmp_path / "SUBSCRIPTION_ARCHITECTURE.md"
        doc.write_text("The merge threshold for duplicate facts is 0.11.\n", encoding="utf-8")
        findings, _ = check_document(doc, facts, [], date.today())
        assert any(f.check_id == "dedup_merge_threshold" for f in findings)

    def test_video_provider_drift_still_fires(self, tmp_path: Path) -> None:
        facts = extract_all_facts(ROOT)
        doc = tmp_path / "SUBSCRIPTION_ARCHITECTURE.md"
        doc.write_text("Video provider: sora\n", encoding="utf-8")
        findings, _ = check_document(doc, facts, [], date.today())
        assert any(f.check_id == "video_providers" for f in findings)
