#!/usr/bin/env python3
"""Offline planner and scorer for the proposed model roster pilot.

This is a *fixture* tool. It expands the 24 synthetic offline task fixtures in
``tests/fixtures/model_roster_pilot.json`` into a deterministic attempt plan, and
scores externally supplied attempt results against the screening thresholds
proposed in ``docs/MODEL_ROSTER_EVALUATION.md`` ("Proposed first pilot").

Scope and non-goals (deliberate, not oversights):

* No network, provider, SDK or client imports. Standard library only.
* No environment or secret loading, and no writes outside the ``--out`` path.
* No runtime route configuration is read or mutated. ``config/model_routing.json``
  and ``config/inference_policy.json`` are untouched: candidate labels in this
  plan are *logical* labels whose endpoint and model qualification is unresolved.
* No executor. ``plan`` never dispatches a call; endpoint qualification, account
  qualification, reservation and settlement stay in the approved runtime path
  (``orchestrator/compute_runtime.py``) and are not bypassed here.
* Fixture tool schemas and structured-output schemas are benchmark-only stubs.
  They are not production tool contracts and not the runtime memory schema.

The ``model-roster-pilot-*`` strings below are versions of this *local artifact
file format*. They are not a public API, schema registry or wire contract.

Extensions: ``--extension <id>`` is an explicit opt-in selector for a
separately identified plan. An extension is planned on the same frozen 24-case
fixture with its own attempt ids, declares its extension discriminator in the
plan, and is scored only by the same explicit selector: the default scorer
never accepts an extension artifact, and an extension scorer never accepts a
default artifact. Omitting the selector keeps the default five-candidate,
208-attempt plan, unchanged in shape and content.

Usage:
  model_roster_pilot.py plan  [--fixtures PATH] [--out PATH] [--format text|json]
                              [--extension EXTENSION_ID]
  model_roster_pilot.py score --results PATH [--out PATH] [--format text|json]
                              [--extension EXTENSION_ID]

Exit codes:
  0  artifact produced, and (score) every candidate slice met its screening gate
  1  (score) scored, but a slice was incomplete, missed the success threshold or
     recorded a hard violation
  2  input rejected: unreadable, malformed, or failing strict validation
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

# --------------------------------------------------------------------------
# Artifact format versions (local file format, not a public API)
# --------------------------------------------------------------------------

FIXTURES_ARTIFACT_VERSION = "model-roster-pilot-fixtures/1"
PLAN_ARTIFACT_VERSION = "model-roster-pilot-plan/1"
RESULTS_ARTIFACT_VERSION = "model-roster-pilot-results/1"
REPORT_ARTIFACT_VERSION = "model-roster-pilot-report/1"

# --------------------------------------------------------------------------
# Proposed pilot shape (docs/MODEL_ROSTER_EVALUATION.md)
# --------------------------------------------------------------------------

REPEATS_PER_CASE = 2
CASES_PER_WORKLOAD = 8
ATTEMPTS_PER_SLICE = CASES_PER_WORKLOAD * REPEATS_PER_CASE
MAX_CALLS_PER_ATTEMPT = 3
SUCCESS_THRESHOLD_PER_SLICE = 15
LATENCY_P95_TARGETS_SECONDS: Mapping[str, float] = {
    "utility": 10.0,
    "orchestration": 30.0,
    "synthesis": 60.0,
}
# Restated from the proposed pilot as a suggestion only. Nothing here approves
# spend, and this harness cannot reserve against the account ledger.
SUGGESTED_SPEND_CAP_USD = 25.0

WORKLOADS: tuple[str, ...] = ("orchestration", "synthesis", "utility")
CASE_ID_PATTERN = re.compile(r"^[ORU]0[1-8]$")
WORKLOAD_BY_CASE_PREFIX: Mapping[str, str] = {
    "O": "orchestration",
    "R": "synthesis",
    "U": "utility",
}
HARD_VIOLATION_KINDS: tuple[str, ...] = (
    "fabricated_tool_result",
    "fabricated_source",
    "prompt_injection_followed",
    "unauthorized_action",
)

EXIT_OK = 0
EXIT_SCREENING_NOT_MET = 1
EXIT_REJECTED = 2

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURES_PATH = REPO_ROOT / "tests" / "fixtures" / "model_roster_pilot.json"

RESULT_TOP_REQUIRED: tuple[str, ...] = ("artifact_version", "fixtures_sha256", "attempts")
RESULT_TOP_OPTIONAL: tuple[str, ...] = ("notes", "extension")
RESULT_REQUIRED: tuple[str, ...] = ("attempt_id",)
RESULT_OPTIONAL: tuple[str, ...] = (
    "calls_used",
    "latency_seconds",
    "cost_usd",
    "schema_valid",
    "semantic_verdict",
    "adjudicator",
    "hard_violation_kinds",
    "notes",
)
SEMANTIC_VERDICTS: tuple[str, ...] = ("pass", "fail", "pending")

# Reasons that make an attempt a failure. Everything else that blocks a pass
# leaves the attempt incomplete.
FAIL_REASONS: frozenset[str] = frozenset(
    {
        "semantic_failed",
        "schema_violation",
        "calls_exhausted",
        "no_model_call",
    }
)


class ArtifactError(Exception):
    """Raised when an input artifact is unreadable, malformed or invalid."""


@dataclass(frozen=True)
class Candidate:
    """A logical candidate label. Not a model id and not a qualified route."""

    label: str
    workloads: tuple[str, ...]
    role: str
    note: str


CANDIDATES: tuple[Candidate, ...] = (
    Candidate(
        label="luna",
        workloads=WORKLOADS,
        role="comparison",
        note="Logical label only. Exact revision, endpoint and price ceiling unresolved.",
    ),
    Candidate(
        label="glm-flash",
        workloads=WORKLOADS,
        role="comparison",
        note="Logical label only. Exact revision, endpoint and price ceiling unresolved.",
    ),
    Candidate(
        label="deepseek-flash",
        workloads=WORKLOADS,
        role="comparison",
        note="Logical label only. Exact revision, endpoint and price ceiling unresolved.",
    ),
    Candidate(
        label="sol",
        workloads=WORKLOADS,
        role="quality-reference",
        note="Quality reference arm; not a promotion candidate on pilot evidence alone.",
    ),
    Candidate(
        label="mercury",
        workloads=("utility",),
        role="utility-only",
        note="Utility slice only in this pilot, per the proposed first pilot.",
    ),
)

# --------------------------------------------------------------------------
# Opt-in extensions (separately identified; never part of the default plan)
# --------------------------------------------------------------------------
#
# An extension is a distinct, explicitly selected plan. It does not extend
# ``CANDIDATES``: the default five-candidate, 208-attempt plan is unchanged and
# is still what an omitted selector resolves to. An extension is planned on the
# same frozen 24-case fixture, keeps its own attempt ids, and is scored only by
# the same explicit selector, so extension evidence is never merged with the
# default records and never resets them.
#
# The model identity below is the exact authorized model for the arm. Declaring
# a model identity is not endpoint qualification: the route pin is supplied by
# the operator's external inference policy, and no endpoint, provider tag,
# region, privacy decision or price ceiling is approved by this file. Adding an
# extension must not encode an endpoint approval here.

SONNET_EXTENSION_ID = "sonnet-v1"
SONNET_CANDIDATE_LABEL = "sonnet"
SONNET_MODEL_ID = "openrouter/anthropic/claude-sonnet-5"

EXTENSION_CATALOGS: Mapping[str, tuple[Candidate, ...]] = {
    SONNET_EXTENSION_ID: (
        Candidate(
            label=SONNET_CANDIDATE_LABEL,
            workloads=WORKLOADS,
            role="extension-challenger",
            note=(
                f"Opt-in extension arm on the unchanged 24-case fixture. Authorized model "
                f"identity: {SONNET_MODEL_ID}. The route pin is supplied through the operator's "
                "external inference policy. No fallback arm is planned for this extension."
            ),
        ),
    ),
}

# Authorized model identity per extension candidate label. Kept beside the
# catalogs so a new extension must declare its identity explicitly.
EXTENSION_MODEL_IDS: Mapping[str, str] = {SONNET_CANDIDATE_LABEL: SONNET_MODEL_ID}


def resolve_extension(extension: str | None) -> str | None:
    """Normalize an explicit extension selector, or reject it.

    ``None`` means the default plan and is the only value that is not an
    opt-in extension id. Resolution is a pure function: it never mutates a
    catalog, so a rejected or extension run cannot disturb the default plan.
    """
    if extension is None:
        return None
    if not isinstance(extension, str) or extension not in EXTENSION_CATALOGS:
        raise ArtifactError(
            f"extension: unknown extension {extension!r}; expected none for the default plan or "
            f"one of {', '.join(sorted(EXTENSION_CATALOGS))}"
        )
    return extension


def resolve_catalog(extension: str | None = None) -> tuple[Candidate, ...]:
    """The candidate catalog for the selected plan.

    The default plan resolves to ``CANDIDATES``; an extension resolves to its
    own catalog, which shares no candidate label with the default five and
    plans exactly one arm, so an extension can never silently add, replace or
    fall back to a default candidate.
    """
    selected = resolve_extension(extension)
    if selected is None:
        return CANDIDATES
    catalog = EXTENSION_CATALOGS[selected]
    default_labels = {candidate.label for candidate in CANDIDATES}
    overlap = sorted(candidate.label for candidate in catalog if candidate.label in default_labels)
    if overlap:  # pragma: no cover - catalog authoring guard
        raise ArtifactError(
            f"extension {selected!r}: candidate label(s) {', '.join(overlap)} also exist in the "
            "default plan; an extension must stay separately identified"
        )
    if len(catalog) != 1:  # pragma: no cover - catalog authoring guard
        raise ArtifactError(
            f"extension {selected!r}: an extension plans exactly one arm; an extra or fallback "
            "candidate needs its own separate approval and comparison"
        )
    return catalog


def extension_model_id(extension: str | None = None) -> str | None:
    """The exact authorized model identity declared by an extension, if any."""
    selected = resolve_extension(extension)
    if selected is None:
        return None
    label = resolve_catalog(selected)[0].label
    model_id = EXTENSION_MODEL_IDS.get(label)
    if model_id is None:  # pragma: no cover - catalog authoring guard
        raise ArtifactError(
            f"extension {selected!r}: candidate {label!r} declares no authorized model identity"
        )
    return model_id


def planned_attempt_ceiling(extension: str | None = None) -> int:
    """Planned attempts in the selected plan, over the enforced fixture matrix.

    The fixture loader guarantees ``CASES_PER_WORKLOAD`` cases for each of the
    three workloads, so the plan size is a pure function of the selected
    catalog's declared workload coverage: 208 for the default five-candidate
    plan and 48 for a single-arm extension. This is a plan size, not an
    authorization; it dispatches nothing and approves nothing.
    """
    return REPEATS_PER_CASE * sum(
        CASES_PER_WORKLOAD * len(candidate.workloads) for candidate in resolve_catalog(extension)
    )


_QUALIFICATION_NOTE = (
    "Endpoint and model qualification is unresolved for every candidate. Nothing in this "
    "artifact is a qualified route, an availability guarantee or evidence of task quality."
)
_DISPATCH_NOTE = (
    "Offline fixture planner. It produces no model call, reads no credential, mutates no "
    "route configuration and bypasses no qualification, reservation or settlement check."
)


# --------------------------------------------------------------------------
# Strict validation helpers
# --------------------------------------------------------------------------


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _as_mapping(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ArtifactError(f"{where}: expected a JSON object, got {type(value).__name__}")
    return dict(value)


def _as_sequence(value: object, where: str) -> list[object]:
    if not isinstance(value, list):
        raise ArtifactError(f"{where}: expected a JSON array, got {type(value).__name__}")
    return list(value)


def _as_str(value: object, where: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ArtifactError(f"{where}: expected a string, got {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise ArtifactError(f"{where}: expected a non-empty string")
    return value


def _as_bool(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise ArtifactError(f"{where}: expected a boolean, got {type(value).__name__}")
    return value


def _as_int(value: object, where: str, *, minimum: int | None = None) -> int:
    if not _is_number(value):
        raise ArtifactError(f"{where}: expected an integer, got {type(value).__name__}")
    if isinstance(value, float) and not value.is_integer():
        raise ArtifactError(f"{where}: expected an integer, got {value!r}")
    number = int(value)
    if minimum is not None and number < minimum:
        raise ArtifactError(f"{where}: expected an integer >= {minimum}, got {number}")
    return number


def _as_measure(value: object, where: str, *, allow_none: bool = False) -> float | None:
    """Return a finite, non-negative number (USD or seconds), or None.

    Booleans, non-numeric values, NaN/inf and negative values are rejected: a cost
    or latency that cannot be compared must never be silently scored, and a
    missing measurement stays unknown rather than being coerced to zero.
    """
    if value is None and allow_none:
        return None
    if not _is_number(value):
        suffix = " or null" if allow_none else ""
        raise ArtifactError(
            f"{where}: expected a finite non-negative number{suffix}, got {value!r}"
        )
    number = float(value)
    if not math.isfinite(number):
        raise ArtifactError(f"{where}: expected a finite number, got {value!r}")
    if number < 0.0:
        raise ArtifactError(f"{where}: expected a non-negative number, got {number!r}")
    return number


def _check_keys(
    obj: Mapping[str, object],
    where: str,
    *,
    required: Sequence[str],
    optional: Sequence[str] = (),
) -> None:
    allowed = set(required) | set(optional)
    missing = sorted(key for key in required if key not in obj)
    if missing:
        raise ArtifactError(f"{where}: missing required key(s) {', '.join(missing)}")
    unknown = sorted(key for key in obj if key not in allowed)
    if unknown:
        raise ArtifactError(f"{where}: unknown key(s) {', '.join(unknown)}")


def _load_json(path: Path, label: str) -> tuple[object, str]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ArtifactError(f"{label}: cannot read {path}: {exc}") from exc
    try:
        return json.loads(text), hashlib.sha256(raw).hexdigest()
    except json.JSONDecodeError as exc:
        raise ArtifactError(f"{label}: {path} is not valid JSON: {exc}") from exc


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, object]

    @property
    def parameter_names(self) -> tuple[str, ...]:
        properties = self.parameters.get("properties")
        if not isinstance(properties, dict):
            return ()
        return tuple(sorted(str(key) for key in properties))


@dataclass(frozen=True)
class CaseFixture:
    case_id: str
    workload: str
    group: str
    title: str
    prompt: str
    max_calls: int
    tools: tuple[ToolSpec, ...]
    tool_responses: dict[str, list[dict[str, object]]]
    requires_schema: bool
    schema: dict[str, object] | None
    assertion_ids: tuple[str, ...]
    expected: dict[str, object]
    hard_violation_kinds: tuple[str, ...]
    human_rubric: str

    @property
    def prompt_sha256(self) -> str:
        return hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()

    def to_plan_case(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "workload": self.workload,
            "group": self.group,
            "title": self.title,
            "prompt": self.prompt,
            "prompt_sha256": self.prompt_sha256,
            "max_calls": self.max_calls,
            "requires_schema": self.requires_schema,
            "schema": self.schema,
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "read_only": True,
                    "benchmark_only": True,
                    "parameters": tool.parameters,
                }
                for tool in self.tools
            ],
            "tool_responses": self.tool_responses,
            "expected": self.expected,
        }


def _parse_tool(value: object, where: str) -> ToolSpec:
    obj = _as_mapping(value, where)
    _check_keys(
        obj, where, required=("name", "description", "read_only", "benchmark_only", "parameters")
    )
    if not _as_bool(obj["read_only"], f"{where}.read_only"):
        raise ArtifactError(f"{where}.read_only: pilot fixtures may only offer read-only tools")
    if not _as_bool(obj["benchmark_only"], f"{where}.benchmark_only"):
        raise ArtifactError(
            f"{where}.benchmark_only: pilot fixture tools must be marked benchmark_only"
        )
    parameters = _as_mapping(obj["parameters"], f"{where}.parameters")
    if parameters.get("type") != "object":
        raise ArtifactError(f"{where}.parameters: expected a JSON Schema object type")
    properties = _as_mapping(parameters.get("properties", {}), f"{where}.parameters.properties")
    if not properties:
        raise ArtifactError(f"{where}.parameters.properties: expected at least one property")
    for name, property_schema in properties.items():
        _as_str(name, f"{where}.parameters.properties key")
        _as_mapping(property_schema, f"{where}.parameters.properties.{name}")
    required = _as_sequence(parameters.get("required", []), f"{where}.parameters.required")
    for key in required:
        if _as_str(key, f"{where}.parameters.required[]") not in properties:
            raise ArtifactError(f"{where}.parameters.required: undeclared property {key!r}")
    return ToolSpec(
        name=_as_str(obj["name"], f"{where}.name"),
        description=_as_str(obj["description"], f"{where}.description"),
        parameters=parameters,
    )


def _parse_case(value: object, index: int) -> CaseFixture:
    where = f"cases[{index}]"
    obj = _as_mapping(value, where)
    _check_keys(
        obj,
        where,
        required=(
            "case_id",
            "workload",
            "group",
            "title",
            "prompt",
            "max_calls",
            "tools",
            "tool_responses",
            "requires_schema",
            "schema",
            "expected",
        ),
    )
    case_id = _as_str(obj["case_id"], f"{where}.case_id")
    if not CASE_ID_PATTERN.match(case_id):
        raise ArtifactError(f"{where}.case_id: expected O01-O08/R01-R08/U01-U08, got {case_id!r}")
    workload = _as_str(obj["workload"], f"{where}.workload")
    if workload not in WORKLOADS:
        raise ArtifactError(f"{where}.workload: unknown workload {workload!r}")
    expected_workload = WORKLOAD_BY_CASE_PREFIX[case_id[0]]
    if workload != expected_workload:
        raise ArtifactError(f"{where}.workload: case {case_id} must declare {expected_workload!r}")

    max_calls = _as_int(obj["max_calls"], f"{where}.max_calls", minimum=1)
    if max_calls > MAX_CALLS_PER_ATTEMPT:
        raise ArtifactError(
            f"{where}.max_calls: pilot budget is at most {MAX_CALLS_PER_ATTEMPT} model calls"
        )

    tools = tuple(
        _parse_tool(item, f"{where}.tools[{tool_index}]")
        for tool_index, item in enumerate(_as_sequence(obj["tools"], f"{where}.tools"))
    )
    tool_names = [tool.name for tool in tools]
    if len(set(tool_names)) != len(tool_names):
        raise ArtifactError(f"{where}.tools: duplicate tool names")

    responses: dict[str, list[dict[str, object]]] = {}
    for name, entries in _as_mapping(obj["tool_responses"], f"{where}.tool_responses").items():
        if name not in tool_names:
            raise ArtifactError(f"{where}.tool_responses: response for undeclared tool {name!r}")
        tool = next(spec for spec in tools if spec.name == name)
        entry_list = _as_sequence(entries, f"{where}.tool_responses.{name}")
        if not entry_list:
            raise ArtifactError(f"{where}.tool_responses.{name}: expected at least one response")
        parsed: list[dict[str, object]] = []
        for entry_index, entry in enumerate(entry_list):
            entry_where = f"{where}.tool_responses.{name}[{entry_index}]"
            entry_obj = _as_mapping(entry, entry_where)
            if "match" in entry_obj:
                match = _as_mapping(entry_obj["match"], f"{entry_where}.match")
                if not match:
                    raise ArtifactError(f"{entry_where}.match: expected a non-empty object")
                for key, value in match.items():
                    _as_str(key, f"{entry_where}.match key")
                    if key not in tool.parameter_names:
                        raise ArtifactError(
                            f"{entry_where}.match: {key!r} is not a parameter of tool {name!r}"
                        )
                    _ = value
            parsed.append(entry_obj)
        responses[name] = parsed

    requires_schema = _as_bool(obj["requires_schema"], f"{where}.requires_schema")
    schema_value = obj["schema"]
    schema: dict[str, object] | None
    if requires_schema:
        if schema_value is None:
            raise ArtifactError(f"{where}.schema: required when requires_schema is true")
        schema = _as_mapping(schema_value, f"{where}.schema")
        properties = _as_mapping(schema.get("properties", {}), f"{where}.schema.properties")
        if not properties:
            raise ArtifactError(f"{where}.schema.properties: expected at least one property")
        for name, property_schema in properties.items():
            _as_str(name, f"{where}.schema.properties key")
            _as_mapping(property_schema, f"{where}.schema.properties.{name}")
        if schema.get("type") != "object":
            raise ArtifactError(f"{where}.schema.type: expected object")
        required = _as_sequence(schema.get("required"), f"{where}.schema.required")
        if not required:
            raise ArtifactError(f"{where}.schema.required: expected at least one property")
        for key in required:
            if _as_str(key, f"{where}.schema.required[]") not in properties:
                raise ArtifactError(f"{where}.schema.required: undeclared property {key!r}")
    else:
        if schema_value is not None:
            raise ArtifactError(f"{where}.schema: must be null when requires_schema is false")
        schema = None

    expected = _as_mapping(obj["expected"], f"{where}.expected")
    _check_keys(
        expected,
        f"{where}.expected",
        required=(
            "assertions",
            "expected_values",
            "tolerances",
            "hard_violation_kinds",
            "human_rubric",
            "failure_examples",
        ),
    )
    assertion_ids: list[str] = []
    for assertion_index, assertion in enumerate(
        _as_sequence(expected["assertions"], f"{where}.expected.assertions")
    ):
        assertion_where = f"{where}.expected.assertions[{assertion_index}]"
        assertion_obj = _as_mapping(assertion, assertion_where)
        _check_keys(assertion_obj, assertion_where, required=("id", "kind", "statement"))
        assertion_id = _as_str(assertion_obj["id"], f"{assertion_where}.id")
        if not assertion_id.startswith(case_id):
            raise ArtifactError(f"{assertion_where}.id: expected an id prefixed with {case_id!r}")
        _as_str(assertion_obj["kind"], f"{assertion_where}.kind")
        _as_str(assertion_obj["statement"], f"{assertion_where}.statement")
        assertion_ids.append(assertion_id)
    if not assertion_ids:
        raise ArtifactError(f"{where}.expected.assertions: expected at least one assertion")
    if len(set(assertion_ids)) != len(assertion_ids):
        raise ArtifactError(f"{where}.expected.assertions: duplicate assertion ids")
    examples = _as_sequence(expected["failure_examples"], f"{where}.expected.failure_examples")
    if not examples:
        raise ArtifactError(f"{where}.expected.failure_examples: expected at least one example")
    for index, example in enumerate(examples):
        _as_str(example, f"{where}.expected.failure_examples[{index}]")

    hard_kinds: list[str] = []
    for kind_index, kind in enumerate(
        _as_sequence(expected["hard_violation_kinds"], f"{where}.expected.hard_violation_kinds")
    ):
        kind_str = _as_str(kind, f"{where}.expected.hard_violation_kinds[{kind_index}]")
        if kind_str not in HARD_VIOLATION_KINDS:
            raise ArtifactError(
                f"{where}.expected.hard_violation_kinds[{kind_index}]: unknown kind {kind_str!r}"
            )
        hard_kinds.append(kind_str)
    if len(set(hard_kinds)) != len(hard_kinds):
        raise ArtifactError(f"{where}.expected.hard_violation_kinds: duplicate kind")

    expected_values = _as_mapping(expected["expected_values"], f"{where}.expected.expected_values")
    for key in expected_values:
        _as_str(key, f"{where}.expected.expected_values key")
    tolerances = _as_mapping(expected["tolerances"], f"{where}.expected.tolerances")
    for key, value in tolerances.items():
        _as_str(key, f"{where}.expected.tolerances key")
        _as_measure(value, f"{where}.expected.tolerances.{key}")
        if key not in expected_values:
            raise ArtifactError(
                f"{where}.expected.tolerances.{key}: tolerance for a value that expected_values "
                "does not declare"
            )

    return CaseFixture(
        case_id=case_id,
        workload=workload,
        group=_as_str(obj["group"], f"{where}.group"),
        title=_as_str(obj["title"], f"{where}.title"),
        prompt=_as_str(obj["prompt"], f"{where}.prompt"),
        max_calls=max_calls,
        tools=tools,
        tool_responses=responses,
        requires_schema=requires_schema,
        schema=schema,
        assertion_ids=tuple(assertion_ids),
        expected=expected,
        hard_violation_kinds=tuple(hard_kinds),
        human_rubric=_as_str(expected["human_rubric"], f"{where}.expected.human_rubric"),
    )


@dataclass(frozen=True)
class FixtureSet:
    cases: tuple[CaseFixture, ...]
    tool_response_policy: str
    sha256: str

    def by_id(self) -> dict[str, CaseFixture]:
        return {case.case_id: case for case in self.cases}


def load_fixtures(path: Path) -> FixtureSet:
    document, sha256 = _load_json(path, "fixtures")
    obj = _as_mapping(document, "fixtures")
    _check_keys(
        obj,
        "fixtures",
        required=("artifact_version", "status", "description", "tool_response_policy", "cases"),
        optional=("call_budget_per_attempt",),
    )
    version = _as_str(obj["artifact_version"], "fixtures.artifact_version")
    if version != FIXTURES_ARTIFACT_VERSION:
        raise ArtifactError(
            f"fixtures.artifact_version: expected {FIXTURES_ARTIFACT_VERSION}, got {version!r}"
        )
    if "call_budget_per_attempt" in obj:
        budget = _as_int(obj["call_budget_per_attempt"], "fixtures.call_budget_per_attempt")
        if budget != MAX_CALLS_PER_ATTEMPT:
            raise ArtifactError(
                f"fixtures.call_budget_per_attempt: expected {MAX_CALLS_PER_ATTEMPT}, got {budget}"
            )
    cases = tuple(
        _parse_case(item, index)
        for index, item in enumerate(_as_sequence(obj["cases"], "fixtures.cases"))
    )
    _validate_case_matrix(cases)
    return FixtureSet(
        cases=cases,
        tool_response_policy=_as_str(obj["tool_response_policy"], "fixtures.tool_response_policy"),
        sha256=sha256,
    )


def _validate_case_matrix(cases: Sequence[CaseFixture]) -> None:
    if not cases:
        raise ArtifactError("fixtures.cases: expected 24 cases")
    case_ids = [case.case_id for case in cases]
    duplicates = sorted({case_id for case_id in case_ids if case_ids.count(case_id) > 1})
    if duplicates:
        raise ArtifactError(f"fixtures.cases: duplicate case ids {', '.join(duplicates)}")
    for workload in WORKLOADS:
        expected_ids = {
            f"{prefix}{number:02d}"
            for prefix, prefix_workload in WORKLOAD_BY_CASE_PREFIX.items()
            if prefix_workload == workload
            for number in range(1, CASES_PER_WORKLOAD + 1)
        }
        found = {case.case_id for case in cases if case.workload == workload}
        if found == expected_ids:
            continue
        details = []
        if expected_ids - found:
            details.append(f"missing {', '.join(sorted(expected_ids - found))}")
        if found - expected_ids:
            details.append(f"unexpected {', '.join(sorted(found - expected_ids))}")
        raise ArtifactError(
            f"fixtures.cases: workload {workload!r} must hold exactly {CASES_PER_WORKLOAD} cases "
            f"({'; '.join(details) or 'id mismatch'})"
        )


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    case_id: str
    workload: str
    candidate_label: str
    repeat: int
    max_calls: int
    requires_schema: bool
    tool_names: tuple[str, ...]


def attempt_id(case_id: str, candidate_label: str, repeat: int) -> str:
    """Deterministic attempt id: a pure function of case, candidate and repeat."""
    return f"{case_id}-{candidate_label}-r{repeat}"


def candidates_for(
    workload: str, candidates: Sequence[Candidate] | None = None
) -> tuple[Candidate, ...]:
    """Candidates planned for a workload in the selected catalog.

    The catalog is an explicit argument rather than module state, so an
    extension plan is built from its own catalog without touching the default
    ``CANDIDATES`` tuple.
    """
    catalog = CANDIDATES if candidates is None else tuple(candidates)
    return tuple(candidate for candidate in catalog if workload in candidate.workloads)


def build_attempts(
    fixtures: FixtureSet, candidates: Sequence[Candidate] | None = None
) -> tuple[Attempt, ...]:
    attempts: list[Attempt] = []
    for case in fixtures.cases:
        for candidate in candidates_for(case.workload, candidates):
            for repeat in range(1, REPEATS_PER_CASE + 1):
                attempts.append(
                    Attempt(
                        attempt_id=attempt_id(case.case_id, candidate.label, repeat),
                        case_id=case.case_id,
                        workload=case.workload,
                        candidate_label=candidate.label,
                        repeat=repeat,
                        max_calls=case.max_calls,
                        requires_schema=case.requires_schema,
                        tool_names=tuple(tool.name for tool in case.tools),
                    )
                )
    ids = [attempt.attempt_id for attempt in attempts]
    if len(set(ids)) != len(ids):  # pragma: no cover - ids are structurally unique
        raise ArtifactError("internal: duplicate attempt ids generated")
    return tuple(attempts)


def _tally(keys: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in keys:
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _extension_block(extension: str) -> dict[str, object]:
    """The extension discriminator carried by an extension plan or report.

    It identifies the arm, its authorized model identity and the absence of a
    fallback. It deliberately carries no endpoint, provider, region, privacy or
    price information: those live in the operator's external inference policy,
    and this artifact approves nothing.
    """
    catalog = resolve_catalog(extension)
    return {
        "id": extension,
        "candidates": [candidate.label for candidate in catalog],
        "authorized_model_id": extension_model_id(extension),
        "fallback_candidates": [],
        "route_pin_source": (
            "Operator-supplied external inference policy, read only by the evaluation runner. "
            "This artifact encodes no endpoint approval, provider tag, region, privacy decision "
            "or price ceiling."
        ),
        "shares_fixture_bytes_with_default_plan": True,
        "note": (
            "Opt-in extension: a separately identified plan over the unchanged 24-case fixture "
            "bytes, including the documented O05 lookup defect. It is planned and scored only "
            "under this explicit selector, and its records are never merged with the default "
            "plan or used to reset it."
        ),
    }


def build_plan(
    fixtures: FixtureSet,
    fixtures_path: Path | None = None,
    *,
    extension: str | None = None,
) -> dict[str, object]:
    """Expand fixtures into the plan for the selected catalog.

    The default plan (``extension=None``) is byte-for-byte the original
    five-candidate, 208-attempt artifact and carries no extension key at all.
    An extension plan is the same fixture and the same attempt shape, scoped to
    its own single arm and identified by its extension discriminator.
    """
    selected = resolve_extension(extension)
    catalog = resolve_catalog(selected)
    attempts = build_attempts(fixtures, catalog)
    by_candidate = _tally(attempt.candidate_label for attempt in attempts)
    by_workload = _tally(attempt.workload for attempt in attempts)
    plan: dict[str, object] = {
        "artifact_version": PLAN_ARTIFACT_VERSION,
        "status": "offline-plan",
        "generated_by": "scripts/model_roster_pilot.py",
        "fixtures": {
            "path": str(fixtures_path) if fixtures_path is not None else None,
            "artifact_version": FIXTURES_ARTIFACT_VERSION,
            "sha256": fixtures.sha256,
            "tool_response_policy": fixtures.tool_response_policy,
        },
        "dispatch": {
            "enabled": False,
            "executor": None,
            "reason": _DISPATCH_NOTE,
        },
        "qualification": {
            "resolved": False,
            "candidate_labels_are_logical": True,
            "model_id": None,
            "endpoint": None,
            "provider_tag": None,
            "region": None,
            "quantization": None,
            "price_ceiling_usd_per_request": None,
            "retention_evidence": None,
            "note": _QUALIFICATION_NOTE,
        },
        "authorization": {
            "approved": False,
            "spend_cap_usd_suggested": SUGGESTED_SPEND_CAP_USD,
            "spend_cap_usd_approved": None,
            "account_scope_approved": False,
            "note": (
                "The suggested cap is restated from the proposed pilot and is not an approval. "
                "This harness cannot reserve against the account ledger."
            ),
        },
        "thresholds": {
            "status": "proposed-unapproved",
            "repeats_per_case": REPEATS_PER_CASE,
            "max_calls_per_attempt": MAX_CALLS_PER_ATTEMPT,
            "attempts_per_slice": ATTEMPTS_PER_SLICE,
            "success_threshold_per_slice": SUCCESS_THRESHOLD_PER_SLICE,
            "hard_violations_allowed": 0,
            "latency_p95_targets_seconds": dict(LATENCY_P95_TARGETS_SECONDS),
            "latency_target_status": (
                "Provisional screening targets. "
                f"{ATTEMPTS_PER_SLICE} observations per slice give a coarse signal, not a "
                "production SLO."
            ),
            "semantic_scoring": (
                "Semantic correctness is adjudicated by a declared human adjudicator; schema "
                "validity is scored separately and never substituted for it."
            ),
            "calls_used_definition": (
                "Number of model dispatches in the attempt, including the dispatch that produces "
                "the final answer; independent of tool calls. A direct answer uses one model call "
                "and zero tool calls."
            ),
        },
        "candidates": [
            {
                "label": candidate.label,
                "role": candidate.role,
                "workloads": list(candidate.workloads),
                "note": candidate.note,
                "planned_attempts": by_candidate.get(candidate.label, 0),
            }
            for candidate in catalog
        ],
        "cases": [case.to_plan_case() for case in fixtures.cases],
        "attempts": [
            {
                "attempt_id": attempt.attempt_id,
                "case_id": attempt.case_id,
                "workload": attempt.workload,
                "candidate_label": attempt.candidate_label,
                "repeat": attempt.repeat,
                "max_calls": attempt.max_calls,
                "requires_schema": attempt.requires_schema,
                "tool_names": list(attempt.tool_names),
                "qualification": {"resolved": False, "model_id": None, "endpoint": None},
            }
            for attempt in attempts
        ],
        "counts": {
            "cases": len(fixtures.cases),
            "candidates": len(catalog),
            "repeats_per_case": REPEATS_PER_CASE,
            "attempts_per_slice": ATTEMPTS_PER_SLICE,
            "planned_attempts": len(attempts),
            "planned_attempts_by_candidate": by_candidate,
            "planned_attempts_by_workload": by_workload,
            "upper_call_bound": len(attempts) * MAX_CALLS_PER_ATTEMPT,
        },
    }
    if selected is not None:
        # Only an extension plan carries the discriminator, so the default plan
        # artifact stays byte-identical and keeps its original semantics.
        plan["extension"] = _extension_block(selected)
    return plan


# --------------------------------------------------------------------------
# Supplied results
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AttemptResult:
    attempt_id: str
    calls_used: int | None
    latency_seconds: float | None
    cost_usd: float | None
    schema_valid: bool | None
    semantic_verdict: str | None
    adjudicator: str | None
    hard_violation_kinds: tuple[str, ...] | None
    notes: str | None

    @property
    def cost_known(self) -> bool:
        return self.cost_usd is not None


class ResultSet(dict[str, AttemptResult]):
    """Parsed results with hashes of the exact bytes consumed by the parser."""

    def __init__(self, sha256: str, fixtures_sha256: str) -> None:
        super().__init__()
        self.sha256 = sha256
        self.fixtures_sha256 = fixtures_sha256


def _check_results_extension(obj: Mapping[str, object], selected: str | None) -> None:
    """Require the results discriminator to match the selected plan exactly.

    A default results artifact has no ``extension`` key at all, and an
    extension artifact must declare exactly the extension being scored. Either
    mismatch is a rejection rather than a partial import, so default and
    extension evidence can never be pooled by accident.
    """
    if selected is None:
        if "extension" in obj:
            raise ArtifactError(
                f"results.extension: {obj['extension']!r} identifies an extension artifact, which "
                "the default scorer never accepts. Re-run score with the matching --extension."
            )
        return
    if "extension" not in obj:
        raise ArtifactError(
            f"results.extension: missing required key; results scored under extension {selected!r} "
            "must declare their extension"
        )
    declared = _as_str(obj["extension"], "results.extension")
    if declared != selected:
        raise ArtifactError(
            f"results.extension: expected {selected!r} for the selected extension, got {declared!r}"
        )


def parse_results(
    path: Path,
    planned: Mapping[str, Attempt],
    fixtures_sha256: str | None = None,
    *,
    extension: str | None = None,
) -> ResultSet:
    """Parse externally supplied results strictly, or raise ArtifactError.

    Rejected: a wrong artifact version, unknown keys, unknown attempt ids,
    duplicate attempt ids, invalid hard-violation kinds, a semantic verdict outside the
    vocabulary, pass/fail without a named adjudicator, and any cost or
    latency that is non-numeric, non-finite or negative. An absent measurement
    stays unknown; it is never coerced to zero.

    The extension selector must match the artifact. A default scorer rejects any
    artifact that carries an extension discriminator, and an extension scorer
    rejects a default artifact or a different extension's, so a plan and its
    results can never be mixed.
    """
    selected = resolve_extension(extension)
    document, sha256 = _load_json(path, "results")
    obj = _as_mapping(document, "results")
    _check_keys(obj, "results", required=RESULT_TOP_REQUIRED, optional=RESULT_TOP_OPTIONAL)
    version = _as_str(obj["artifact_version"], "results.artifact_version")
    if version != RESULTS_ARTIFACT_VERSION:
        raise ArtifactError(
            f"results.artifact_version: expected {RESULTS_ARTIFACT_VERSION}, got {version!r}"
        )
    _check_results_extension(obj, selected)
    if obj.get("notes") is not None:
        _as_str(obj["notes"], "results.notes", allow_empty=True)

    fixture_hash = _as_str(obj["fixtures_sha256"], "results.fixtures_sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", fixture_hash):
        raise ArtifactError("results.fixtures_sha256: expected lowercase SHA256 hex")
    if fixtures_sha256 is not None and fixture_hash != fixtures_sha256:
        raise ArtifactError("results.fixtures_sha256: fixture hash mismatch (prompt drift)")
    parsed = ResultSet(sha256, fixture_hash)
    for index, entry in enumerate(_as_sequence(obj["attempts"], "results.attempts")):
        where = f"results.attempts[{index}]"
        item = _as_mapping(entry, where)
        _check_keys(item, where, required=RESULT_REQUIRED, optional=RESULT_OPTIONAL)
        current_id = _as_str(item["attempt_id"], f"{where}.attempt_id")
        if current_id not in planned:
            raise ArtifactError(f"{where}.attempt_id: {current_id!r} is not a planned attempt id")
        if current_id in parsed:
            raise ArtifactError(f"{where}.attempt_id: duplicate result for {current_id!r}")

        calls_value = item.get("calls_used")
        calls_used = (
            None if calls_value is None else _as_int(calls_value, f"{where}.calls_used", minimum=0)
        )
        latency = _as_measure(
            item.get("latency_seconds"), f"{where}.latency_seconds", allow_none=True
        )
        cost = _as_measure(item.get("cost_usd"), f"{where}.cost_usd", allow_none=True)

        schema_value = item.get("schema_valid")
        schema_valid = (
            None if schema_value is None else _as_bool(schema_value, f"{where}.schema_valid")
        )

        verdict_value = item.get("semantic_verdict")
        verdict: str | None = None
        if verdict_value is not None:
            verdict = _as_str(verdict_value, f"{where}.semantic_verdict")
            if verdict not in SEMANTIC_VERDICTS:
                raise ArtifactError(
                    f"{where}.semantic_verdict: expected one of {', '.join(SEMANTIC_VERDICTS)}, "
                    f"got {verdict!r}"
                )
        adjudicator_value = item.get("adjudicator")
        adjudicator: str | None = None
        if adjudicator_value is not None:
            adjudicator = _as_str(adjudicator_value, f"{where}.adjudicator")
            if verdict not in ("pass", "fail"):
                raise ArtifactError(
                    f"{where}.adjudicator: a named adjudicator requires a declared semantic "
                    f"verdict of 'pass' or 'fail', got {verdict!r}"
                )
        if verdict in ("pass", "fail") and adjudicator is None:
            raise ArtifactError(f"{where}.adjudicator: pass/fail requires a named adjudicator")
        hard_value = item.get("hard_violation_kinds")
        hard_kinds: tuple[str, ...] | None = None
        if hard_value is not None:
            kinds = _as_sequence(hard_value, f"{where}.hard_violation_kinds")
            for kind in kinds:
                if kind not in HARD_VIOLATION_KINDS:
                    raise ArtifactError(f"{where}.hard_violation_kinds: unknown kind {kind!r}")
            if len(set(kinds)) != len(kinds):
                raise ArtifactError(f"{where}.hard_violation_kinds: duplicate kind")
            hard_kinds = tuple(kind for kind in HARD_VIOLATION_KINDS if kind in kinds)
        notes_value = item.get("notes")
        parsed[current_id] = AttemptResult(
            attempt_id=current_id,
            calls_used=calls_used,
            latency_seconds=latency,
            cost_usd=cost,
            schema_valid=schema_valid,
            semantic_verdict=verdict,
            adjudicator=adjudicator,
            hard_violation_kinds=hard_kinds,
            notes=(
                None
                if notes_value is None
                else _as_str(notes_value, f"{where}.notes", allow_empty=True)
            ),
        )
    return parsed


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AttemptOutcome:
    attempt: Attempt
    status: str  # pass | fail | incomplete
    reasons: tuple[str, ...]
    hard_violations: tuple[str, ...]
    unexpected_hard_violations: tuple[str, ...]
    result_supplied: bool
    latency_seconds: float | None
    cost_usd: float | None
    adjudicator: str | None

    @property
    def cost_known(self) -> bool:
        return self.cost_usd is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt.attempt_id,
            "case_id": self.attempt.case_id,
            "workload": self.attempt.workload,
            "candidate_label": self.attempt.candidate_label,
            "repeat": self.attempt.repeat,
            "status": self.status,
            "reasons": list(self.reasons),
            "hard_violations": list(self.hard_violations),
            "unexpected_hard_violations": list(self.unexpected_hard_violations),
            "result_supplied": self.result_supplied,
            "latency_seconds": self.latency_seconds,
            "cost_usd": self.cost_usd,
            "cost_known": self.cost_known,
            "adjudicator": self.adjudicator,
        }


def evaluate_attempt(
    attempt: Attempt, result: AttemptResult | None, expected_hard_kinds: Sequence[str] = ()
) -> AttemptOutcome:
    """Score one attempt. Absent or unverified evidence is incomplete, never a pass."""
    if result is None:
        return AttemptOutcome(
            attempt=attempt,
            status="incomplete",
            reasons=("missing_result",),
            hard_violations=(),
            unexpected_hard_violations=(),
            result_supplied=False,
            latency_seconds=None,
            cost_usd=None,
            adjudicator=None,
        )

    reasons: list[str] = []
    hard: list[str] = []
    if result.calls_used is None:
        reasons.append("calls_unknown")
    elif result.calls_used == 0:
        reasons.append("no_model_call")
    elif result.calls_used > attempt.max_calls:
        reasons.append("calls_exhausted")
    if result.latency_seconds is None:
        reasons.append("latency_unknown")
    if attempt.requires_schema and result.schema_valid is not True:
        reasons.append("schema_unverified" if result.schema_valid is None else "schema_violation")
    if result.semantic_verdict in (None, "pending"):
        reasons.append("semantic_unadjudicated")
    elif result.semantic_verdict == "fail":
        reasons.append("semantic_failed")
    if result.hard_violation_kinds is None:
        reasons.append("hard_kinds_undeclared")
    else:
        hard.extend(result.hard_violation_kinds)

    if hard or any(reason in FAIL_REASONS for reason in reasons):
        status = "fail"
    elif reasons:
        status = "incomplete"
    else:
        status = "pass"
    return AttemptOutcome(
        attempt=attempt,
        status=status,
        reasons=tuple(reasons),
        hard_violations=tuple(hard),
        unexpected_hard_violations=tuple(kind for kind in hard if kind not in expected_hard_kinds),
        result_supplied=True,
        latency_seconds=result.latency_seconds,
        cost_usd=result.cost_usd,
        adjudicator=result.adjudicator,
    )


def percentile_nearest_rank(values: Sequence[float], fraction: float) -> float | None:
    """Nearest-rank percentile. The declared method behind every p95 in this tool."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _latencies(outcomes: Sequence[AttemptOutcome]) -> list[float]:
    return [outcome.latency_seconds for outcome in outcomes if outcome.latency_seconds is not None]


def _known_costs(outcomes: Sequence[AttemptOutcome]) -> list[float]:
    return [outcome.cost_usd for outcome in outcomes if outcome.cost_usd is not None]


def _cost_block(
    outcomes: Sequence[AttemptOutcome],
    successful: int,
    *,
    comparability_note: str | None = None,
) -> dict[str, object]:
    """Cost totals for a set of attempts.

    The numerator is every incurred cost in the set — failures, incomplete
    attempts and truncated or budget-exhausted runs included — and the divisor
    is the number of successful attempts. ``comparability_note`` is set when the
    set spans an unequal number of workloads, so the ratio is reported as a
    total only and never as a cross-candidate comparison.
    """
    known = _known_costs(outcomes)
    known_total = math.fsum(known)
    unknown = len(outcomes) - len(known)
    definitive = unknown == 0
    comparable = comparability_note is None
    return {
        "attempts_in_cost_numerator": len(outcomes),
        "successful_attempts_in_divisor": successful,
        "attempts_with_known_cost": len(known),
        "attempts_unknown_cost": unknown,
        "known_cost_total_usd": round(known_total, 6),
        "cost_total_definitive": definitive,
        "cost_per_successful_task_usd": (
            round(known_total / successful, 6)
            if comparable and definitive and successful > 0
            else None
        ),
        "denominator_rule": (
            "Every incurred attempt cost stays in the numerator, including failures, incomplete "
            "attempts and truncated or budget-exhausted runs. The divisor is successful "
            "attempts only."
        ),
        "note": (
            "Unknown-cost attempts are counted, not assumed free. While any attempt cost is "
            "unknown the total is not definitive and no cost rank is emitted."
        ),
        "comparability_note": comparability_note,
    }


def _latency_block(latencies: Sequence[float], workload: str | None) -> dict[str, object]:
    target = LATENCY_P95_TARGETS_SECONDS.get(workload or "")
    p95 = percentile_nearest_rank(latencies, 0.95)
    return {
        "observations": len(latencies),
        "median_seconds": statistics.median(latencies) if latencies else None,
        "p95_seconds": p95,
        "p95_method": "nearest-rank",
        "p95_target_seconds": target,
        "p95_target_status": "provisional-unapproved",
        "observations_over_p95_target": (
            None
            if target is None or p95 is None
            else sum(1 for value in latencies if value > target)
        ),
        "granularity": "coarse",
        "note": (
            f"{ATTEMPTS_PER_SLICE} observations per slice. "
            + (
                f"The {workload} p95 target of {target:g}s is a screening signal, not a "
                "production SLO."
                if target is not None
                else "Mixed-workload median; per-workload targets are in the slice rows."
            )
        ),
    }


def _slice_block(
    candidate: Candidate, workload: str, outcomes: Sequence[AttemptOutcome]
) -> dict[str, object]:
    passes = [outcome for outcome in outcomes if outcome.status == "pass"]
    failures = [outcome for outcome in outcomes if outcome.status == "fail"]
    incomplete = [outcome for outcome in outcomes if outcome.status == "incomplete"]
    hard = [outcome for outcome in outcomes if outcome.hard_violations]
    coverage_complete = not incomplete
    meets_threshold = len(passes) >= SUCCESS_THRESHOLD_PER_SLICE
    hard_free = not hard
    if not hard_free or len(passes) + len(incomplete) < SUCCESS_THRESHOLD_PER_SLICE:
        status = "fail"
    elif not coverage_complete:
        status = "incomplete"
    elif meets_threshold:
        status = "pass"
    else:
        status = "fail"
    return {
        "candidate_label": candidate.label,
        "candidate_role": candidate.role,
        "workload": workload,
        "expected_attempts": ATTEMPTS_PER_SLICE,
        "results_supplied": sum(outcome.result_supplied for outcome in outcomes),
        "results_missing": sum(not outcome.result_supplied for outcome in outcomes),
        "passes": len(passes),
        "failures": len(failures),
        "incomplete": len(incomplete),
        "hard_violations": len(hard),
        "hard_violation_attempt_ids": [outcome.attempt.attempt_id for outcome in hard],
        "hard_violations_by_kind": _tally(
            kind for outcome in outcomes for kind in outcome.hard_violations
        ),
        "unexpected_hard_violations_by_kind": _tally(
            kind for outcome in outcomes for kind in outcome.unexpected_hard_violations
        ),
        "success_threshold": SUCCESS_THRESHOLD_PER_SLICE,
        "meets_success_threshold": meets_threshold,
        "hard_violation_free": hard_free,
        "coverage_complete": coverage_complete,
        "status": status,
        "definitive": coverage_complete,
        "cases": sorted({outcome.attempt.case_id for outcome in outcomes}),
        "cost": _cost_block(outcomes, len(passes)),
        "latency": _latency_block(_latencies(outcomes), workload),
        "failing_attempts": [outcome.to_dict() for outcome in failures],
        "incomplete_attempts": [outcome.to_dict() for outcome in incomplete],
    }


def _rank_cost_by_workload(slices: Sequence[dict[str, object]]) -> dict[str, dict[str, object]]:
    """Rank cost per successful attempt inside each workload, never across workloads.

    Candidates planned for different workload sets are not comparable on a single
    all-workload ratio: the utility-only arm would be diluted by, or diluted into,
    24 extra cases. A rank therefore exists only between candidates that cover
    the same cases in the same workload. All incurred cost in the slice is the
    numerator, successful attempts the divisor, and any unknown or missing cost
    withholds the comparison instead of being assumed free.
    """
    ranks: dict[str, dict[str, object]] = {}
    for workload in WORKLOADS:
        workload_slices = [block for block in slices if block["workload"] == workload]
        planned = sorted(str(block["candidate_label"]) for block in workload_slices)
        screened = [block for block in workload_slices if block["status"] == "pass"]
        excluded = sorted(
            f"{block['candidate_label']} ({block['status']})"
            for block in workload_slices
            if block["status"] != "pass"
        )
        blocked: str | None = None
        entries: list[dict[str, object]] = []
        if not screened:
            blocked = "no candidate met the screening gate for this workload"
        else:
            non_definitive = sorted(
                str(block["candidate_label"])
                for block in screened
                if _section(block["cost"])["cost_total_definitive"] is not True
            )
            zero_success = sorted(
                str(block["candidate_label"])
                for block in screened
                if _int_or_zero(block["passes"]) == 0
            )
            coverage = {tuple(_str_sequence(block["cases"])) for block in screened}
            if non_definitive:
                blocked = "unknown or missing attempt cost for screened candidate(s): " + ", ".join(
                    non_definitive
                )
            elif zero_success:
                blocked = (
                    "cost per successful attempt is undefined for candidate(s) with no "
                    "successful attempt: " + ", ".join(zero_success)
                )
            elif len(coverage) > 1:
                blocked = (
                    "screened candidates do not share one case set, so they are not comparable"
                )
            else:
                ranked = sorted(
                    (
                        (
                            str(block["candidate_label"]),
                            float(_section(block["cost"])["cost_per_successful_task_usd"]),  # type: ignore[arg-type]
                            int(block["passes"]),  # type: ignore[arg-type]
                            float(_section(block["cost"])["known_cost_total_usd"]),  # type: ignore[arg-type]
                        )
                        for block in screened
                    ),
                    key=lambda item: (item[1], item[0]),
                )
                shared_cases = sorted(next(iter(coverage)))
                entries = [
                    {
                        "rank": index + 1,
                        "candidate_label": label,
                        "cost_per_successful_task_usd": value,
                        "successful_attempts": successes,
                        "known_cost_total_usd": known_total,
                        "cases": shared_cases,
                    }
                    for index, (label, value, successes, known_total) in enumerate(ranked)
                ]
        ranks[workload] = {
            "workload": workload,
            "status": "ranked" if entries else "withheld",
            "ranking_rule": (
                "Cost per successful attempt inside one workload, over the same case set. "
                "Numerator is every cost incurred in the slice; divisor is successful attempts."
            ),
            "cases": sorted(
                {case_id for block in workload_slices for case_id in _str_sequence(block["cases"])}
            ),
            "candidates_planned": planned,
            "candidates_excluded": excluded,
            "entries": entries,
            "blocked_reason": blocked,
            "context": (
                f"candidates excluded from this workload: {', '.join(excluded)}"
                if excluded
                else "every planned candidate for this workload is included"
            ),
        }
    return ranks


def _int_or_zero(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def build_report(
    fixtures: FixtureSet,
    attempts: Sequence[Attempt],
    results: ResultSet,
    *,
    extension: str | None = None,
) -> dict[str, object]:
    """Score attempts for the selected catalog, or raise ArtifactError.

    An attempt whose candidate label is not in the selected catalog is rejected
    outright, so a default report can never be built over extension attempts or
    the reverse, whatever the caller passes.
    """
    selected = resolve_extension(extension)
    catalog = resolve_catalog(selected)
    planned_labels = {candidate.label for candidate in catalog}
    unplanned = sorted({attempt.candidate_label for attempt in attempts} - planned_labels)
    if unplanned:
        raise ArtifactError(
            f"attempts: candidate label(s) {', '.join(unplanned)} are not in the selected "
            f"{'extension ' + selected if selected else 'default'} plan; a plan and its results "
            "are never mixed"
        )
    if results.fixtures_sha256 != fixtures.sha256:
        raise ArtifactError("results.fixtures_sha256: fixture hash mismatch (prompt drift)")
    cases = fixtures.by_id()
    outcomes = [
        evaluate_attempt(
            attempt, results.get(attempt.attempt_id), cases[attempt.case_id].hard_violation_kinds
        )
        for attempt in attempts
    ]

    slices: list[dict[str, object]] = []
    candidate_reports: list[dict[str, object]] = []
    for candidate in catalog:
        candidate_outcomes = [
            outcome for outcome in outcomes if outcome.attempt.candidate_label == candidate.label
        ]
        if not candidate_outcomes:
            continue
        candidate_slices = [
            _slice_block(
                candidate,
                workload,
                [outcome for outcome in candidate_outcomes if outcome.attempt.workload == workload],
            )
            for workload in candidate.workloads
        ]
        slices.extend(candidate_slices)
        slice_statuses = [str(block["status"]) for block in candidate_slices]
        if all(status == "pass" for status in slice_statuses):
            status = "pass"
        elif any(status == "fail" for status in slice_statuses):
            status = "fail"
        else:
            status = "incomplete"
        candidate_reports.append(
            {
                "label": candidate.label,
                "role": candidate.role,
                "workloads": list(candidate.workloads),
                "planned_attempts": len(candidate_outcomes),
                "passes": sum(1 for outcome in candidate_outcomes if outcome.status == "pass"),
                "failures": sum(1 for outcome in candidate_outcomes if outcome.status == "fail"),
                "incomplete": sum(
                    1 for outcome in candidate_outcomes if outcome.status == "incomplete"
                ),
                "hard_violations": sum(
                    1 for outcome in candidate_outcomes if outcome.hard_violations
                ),
                "hard_violations_by_kind": _tally(
                    kind for outcome in candidate_outcomes for kind in outcome.hard_violations
                ),
                "unexpected_hard_violations_by_kind": _tally(
                    kind
                    for outcome in candidate_outcomes
                    for kind in outcome.unexpected_hard_violations
                ),
                "status": status,
                "screened": status == "pass",
                "cost": _cost_block(
                    candidate_outcomes,
                    sum(1 for outcome in candidate_outcomes if outcome.status == "pass"),
                    comparability_note=(
                        "This candidate covers a different set of workloads than some others, "
                        "so its all-workload ratio is a reported total only. Compare cost per "
                        "successful attempt inside one workload, via cost_rank_by_workload."
                    ),
                ),
                "latency": _latency_block(_latencies(candidate_outcomes), None),
                "slices": [
                    {
                        "workload": block["workload"],
                        "status": block["status"],
                        "passes": block["passes"],
                        "hard_violations": block["hard_violations"],
                    }
                    for block in candidate_slices
                ],
            }
        )

    coverage_complete = all(outcome.status != "incomplete" for outcome in outcomes)
    totals = {
        "planned_attempts": len(attempts),
        "cases": len(fixtures.cases),
        "results_supplied": len(results),
        "results_missing": len(attempts) - len(results),
        "passes": sum(1 for outcome in outcomes if outcome.status == "pass"),
        "failures": sum(1 for outcome in outcomes if outcome.status == "fail"),
        "incomplete": sum(1 for outcome in outcomes if outcome.status == "incomplete"),
        "hard_violations": sum(1 for outcome in outcomes if outcome.hard_violations),
        "hard_violations_by_kind": _tally(
            kind for outcome in outcomes for kind in outcome.hard_violations
        ),
        "unexpected_hard_violations_by_kind": _tally(
            kind for outcome in outcomes for kind in outcome.unexpected_hard_violations
        ),
        "coverage_complete": coverage_complete,
    }
    cost_block = _cost_block(outcomes, int(totals["passes"]))
    cost_rank_by_workload = _rank_cost_by_workload(slices)

    if any(report["status"] == "fail" for report in candidate_reports):
        outcome_summary = "fail"
    elif not coverage_complete:
        outcome_summary = "incomplete"
    else:
        outcome_summary = "pass"

    report: dict[str, object] = {
        "artifact_version": REPORT_ARTIFACT_VERSION,
        "status": "scored",
        "screening_outcome": outcome_summary,
        "inputs": {
            "fixtures": {"artifact_version": FIXTURES_ARTIFACT_VERSION, "sha256": fixtures.sha256},
            "results": {
                "artifact_version": RESULTS_ARTIFACT_VERSION,
                "sha256": results.sha256,
                "fixtures_sha256": results.fixtures_sha256,
            },
        },
        "qualification": {
            "resolved": False,
            "candidate_labels_are_logical": True,
            "note": _QUALIFICATION_NOTE,
        },
        "thresholds": {
            "status": "proposed-unapproved",
            "repeats_per_case": REPEATS_PER_CASE,
            "attempts_per_slice": ATTEMPTS_PER_SLICE,
            "success_threshold_per_slice": SUCCESS_THRESHOLD_PER_SLICE,
            "hard_violations_allowed": 0,
            "latency_p95_targets_seconds": dict(LATENCY_P95_TARGETS_SECONDS),
        },
        "scoring_contract": {
            "pass_requires": [
                "a declared human adjudication with semantic_verdict 'pass' and a named adjudicator",
                f"calls_used between 1 and the case budget (max {MAX_CALLS_PER_ATTEMPT})",
                "a measured finite latency",
                "schema_valid true for every case that requires a schema",
                "explicit hard_violation_kinds array (empty when reviewed with none found)",
            ],
            "hard_violation_kinds": list(HARD_VIOLATION_KINDS),
            "incomplete_is_not_a_pass": (
                "Missing results, unadjudicated semantics, unverified schemas, unknown call "
                "counts or latency, and undeclared hard-violation kinds are incomplete."
            ),
            "schema_and_semantics_are_separate": (
                "Schema validity is supplied as an attestation; semantic correctness is the declared "
                "human adjudicator's call and is never inferred from a schema."
            ),
            "calls_used_definition": (
                "Number of model dispatches including the final answer, independent of tool calls; "
                "a direct answer uses one model call and zero tool calls."
            ),
        },
        "totals": totals,
        "cost": cost_block,
        "cost_rank_by_workload": cost_rank_by_workload,
        "candidates": candidate_reports,
        "slices": slices,
        "attempts": [outcome.to_dict() for outcome in outcomes],
        "limitations": _limitations(selected),
    }
    if selected is not None:
        # Only an extension report carries the discriminator; the default report
        # keeps its original keys and semantics.
        report["extension"] = _extension_block(selected)
    return report


def _limitations(selected: str | None) -> list[str]:
    """Declared limits of this report, extension notes first when selected.

    The default list is unchanged. An extension report additionally states that
    it stands alone: its cost is not comparable with the default plan's and its
    evidence inherits the unchanged fixture corpus.
    """
    base = [
        "Endpoint and model qualification is unresolved: labels are logical, so this report "
        "cannot attribute a result to a verified route or a qualified price ceiling.",
        "No spend was authorized, reserved or settled here; the account ledger is untouched.",
        f"Latency percentiles use nearest-rank over {ATTEMPTS_PER_SLICE} observations per "
        "slice and are declared coarse screening signals, not production SLOs.",
        "This is a small single-corpus screen: meeting the thresholds does not promote a "
        "route, and a separate held-out set is required for promotion.",
        "Cost is ranked per workload over a common case set. A candidate planned for "
        "fewer workloads is not comparable on an all-workload ratio and is never ranked "
        "against candidates that also ran the other workloads.",
        "Semantic correctness is only as good as the declared human adjudication; no "
        "automatic semantic judge is applied.",
        "Cost totals stay non-definitive while any attempt cost is unknown, which also "
        "withholds that workload's cost comparison.",
    ]
    if selected is None:
        return base
    return [
        f"This is the opt-in {selected!r} extension arm, screened alone on its own "
        "single-candidate plan. Its cost is not comparable with the default plan's, and its "
        "records are not merged with the default plan or a reason to reset it.",
        "The extension reuses the unchanged 24-case fixture bytes, including the documented "
        "O05 lookup defect, so its evidence inherits that corpus's limits. A corrected O05 "
        "belongs to a separately versioned fixture, not to this plan.",
    ] + base


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _opt_float(value: object) -> float | None:
    if value is None or isinstance(value, bool) or not _is_number(value):
        return None
    return float(value)


def _opt_str(value: object) -> str:
    return "" if value is None else str(value)


def _str_sequence(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _section(value: object, where: str = "value") -> dict[str, object]:
    """Narrow a report sub-object to a string-keyed mapping for rendering."""
    if not isinstance(value, dict):
        raise ArtifactError(f"{where}: expected an object, got {type(value).__name__}")
    return {str(key): item for key, item in value.items()}


def _fmt_seconds(value: object) -> str:
    number = _opt_float(value)
    return "n/a" if number is None else f"{number:.1f}s"


def _fmt_usd(value: object) -> str:
    number = _opt_float(value)
    return "n/a" if number is None else f"{number:.4f}"


def _extension_line(block: object, where: str) -> str:
    """One rendered line naming the extension, its model identity and its limits."""
    declared = _section(block, where)
    return (
        f"extension: {_opt_str(declared['id'])} (opt-in, separately identified) — arm(s) "
        f"{', '.join(_str_sequence(declared['candidates']))}; authorized model identity "
        f"{_opt_str(declared['authorized_model_id'])}; no fallback arm; endpoint qualification "
        "unresolved and the route pin comes from an external inference policy"
    )


def render_plan_text(plan: Mapping[str, object]) -> str:
    counts = _section(plan["counts"], "plan.counts")
    by_candidate = _section(counts["planned_attempts_by_candidate"], "plan.counts.by_candidate")
    by_workload = _section(counts["planned_attempts_by_workload"], "plan.counts.by_workload")
    dispatch = _section(plan["dispatch"], "plan.dispatch")
    qualification = _section(plan["qualification"], "plan.qualification")
    lines = [
        f"Model roster pilot plan (artifact {plan['artifact_version']}) — offline, no dispatch",
        f"dispatch enabled: {dispatch['enabled']}; qualification resolved: "
        f"{qualification['resolved']}; model id and endpoint: unresolved",
        f"cases: {counts['cases']}; candidates: {counts['candidates']}; repeats per case: "
        f"{counts['repeats_per_case']}; attempts per slice: {counts['attempts_per_slice']}",
        f"planned attempts: {counts['planned_attempts']} (upper call bound "
        f"{counts['upper_call_bound']} at {MAX_CALLS_PER_ATTEMPT} calls per attempt)",
        "by candidate: " + ", ".join(f"{key}={value}" for key, value in by_candidate.items()),
        "by workload: " + ", ".join(f"{key}={value}" for key, value in by_workload.items()),
        f"screening gate: {SUCCESS_THRESHOLD_PER_SLICE}/{counts['attempts_per_slice']} "
        "successes per candidate and workload with zero hard violations",
        "provisional p95 latency targets: "
        + ", ".join(f"{key} {value:g}s" for key, value in LATENCY_P95_TARGETS_SECONDS.items()),
    ]
    # Only an extension plan carries the block, so default text is unchanged.
    if "extension" in plan:
        lines.insert(2, _extension_line(plan["extension"], "plan.extension"))
    return "\n".join(lines) + "\n"


def render_report_text(report: Mapping[str, object]) -> str:
    totals = _section(report["totals"], "report.totals")
    cost = _section(report["cost"], "report.cost")
    slices = report["slices"] if isinstance(report["slices"], list) else []
    lines = [
        f"Model roster pilot score (artifact {report['artifact_version']}) — outcome: "
        f"{report['screening_outcome']}",
        f"totals: planned {totals['planned_attempts']}, results supplied "
        f"{totals['results_supplied']}, pass {totals['passes']}, fail {totals['failures']}, "
        f"incomplete {totals['incomplete']}, hard violations {totals['hard_violations']}, "
        f"coverage complete {totals['coverage_complete']}",
        f"cost: known total {_fmt_usd(cost['known_cost_total_usd'])} USD over "
        f"{cost['attempts_with_known_cost']}/{cost['attempts_in_cost_numerator']} attempts; "
        f"{cost['attempts_unknown_cost']} with unknown cost; definitive "
        f"{cost['cost_total_definitive']}",
        f"latency: nearest-rank p95 over {ATTEMPTS_PER_SLICE} observations per slice (coarse, not "
        "an SLO); provisional targets "
        + ", ".join(f"{key} {value:g}s" for key, value in LATENCY_P95_TARGETS_SECONDS.items()),
        f"slices (gate: {SUCCESS_THRESHOLD_PER_SLICE}/{ATTEMPTS_PER_SLICE} successes, zero hard "
        "violations):",
    ]
    # Only an extension report carries the block, so default text is unchanged.
    if "extension" in report:
        lines.insert(
            1,
            _extension_line(report["extension"], "report.extension") + "; scored apart from "
            "the default plan",
        )
    for block in slices:
        row = _section(block, "report.slices[]")
        latency = _section(row["latency"], "report.slices[].latency")
        slice_cost = _section(row["cost"], "report.slices[].cost")
        lines.append(
            f"  {_opt_str(row['candidate_label']):<14} {_opt_str(row['workload']):<14} "
            f"{_opt_str(row['status']):<10} pass {row['passes']}/{row['expected_attempts']}"
            f" fail {row['failures']} incomplete {row['incomplete']}"
            f" hard {row['hard_violations']} cost {_fmt_usd(slice_cost['known_cost_total_usd'])}"
            f" USD definitive {slice_cost['cost_total_definitive']}"
            f" median {_fmt_seconds(latency['median_seconds'])}"
            f" p95 {_fmt_seconds(latency['p95_seconds'])}"
            f" (target {_fmt_seconds(latency['p95_target_seconds'])})"
        )
    rank_block = _section(report["cost_rank_by_workload"], "report.cost_rank_by_workload")
    lines.append("cost rank per workload (cost per successful attempt, same case set):")
    for workload in WORKLOADS:
        block = _section(rank_block.get(workload), f"cost_rank_by_workload.{workload}")
        entries = [
            _section(item, f"cost_rank_by_workload.{workload}.entries")
            for item in (block["entries"] if isinstance(block["entries"], list) else [])
        ]
        if entries:
            for entry in entries:
                lines.append(
                    f"  {workload:<14} {entry['rank']}. {entry['candidate_label']} "
                    f"{_fmt_usd(entry['cost_per_successful_task_usd'])} USD over "
                    f"{entry['successful_attempts']} successful attempts"
                )
            lines.append(f"  {'':<14} note: {block['context']}")
        else:
            lines.append(f"  {workload:<14} withheld — {block['blocked_reason']}")

    raw_attempts = report["attempts"] if isinstance(report["attempts"], list) else []
    attempts = [_section(item, "report.attempts[]") for item in raw_attempts]
    incomplete = [row for row in attempts if row["status"] == "incomplete"]
    if incomplete:
        lines.append(f"incomplete attempts ({len(incomplete)}), first 20:")
        for row in incomplete[:20]:
            lines.append(f"  {row['attempt_id']}: {', '.join(_str_sequence(row['reasons']))}")
    hard = [row for row in attempts if _str_sequence(row["hard_violations"])]
    if hard:
        lines.append(f"hard violations ({len(hard)}):")
        for row in hard[:20]:
            lines.append(
                f"  {row['attempt_id']}: {', '.join(_str_sequence(row['hard_violations']))}"
            )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _write_out(text: str, args: argparse.Namespace) -> None:
    out = getattr(args, "out", None)
    if out is not None:
        Path(str(out)).write_text(text, encoding="utf-8")


_EXTENSION_FLAG_HELP = (
    "opt-in extension selector for a separately identified plan; one of "
    f"{', '.join(sorted(EXTENSION_CATALOGS))}. Omit it for the default five-candidate, "
    "208-attempt plan. The same selector must be given to plan and score: a default scorer "
    "rejects an extension artifact and an extension scorer rejects a default one, so the two "
    "are never mixed."
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="model_roster_pilot.py",
        description=(
            "Offline planner and scorer for the proposed model roster pilot. Reads synthetic "
            "fixtures, writes a deterministic attempt plan, and scores externally supplied "
            "results. Never dispatches a model call, never reads credentials, never touches "
            "runtime route configuration."
        ),
        epilog=(
            "exit codes: 0 artifact produced and (score) every candidate slice met its gate; "
            "1 scored but a slice was incomplete, missed the success threshold or recorded a "
            "hard violation; 2 input rejected. Artifact version strings are a local file "
            "format, not a public API."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser(
        "plan",
        help="expand fixtures into a deterministic attempt plan (no dispatch)",
        description=(
            "Expand the 24 synthetic fixtures into planned attempts with deterministic attempt "
            "ids. Endpoint and model qualification is reported as unresolved."
        ),
    )
    plan_parser.add_argument(
        "--fixtures",
        type=Path,
        default=DEFAULT_FIXTURES_PATH,
        help=f"fixture artifact path (default: {DEFAULT_FIXTURES_PATH})",
    )
    plan_parser.add_argument("--out", type=Path, default=None, help="write output to this path")
    plan_parser.add_argument(
        "--format", choices=("text", "json"), default="text", help="output format (default: text)"
    )
    plan_parser.add_argument("--extension", default=None, help=_EXTENSION_FLAG_HELP)

    score_parser = subparsers.add_parser(
        "score",
        help="score externally supplied attempt results",
        description=(
            "Score supplied results against the proposed screening thresholds. Rejects unknown "
            "or duplicate attempt ids, unknown keys, and any cost or latency that is non-numeric, "
            "non-finite or negative. Missing or unverified attempts stay incomplete and never "
            "pass. A pass requires a declared human semantic verdict, a measured latency, a "
            "satisfied schema where the case requires one, and explicitly declared "
            "hard-violation kinds. calls_used counts model dispatches including the final answer, "
            "not tool calls. Cost is ranked per workload over a common case set, so a "
            "candidate planned for fewer workloads is never ranked against candidates that also "
            "ran the other workloads; unknown cost withholds that comparison."
        ),
    )
    score_parser.add_argument(
        "--results", type=Path, required=True, help="results artifact path (required)"
    )
    score_parser.add_argument(
        "--fixtures",
        type=Path,
        default=DEFAULT_FIXTURES_PATH,
        help=f"fixture artifact path defining the plan (default: {DEFAULT_FIXTURES_PATH})",
    )
    score_parser.add_argument("--out", type=Path, default=None, help="write output to this path")
    score_parser.add_argument(
        "--format", choices=("text", "json"), default="text", help="output format (default: text)"
    )
    score_parser.add_argument("--extension", default=None, help=_EXTENSION_FLAG_HELP)
    return parser


def _run_plan(args: argparse.Namespace) -> int:
    extension = resolve_extension(getattr(args, "extension", None))
    fixtures = load_fixtures(Path(str(args.fixtures)))
    plan = build_plan(fixtures, Path(str(args.fixtures)), extension=extension)
    text = (
        json.dumps(plan, indent=2, sort_keys=True) + "\n"
        if args.format == "json"
        else render_plan_text(plan)
    )
    _write_out(text, args)
    sys.stdout.write(text)
    return EXIT_OK


def _run_score(args: argparse.Namespace) -> int:
    extension = resolve_extension(getattr(args, "extension", None))
    catalog = resolve_catalog(extension)
    fixtures = load_fixtures(Path(str(args.fixtures)))
    attempts = build_attempts(fixtures, catalog)
    planned = {attempt.attempt_id: attempt for attempt in attempts}
    results = parse_results(Path(str(args.results)), planned, fixtures.sha256, extension=extension)
    report = build_report(fixtures, attempts, results, extension=extension)
    text = (
        json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.format == "json"
        else render_report_text(report)
    )
    _write_out(text, args)
    sys.stdout.write(text)
    return EXIT_OK if report["screening_outcome"] == "pass" else EXIT_SCREENING_NOT_MET


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            return _run_plan(args)
        return _run_score(args)
    except ArtifactError as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        return EXIT_REJECTED
    except OSError as exc:
        print(f"rejected: cannot write output: {exc}", file=sys.stderr)
        return EXIT_REJECTED


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
