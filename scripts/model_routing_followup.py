#!/usr/bin/env python3
"""Sequential, evaluation-only runner for the routing follow-up screen.

This runner implements ``docs/MODEL_ROUTING_FOLLOWUP_PLAN.md``. It is a *new,
separately identified* experiment: new case ids, a new corpus version, a new
state identity and a new results discriminator. The baseline roster pilot and the
endpoint-reliability run keep their own fixtures, states and results; nothing
here merges into, resets or replays them.

Deliberate properties, all observable in the state file:

* One exact, independently qualified route per candidate. ``--pins`` must name
  exactly ``luna``, ``sol`` and ``sonnet``; each pin must serve exactly that
  candidate's reviewed model, and exactly one approved OpenRouter route may serve
  that model. Dispatch is pinned with the internal ``_route_id`` seam, so
  provider-side fallback stays disabled and a failure is raised, never walked
  outward.
* Two parameter conditions. ``default`` omits ``reasoning_effort`` entirely and
  requires an isolated catalog with no injected preset; ``explicit`` sends
  the candidate's reviewed effort verbatim. The runner never reads the active
  ``model_routing`` profile to choose either, and it records what it requested
  rather than claiming the two are equivalent.
* A frozen 120-attempt schedule: 72 diagnostic (6 cases x 3 candidates x 2
  conditions x 2 repeats) and 48 held-out (8 cases x 3 candidates x 2 repeats,
  explicit condition only), interleaved so each candidate's first scheduled block
  contains one attempt in *each* condition. The held-out stage cannot start until
  all 72 diagnostic attempts are recorded, and neither stage may be retuned:
  identity pins the fixture bytes, the schedule fingerprint and both policy
  digests.
* Every call is fsynced to durable state *before* it is sent, holds its own
  reservation and settlement, and is never replayed. A recorded attempt -
  including an interrupted or unknown one - is never retried.
* Cap admission is conservative and pre-emptive. Each dispatch is admitted
  against the maximum charge reachable from the remaining calls in its attempt
  (this call's own bound plus the ceiling bound for each call that could still
  follow), and the durable accounting keeps the full reservation bound for any
  call whose usage is unknown. The authoritative ledger must move by exactly the
  amount this run recorded; any other movement is a fail-closed exclusivity
  violation.
* Failures split into two classes. A known semantic or task-budget failure is
  recorded against its attempt and the run continues with the next *distinct*
  scheduled attempt. A transport, auth, policy, unknown, accounting or
  interrupted failure stops the run and is investigated without replay.
* No automatic semantic scoring. The results artifact is an import for human
  adjudication: every verdict is ``pending`` with a ``null`` reviewer.

Fixture artifact contract (``model-routing-followup-fixtures/1``)::

    {"artifact_version": "model-routing-followup-fixtures/1", "cases": [ ... 14 ... ]}

Each case is an object with exactly these keys::

    case_id            D01..D06 (diagnostic) or H01..H08 (heldout)
    stage              "diagnostic" | "heldout"
    latency_class      "utility" | "orchestration" | "synthesis"
    prompt             non-empty string
    max_calls          3
    max_tool_calls     non-negative int
    tools              [{"type": "function", "function": {"name", "description", "parameters"}}]
    tool_responses     {tool name: [ {"match": {...}, "response": {...}}
                                  | {"sequence": [{...}, ...]} ]}
    schema             JSON object or null
    expected           {"assertions": [{"id", "statement"}],
                       "hard_violation_rules": [str],
                       "acceptable_minor_misses": [str],
                       "rubric": str}

Tool simulation is argument-sensitive: a ``match`` entry must match every named
argument exactly and by key, and a request with no matching entry receives a
typed ``not_found`` result. There is deliberately no positional fallback, so a
wrong identifier can never be answered with another identifier's payload. A
``sequence`` entry must be the sole entry for its tool; its last value repeats
once the sequence is exhausted, which is what a repeated search needs. Unknown
top-level fixture keys are tolerated (they are metadata); unknown *per-case*
keys are rejected, because those are the execution contract.

Runner/runtime and routing-catalog hashes are part of the identity: parameter
conditions must not drift between diagnostic and held-out execution. A material
mid-experiment fix requires investigation and a separately identified version.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import math
import os
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import asyncpg

from orchestrator import compute_runtime, model_routing
from orchestrator.config import Settings, get_settings
from orchestrator.entitlements import EntitlementService
from orchestrator.entitlements.errors import BudgetExceeded, EntitlementsError
from orchestrator.entitlements.ledger import next_period_key
from orchestrator.entitlements.plans import Capability
from orchestrator.entitlements.policy import RoutePolicy, load_inference_policy, load_policy
from scripts import model_endpoint_reliability as reliability
from scripts import model_roster_live as live

JsonObject = dict[str, Any]

# ---------------------------------------------------------------------------
# Approved experiment shape (docs/MODEL_ROUTING_FOLLOWUP_PLAN.md)
# ---------------------------------------------------------------------------

#: Independent local artifact format versions. These are not a public API.
EXPERIMENT: Final[str] = "model-routing-followup/1"
FIXTURES_ARTIFACT_VERSION: Final[str] = "model-routing-followup-fixtures/1"
STATE_VERSION: Final[str] = "model-routing-followup-state/1"
RESULTS_ARTIFACT_VERSION: Final[str] = "model-routing-followup-results/1"

#: The only funded period. There is no rollover, refill or trial money here.
ORIGINAL_FUNDED_PERIOD: Final[str] = "2026-09"

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURES_PATH: Final[Path] = (
    REPO_ROOT / "tests" / "fixtures" / "model_routing_followup.json"
)

STAGES: Final[tuple[str, ...]] = ("diagnostic", "heldout")
CONDITIONS: Final[tuple[str, ...]] = ("default", "explicit")
LATENCY_CLASSES: Final[tuple[str, ...]] = ("utility", "orchestration", "synthesis")
DIAGNOSTIC_CASE_IDS: Final[tuple[str, ...]] = tuple(f"D{n:02d}" for n in range(1, 7))
HELD_OUT_CASE_IDS: Final[tuple[str, ...]] = tuple(f"H{n:02d}" for n in range(1, 9))

#: Experiment ceilings for every case, from the plan. They are not production
#: latency targets and this runner changes no production default.
MAX_CALLS_PER_ATTEMPT: Final[int] = 3
MAX_OUTPUT_TOKENS: Final[int] = 4096
MAX_CONTEXT_TOKENS: Final[int] = 16_000
ATTEMPT_DEADLINE_S: Final[float] = 90.0
REPEATS: Final[int] = 2
DIAGNOSTIC_ATTEMPTS: Final[int] = 72
HELD_OUT_ATTEMPTS: Final[int] = 48
TOTAL_ATTEMPTS: Final[int] = DIAGNOSTIC_ATTEMPTS + HELD_OUT_ATTEMPTS
DISPATCH_BOUND: Final[int] = TOTAL_ATTEMPTS * MAX_CALLS_PER_ATTEMPT

#: USD 20 incremental sub-cap inside the shared USD 25 aggregate account cap.
INCREMENTAL_CAP_MICROUSD: Final[int] = 20_000_000

#: Stop categories. Every one of them stops the run; the split exists so the
#: state records *why*, and so an operator can tell a misclassification between
#: "policy" and "unknown" cannot change the outcome.
STOP_CATEGORIES: Final[tuple[str, ...]] = (
    "transport",
    "auth",
    "policy",
    "unknown",
    "accounting",
    "interrupted",
)

NOT_FOUND_MESSAGE: Final[str] = "No fixture result for these arguments."


def not_found_result() -> JsonObject:
    """A fresh typed missing-result payload for an unmatched tool call.

    The simulator returns this instead of falling back to some other entry's
    payload, so a wrong identifier can never be answered with a valid one.
    """
    return {"error": {"code": "not_found", "message": NOT_FOUND_MESSAGE}}


@dataclass(frozen=True, slots=True)
class Candidate:
    """One candidate: a reviewed model identity plus its explicit-effort condition.

    The model identity is the exact reviewed model. It is not an endpoint
    approval: the route pin comes from the operator's external inference policy
    and nothing here qualifies a route.
    """

    label: str
    model: str
    effort: str


CANDIDATES: Final[tuple[Candidate, ...]] = (
    Candidate("luna", "openrouter/openai/gpt-6-luna", "low"),
    Candidate("sol", "openrouter/openai/gpt-6-sol", "high"),
    Candidate("sonnet", "openrouter/anthropic/claude-sonnet-5", "high"),
)
CANDIDATE_BY_LABEL: Final[Mapping[str, Candidate]] = {c.label: c for c in CANDIDATES}
CANDIDATE_LABELS: Final[tuple[str, ...]] = tuple(candidate.label for candidate in CANDIDATES)


# ---------------------------------------------------------------------------
# Experiment profile: the injection seam for a separately identified re-run
# ---------------------------------------------------------------------------

#: Operator-facing text of the results import. Extracted verbatim so a reused
#: executor can supply its own without changing this experiment's artifact.
RESULTS_NOTES: Final[str] = (
    "Human adjudication required. Every verdict is pending and no reviewer is "
    "recorded. Provider invoice cost is null where the provider did not report "
    "it, and the account ledger charge is null where the movement could not be "
    "attributed; unknown costs are never scored as zero. Raw provider payloads "
    "stay in the private state file."
)

#: CLI phase name to the fixture stage it executes, and the label used in the
#: phase-gating refusal. Both are this experiment's own approved values.
PHASE_STAGES: Final[Mapping[str, str]] = MappingProxyType(
    {"diagnostic": "diagnostic", "heldout": "heldout"}
)
PHASE_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {"diagnostic": "diagnostic stage", "heldout": "held-out stage"}
)


@dataclass(frozen=True, slots=True)
class ExperimentProfile:
    """Which separately identified experiment a reused executor is running.

    Every default is this file's own approved follow-up value, so the existing
    CLI, state identity, results artifact and guards are byte-for-byte unchanged
    when no profile is supplied. A new experiment may substitute its own identity
    and ceilings; it may not widen a guard. The executor still checks every cap,
    digest, exclusivity, no-replay and period rule it checked before, against
    these numbers, and a profile is only ever constructed in code - never read
    from the environment, a file or an argument.
    """

    #: Artifact and state identity. A different profile can neither adopt nor
    #: continue another experiment's records.
    experiment: str = EXPERIMENT
    state_version: str = STATE_VERSION
    results_artifact_version: str = RESULTS_ARTIFACT_VERSION
    generated_by: str = "scripts/model_routing_followup.py"
    results_notes: str = RESULTS_NOTES
    lock_holder_label: str = "follow-up runner"

    #: Ceilings. The incremental cap is enforced on every dispatch admission and
    #: again on restart; the dispatch bound is enforced before every call.
    incremental_cap_microusd: int = INCREMENTAL_CAP_MICROUSD
    total_attempts: int = TOTAL_ATTEMPTS
    dispatch_bound: int = DISPATCH_BOUND

    #: Extra source files whose bytes belong in the frozen implementation
    #: identity, so a re-run's own executor is hashed too.
    implementation_paths: tuple[Path, ...] = ()

    #: Phase naming and the phase that is gated on a fully recorded earlier one.
    phase_stages: Mapping[str, str] = field(default_factory=lambda: PHASE_STAGES)
    phase_labels: Mapping[str, str] = field(default_factory=lambda: PHASE_LABELS)
    gated_phase: str = "heldout"
    gate_stage: str = "diagnostic"


#: This experiment's own profile. Every call site defaults to it.
DEFAULT_PROFILE: Final[ExperimentProfile] = ExperimentProfile()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FollowupError(Exception):
    """Preflight, attribution or persistent-state failure; never downgraded."""


class PolicyViolation(FollowupError):
    """A pin, parameter, approval, period or envelope rule refused the run."""


class AccountingViolation(FollowupError):
    """A ledger, cap, baseline or exclusivity rule refused the run."""


class TaskFailure(Exception):
    """A recorded semantic or task-budget failure of one attempt.

    This is the model's failure, not the harness's: the attempt is recorded as
    failed and the run continues with the next distinct scheduled attempt. It is
    never repaired, retried or reclassified.
    """

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PolicyViolation(message)


def require_accounting(condition: bool, message: str) -> None:
    if not condition:
        raise AccountingViolation(message)


# ---------------------------------------------------------------------------
# Strict validation helpers
# ---------------------------------------------------------------------------


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _as_mapping(value: object, where: str) -> JsonObject:
    if not isinstance(value, dict):
        raise FollowupError(f"{where}: expected a JSON object, got {type(value).__name__}")
    return dict(value)


def _as_sequence(value: object, where: str) -> list[object]:
    if not isinstance(value, list):
        raise FollowupError(f"{where}: expected a JSON array, got {type(value).__name__}")
    return list(value)


def _as_str(value: object, where: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise FollowupError(f"{where}: expected a string, got {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise FollowupError(f"{where}: expected a non-empty string")
    return value


def _as_int(value: object, where: str, *, minimum: int | None = None) -> int:
    if not isinstance(value, (int, float)) or not _is_number(value):
        raise FollowupError(f"{where}: expected an integer, got {value!r}")
    if isinstance(value, float) and not value.is_integer():
        raise FollowupError(f"{where}: expected an integer, got {value!r}")
    number = int(value)
    if minimum is not None and number < minimum:
        raise FollowupError(f"{where}: expected an integer >= {minimum}, got {number}")
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
        raise FollowupError(f"{where}: missing required key(s) {', '.join(missing)}")
    unknown = sorted(key for key in obj if key not in allowed)
    if unknown:
        raise FollowupError(f"{where}: unknown key(s) {', '.join(unknown)}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One benchmark-only tool, carried as the exact OpenAI function payload."""

    name: str
    function: JsonObject

    @property
    def parameters(self) -> JsonObject:
        parameters = self.function.get("function", {}).get("parameters")
        return parameters if isinstance(parameters, dict) else {}

    @property
    def parameter_names(self) -> tuple[str, ...]:
        properties = self.parameters.get("properties")
        if not isinstance(properties, dict):
            return ()
        return tuple(sorted(str(key) for key in properties))


@dataclass(frozen=True, slots=True)
class CaseFixture:
    case_id: str
    stage: str
    latency_class: str
    prompt: str
    max_calls: int
    max_tool_calls: int
    tools: tuple[ToolSpec, ...]
    tool_responses: dict[str, tuple[JsonObject, ...]]
    schema: JsonObject | None
    expected: JsonObject
    sha256: str
    #: Prior user/assistant turns sent before ``prompt``. This experiment's own
    #: fixtures have none; a reused executor may supply a multi-turn corpus.
    history: tuple[JsonObject, ...] = ()

    @property
    def prompt_sha256(self) -> str:
        return hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FixtureSet:
    cases: tuple[CaseFixture, ...]
    sha256: str

    def by_id(self) -> dict[str, CaseFixture]:
        return {case.case_id: case for case in self.cases}


def _parse_tool(value: object, where: str) -> ToolSpec:
    obj = _as_mapping(value, where)
    _check_keys(obj, where, required=("type", "function"))
    require(obj["type"] == "function", f"{where}.type: expected 'function'")
    function = _as_mapping(obj["function"], f"{where}.function")
    _check_keys(
        function,
        f"{where}.function",
        required=("name", "description", "parameters"),
        optional=("strict",),
    )
    name = _as_str(function["name"], f"{where}.function.name")
    parameters = _as_mapping(function["parameters"], f"{where}.function.parameters")
    require(
        parameters.get("type") == "object",
        f"{where}.function.parameters: expected a JSON Schema object type",
    )
    properties = _as_mapping(
        parameters.get("properties", {}), f"{where}.function.parameters.properties"
    )
    for property_name, property_schema in properties.items():
        _as_str(property_name, f"{where}.function.parameters.properties key")
        _as_mapping(property_schema, f"{where}.function.parameters.properties.{property_name}")
    for key in _as_sequence(
        parameters.get("required", []), f"{where}.function.parameters.required"
    ):
        require(
            _as_str(key, f"{where}.function.parameters.required[]") in properties,
            f"{where}.function.parameters.required: undeclared property {key!r}",
        )
    return ToolSpec(name=name, function={"type": "function", "function": function})


def _parse_tool_responses(
    value: object, where: str, tools: Sequence[ToolSpec]
) -> dict[str, tuple[JsonObject, ...]]:
    """Parse the argument-sensitive tool simulator table.

    A ``match`` entry answers exactly those arguments; a ``sequence`` entry must
    be the only entry for its tool and repeats its last value once exhausted.
    Nothing here accepts a positional match: a wrong identifier must receive the
    typed not-found result instead of another identifier's payload.
    """
    mapping = _as_mapping(value, where)
    by_name = {tool.name: tool for tool in tools}
    parsed: dict[str, tuple[JsonObject, ...]] = {}
    for tool_name, entries in mapping.items():
        require(tool_name in by_name, f"{where}: response for undeclared tool {tool_name!r}")
        entry_where = f"{where}.{tool_name}"
        tool = by_name[tool_name]
        entries_list = _as_sequence(entries, entry_where)
        normalized: list[JsonObject] = []
        sequences = 0
        for index, entry in enumerate(entries_list):
            item_where = f"{entry_where}[{index}]"
            entry_obj = _as_mapping(entry, item_where)
            has_match = "match" in entry_obj
            has_sequence = "sequence" in entry_obj
            require(
                has_match != has_sequence,
                f"{item_where}: expected exactly one of 'match' or 'sequence'",
            )
            if has_match:
                _check_keys(entry_obj, item_where, required=("match", "response"))
                match = _as_mapping(entry_obj["match"], f"{item_where}.match")
                require(bool(match), f"{item_where}.match: expected a non-empty object")
                for key, expected in match.items():
                    _as_str(key, f"{item_where}.match key")
                    require(
                        key in tool.parameter_names,
                        f"{item_where}.match: {key!r} is not a parameter of tool {tool_name!r}",
                    )
                    _ = expected
                _as_mapping(entry_obj["response"], f"{item_where}.response")
                normalized.append({"match": match, "response": entry_obj["response"]})
            else:
                _check_keys(entry_obj, item_where, required=("sequence",))
                sequences += 1
                values = _as_sequence(entry_obj["sequence"], f"{item_where}.sequence")
                require(bool(values), f"{item_where}.sequence: expected at least one response")
                normalized.append(
                    {
                        "sequence": [
                            _as_mapping(value_item, f"{item_where}.sequence[{value_index}]")
                            for value_index, value_item in enumerate(values)
                        ]
                    }
                )
        require(
            sequences == 0 or (sequences == 1 and len(normalized) == 1),
            f"{entry_where}: a 'sequence' entry must be the only entry for its tool",
        )
        parsed[tool_name] = tuple(normalized)
    return parsed


def _parse_expected(value: object, where: str, case_id: str) -> JsonObject:
    obj = _as_mapping(value, where)
    _check_keys(
        obj,
        where,
        required=("assertions", "hard_violation_rules", "acceptable_minor_misses", "rubric"),
    )
    assertion_ids: list[str] = []
    for index, assertion in enumerate(_as_sequence(obj["assertions"], f"{where}.assertions")):
        assertion_where = f"{where}.assertions[{index}]"
        assertion_obj = _as_mapping(assertion, assertion_where)
        _check_keys(assertion_obj, assertion_where, required=("id", "statement"))
        assertion_id = _as_str(assertion_obj["id"], f"{assertion_where}.id")
        require(
            assertion_id.startswith(case_id),
            f"{assertion_where}.id: expected an id prefixed with {case_id!r}",
        )
        _as_str(assertion_obj["statement"], f"{assertion_where}.statement")
        assertion_ids.append(assertion_id)
    require(bool(assertion_ids), f"{where}.assertions: expected at least one assertion")
    require(
        len(set(assertion_ids)) == len(assertion_ids),
        f"{where}.assertions: duplicate assertion ids",
    )
    for expected_field in ("hard_violation_rules", "acceptable_minor_misses"):
        for index, item in enumerate(
            _as_sequence(obj[expected_field], f"{where}.{expected_field}")
        ):
            _as_str(item, f"{where}.{expected_field}[{index}]")
    _as_str(obj["rubric"], f"{where}.rubric")
    return obj


def _parse_case(value: object, index: int) -> CaseFixture:
    where = f"cases[{index}]"
    obj = _as_mapping(value, where)
    _check_keys(
        obj,
        where,
        required=(
            "case_id",
            "stage",
            "latency_class",
            "prompt",
            "max_calls",
            "max_tool_calls",
            "tools",
            "tool_responses",
            "schema",
            "expected",
        ),
    )
    case_id = _as_str(obj["case_id"], f"{where}.case_id")
    require(
        case_id in DIAGNOSTIC_CASE_IDS or case_id in HELD_OUT_CASE_IDS,
        f"{where}.case_id: expected D01-D06 or H01-H08, got {case_id!r}",
    )
    stage = _as_str(obj["stage"], f"{where}.stage")
    require(stage in STAGES, f"{where}.stage: expected one of {', '.join(STAGES)}")
    expected_stage = "diagnostic" if case_id in DIAGNOSTIC_CASE_IDS else "heldout"
    require(
        stage == expected_stage,
        f"{where}.stage: case {case_id} must declare {expected_stage!r}",
    )
    latency_class = _as_str(obj["latency_class"], f"{where}.latency_class")
    require(
        latency_class in LATENCY_CLASSES,
        f"{where}.latency_class: expected one of {', '.join(LATENCY_CLASSES)}",
    )
    max_calls = _as_int(obj["max_calls"], f"{where}.max_calls", minimum=1)
    require(
        max_calls == MAX_CALLS_PER_ATTEMPT,
        f"{where}.max_calls: every case is {MAX_CALLS_PER_ATTEMPT} model calls, got {max_calls}",
    )
    max_tool_calls = _as_int(obj["max_tool_calls"], f"{where}.max_tool_calls", minimum=0)
    tools = tuple(
        _parse_tool(item, f"{where}.tools[{tool_index}]")
        for tool_index, item in enumerate(_as_sequence(obj["tools"], f"{where}.tools"))
    )
    tool_names = [tool.name for tool in tools]
    require(len(set(tool_names)) == len(tool_names), f"{where}.tools: duplicate tool names")
    tool_responses = _parse_tool_responses(obj["tool_responses"], f"{where}.tool_responses", tools)
    schema_value = obj["schema"]
    schema: JsonObject | None
    if schema_value is None:
        schema = None
    else:
        schema = _as_mapping(schema_value, f"{where}.schema")
        require(schema.get("type") == "object", f"{where}.schema: expected an object schema")
        properties = _as_mapping(schema.get("properties", {}), f"{where}.schema.properties")
        require(bool(properties), f"{where}.schema.properties: expected at least one property")
        for property_name, property_schema in properties.items():
            _as_str(property_name, f"{where}.schema.properties key")
            _as_mapping(property_schema, f"{where}.schema.properties.{property_name}")
        required_keys = _as_sequence(schema.get("required", []), f"{where}.schema.required")
        require(bool(required_keys), f"{where}.schema.required: expected at least one property")
        for key in required_keys:
            require(
                _as_str(key, f"{where}.schema.required[]") in properties,
                f"{where}.schema.required: undeclared property {key!r}",
            )
    expected = _parse_expected(obj["expected"], f"{where}.expected", case_id)
    return CaseFixture(
        case_id=case_id,
        stage=stage,
        latency_class=latency_class,
        prompt=_as_str(obj["prompt"], f"{where}.prompt"),
        max_calls=max_calls,
        max_tool_calls=max_tool_calls,
        tools=tools,
        tool_responses=tool_responses,
        schema=schema,
        expected=expected,
        sha256=hashlib.sha256(
            json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    )


def _validate_case_matrix(cases: Sequence[CaseFixture]) -> None:
    ids = [case.case_id for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    require(not duplicates, f"cases: duplicate case ids {', '.join(duplicates)}")
    for stage, expected_ids in (
        ("diagnostic", DIAGNOSTIC_CASE_IDS),
        ("heldout", HELD_OUT_CASE_IDS),
    ):
        found = {case.case_id for case in cases if case.stage == stage}
        require(
            found == set(expected_ids),
            f"cases: stage {stage!r} must hold exactly {', '.join(expected_ids)}",
        )


def load_fixtures(path: Path) -> FixtureSet:
    """Load and strictly validate the frozen follow-up corpus.

    Unknown top-level keys are tolerated: they are metadata, not execution
    semantics. Unknown per-case keys are rejected.
    """
    try:
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FollowupError(f"fixtures: cannot read {path}: {exc}") from exc
    sha256 = hashlib.sha256(raw).hexdigest()
    obj = _as_mapping(document, "fixtures")
    for key in ("artifact_version", "cases"):
        require(key in obj, f"fixtures: missing required key {key!r}")
    version = _as_str(obj["artifact_version"], "fixtures.artifact_version")
    require(
        version == FIXTURES_ARTIFACT_VERSION,
        f"fixtures.artifact_version: expected {FIXTURES_ARTIFACT_VERSION}, got {version!r}",
    )
    cases = tuple(
        _parse_case(item, index) for index, item in enumerate(_as_sequence(obj["cases"], "cases"))
    )
    _validate_case_matrix(cases)
    return FixtureSet(cases=cases, sha256=sha256)


# ---------------------------------------------------------------------------
# Frozen schedule
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Attempt:
    attempt_id: str
    case_id: str
    stage: str
    candidate_label: str
    condition: str
    repeat: int
    latency_class: str
    max_calls: int
    max_tool_calls: int

    @property
    def effort(self) -> str | None:
        """The explicit effort this attempt sends, or ``None`` for the default."""
        return (
            CANDIDATE_BY_LABEL[self.candidate_label].effort
            if self.condition == "explicit"
            else None
        )


def attempt_id(case_id: str, candidate_label: str, condition: str, repeat: int) -> str:
    """Attempt ids are new: the condition is always part of the id.

    The baseline pilot's ``<case>-<candidate>-r<n>`` shape is never reused, so a
    follow-up result can never be confused with a baseline record.
    """
    return f"{case_id}-{candidate_label}-{condition}-r{repeat}"


def build_schedule(fixtures: FixtureSet) -> tuple[Attempt, ...]:
    """Expand the frozen corpus into the 120-attempt recorded schedule.

    Interleaving is deterministic and candidate-major inside each round, so the
    first six attempts cover one case for all three candidates in both
    conditions: every candidate's first scheduled block exercises both parameter
    paths early, inside the declared count.
    """
    by_stage: dict[str, list[CaseFixture]] = {stage: [] for stage in STAGES}
    for case in fixtures.cases:
        by_stage[case.stage].append(case)
    attempts: list[Attempt] = []
    for repeat in range(1, REPEATS + 1):
        for case in by_stage["diagnostic"]:
            for candidate in CANDIDATES:
                for condition in CONDITIONS:
                    attempts.append(_attempt(case, candidate.label, condition, repeat))
    for repeat in range(1, REPEATS + 1):
        for case in by_stage["heldout"]:
            for candidate in CANDIDATES:
                attempts.append(_attempt(case, candidate.label, "explicit", repeat))
    require(
        len(attempts) == TOTAL_ATTEMPTS,
        f"schedule: expected {TOTAL_ATTEMPTS} attempts, built {len(attempts)}",
    )
    ids = [attempt.attempt_id for attempt in attempts]
    require(len(set(ids)) == len(ids), "schedule: duplicate attempt ids")
    return tuple(attempts)


def _attempt(case: CaseFixture, candidate_label: str, condition: str, repeat: int) -> Attempt:
    return Attempt(
        attempt_id=attempt_id(case.case_id, candidate_label, condition, repeat),
        case_id=case.case_id,
        stage=case.stage,
        candidate_label=candidate_label,
        condition=condition,
        repeat=repeat,
        latency_class=case.latency_class,
        max_calls=case.max_calls,
        max_tool_calls=case.max_tool_calls,
    )


def schedule_for_stage(schedule: Sequence[Attempt], stage: str) -> tuple[Attempt, ...]:
    require(stage in STAGES, f"unknown stage {stage!r}")
    return tuple(attempt for attempt in schedule if attempt.stage == stage)


def schedule_fingerprint(schedule: Sequence[Attempt]) -> str:
    payload = [
        {
            "attempt_id": attempt.attempt_id,
            "case_id": attempt.case_id,
            "stage": attempt.stage,
            "candidate_label": attempt.candidate_label,
            "condition": attempt.condition,
            "repeat": attempt.repeat,
        }
        for attempt in schedule
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def implementation_fingerprint(*, extra_paths: Sequence[Path] = ()) -> str:
    """Freeze code and effective parameter configuration before observations.

    ``extra_paths`` lets a separately identified experiment that reuses this
    executor freeze its own source bytes into the same identity. The default is
    empty, so no *extra* files enter this experiment's digest. The digest value
    itself is not frozen across edits: this executor's own source bytes are
    hashed, so any change here - including the seam that added this parameter -
    changes the recorded fingerprint, which is the identity working as intended.
    """
    routing_path = Path(
        Settings.explicit_evaluation_environment().get(
            model_routing.MODEL_ROUTING_ENV, str(model_routing.DEFAULT_MODEL_ROUTING)
        )
    ).resolve()
    paths = [
        Path(__file__),
        Path(live.__file__),
        Path(compute_runtime.__file__),
        Path(model_routing.__file__),
        Path(reliability.__file__),
        routing_path,
    ]
    paths.extend(extra_paths)
    paths.extend(sorted((REPO_ROOT / "orchestrator" / "entitlements").glob("*.py")))
    return hashlib.sha256(
        json.dumps(
            {str(path.resolve()): live.digest(path) for path in paths}, sort_keys=True
        ).encode()
    ).hexdigest()


# ---------------------------------------------------------------------------
# Route qualification
# ---------------------------------------------------------------------------


def qualified_routes(pins: Mapping[str, Any], inference: Any) -> dict[str, RoutePolicy]:
    """Resolve exactly one independently qualified route per candidate.

    The pin map must name exactly the three planned candidates, each pinned route
    must serve exactly that candidate's reviewed model, and exactly one approved
    OpenRouter route may serve that model - otherwise automatic selection could
    reach a second, unintended and separately billed endpoint for the same model.
    """
    require(
        isinstance(pins, dict) and set(pins) == set(CANDIDATE_LABELS),
        f"pins must name exactly {', '.join(CANDIDATE_LABELS)}",
    )
    routes: dict[str, RoutePolicy] = {}
    for candidate in CANDIDATES:
        route_id = pins[candidate.label]
        require(
            isinstance(route_id, str) and bool(route_id.strip()),
            f"missing route ID for {candidate.label}",
        )
        route = inference.route(route_id)
        require(route is not None, f"unqualified route for {candidate.label}")
        assert route is not None  # narrowed for the type checker
        require(
            route.is_approved(inference.requirements), f"unqualified route for {candidate.label}"
        )
        require(
            route.provider == "openrouter" and route.model.startswith("openrouter/"),
            f"non-OpenRouter route for {candidate.label}",
        )
        require(
            route.model == candidate.model,
            f"model pin mismatch for {candidate.label}: expected {candidate.model}",
        )
        require(
            route.route_class in {"routine", "premium"} and route.price_ceiling is not None,
            f"unfunded route class for {candidate.label}",
        )
        require(
            bool(route.transport.provider_only),
            f"provider transport pin missing for {candidate.label}",
        )
        competitors = [
            other
            for other in inference.routes.values()
            if other.model == route.model
            and other.provider == "openrouter"
            and other.route_class in {"routine", "premium"}
            and other.is_approved(inference.requirements)
        ]
        require(
            len(competitors) == 1 and competitors[0].route_id == route_id,
            f"ambiguous model route pin for {candidate.label}",
        )
        route.transport_payload(inference.requirements)
        # Supported parameters are revalidated here, not discovered from a
        # failed call: an unsupported effort is a stop before any spend.
        require(
            model_routing.load_model_routing().model(route.model) is not None,
            f"isolated catalog must declare {candidate.label} and its effort ladder",
        )
        require(
            model_routing.supports_reasoning_effort(route.model, candidate.effort),
            f"{candidate.label} model does not accept reasoning effort {candidate.effort!r}",
        )
        require(
            not model_routing.model_parameter_presets(route.model, "routine"),
            "provider-default condition requires an isolated routing catalog without presets",
        )
        routes[candidate.label] = route
    return routes


def validate_period(period: str) -> None:
    require(
        len(period) == 7 and datetime.strptime(period, "%Y-%m").strftime("%Y-%m") == period,
        "period must be YYYY-MM",
    )
    require(
        period == ORIGINAL_FUNDED_PERIOD,
        "only the original funded period is authorized",
    )


def ensure_funded_window(service: EntitlementService, period: str, margin_s: float) -> None:
    """Refuse a call that could normally run past the original period boundary.

    The margin covers the whole remaining attempt deadline, so no attempt ever
    begins when its own calls could cross into an unfunded month.
    """
    require(service.current_period_key() == period, "original period ended")
    boundary = datetime.fromisoformat(next_period_key(period) + "-01").replace(tzinfo=timezone.utc)
    require(
        datetime.now(timezone.utc) + timedelta(seconds=margin_s) < boundary,
        "too close to the original period boundary",
    )


# ---------------------------------------------------------------------------
# Durable state
# ---------------------------------------------------------------------------


def account_lock_path(
    state_path: Path, account: uuid.UUID, *, experiment: str = EXPERIMENT
) -> Path:
    """One account-wide lock for one experiment, independent of the state file name.

    A second state path, a second phase and a second candidate batch therefore
    cannot run against the same account concurrently. A separately identified
    experiment passes its own ``experiment`` so it locks under its own name, and
    can additionally take this experiment's lock when the two share an account.
    """
    return state_path.with_name(f".{experiment.replace('/', '-')}.{account}.lock")


def write_state(path: Path, state: JsonObject) -> None:
    """Durable, private checkpoint. Reused from the baseline runner unchanged."""
    live.write_state(path, state)


@contextmanager
def locked_state(
    state_path: Path,
    account: uuid.UUID,
    identity: JsonObject,
    *,
    profile: ExperimentProfile = DEFAULT_PROFILE,
) -> Iterator[JsonObject]:
    """Take the private account lock and return this run's durable state.

    The profile's state version and the whole identity must match exactly, so a
    run can neither adopt nor continue another experiment's records. A torn
    checkpoint is never entered: an incomplete write is evidence of a crash, not a
    state to continue from.
    """
    require(
        state_path.parent.is_dir() and not state_path.is_symlink(), "state directory/file invalid"
    )
    require(state_path.parent.stat().st_mode & 0o077 == 0, "state directory must be private (0700)")
    lock_path = account_lock_path(state_path, account, experiment=profile.experiment)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FollowupError(
                f"another {profile.lock_holder_label} holds the account lock"
            ) from exc
        require(
            not state_path.with_name(state_path.name + ".tmp").exists(),
            "incomplete state checkpoint",
        )
        if state_path.exists():
            require(state_path.is_file() and not state_path.is_symlink(), "state path invalid")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            require(isinstance(state, dict), "state must be an object")
            require(state.get("version") == profile.state_version, "state version changed")
            require(state.get("identity") == identity, "run identity drift")
            require(isinstance(state.get("attempts"), dict), "state attempts invalid")
        else:
            state = {
                "version": profile.state_version,
                "identity": identity,
                "attempts": {},
                "account_ledger_baseline": None,
                "stages": {},
            }
            write_state(state_path, state)
        yield state
    finally:
        os.close(fd)


def write_results(path: Path, results: JsonObject) -> None:
    """Write the results artifact to a new, private path only."""
    require(not path.exists(), "results file already exists")
    require(path.parent.is_dir(), "results directory missing")
    write_state(path, results)


def call_rows(state: Mapping[str, Any]) -> list[JsonObject]:
    """Every dispatched call ever recorded, in recorded order."""
    rows: list[JsonObject] = []
    attempts = state.get("attempts")
    if not isinstance(attempts, dict):
        return rows
    for entry in attempts.values():
        if not isinstance(entry, dict):
            continue
        calls = entry.get("calls")
        if isinstance(calls, list):
            rows.extend(call for call in calls if isinstance(call, dict))
    return rows


def accounted_microusd(state: Mapping[str, Any]) -> int:
    """Conservative experiment charge: known charges plus unknown intent bounds.

    A settled call contributes its charge; an interrupted, unknown or
    unattributable call keeps its full reservation bound. A call that was never
    dispatched contributes nothing, and no charge is ever refunded.
    """
    total = 0
    for row in call_rows(state):
        charge = row.get("account_charge_microusd")
        if isinstance(charge, int) and not isinstance(charge, bool):
            total += charge
            continue
        bound = row.get("reservation_bound_microusd")
        if isinstance(bound, int) and not isinstance(bound, bool):
            total += bound
    return total


def baseline_exposure(state: Mapping[str, Any]) -> int:
    baseline = state.get("account_ledger_baseline")
    if not isinstance(baseline, dict):
        raise AccountingViolation("account ledger baseline missing")
    value = baseline.get("exposure_microusd")
    if not isinstance(value, int) or isinstance(value, bool):
        raise AccountingViolation("baseline exposure invalid")
    return value


def require_exclusive(state: Mapping[str, Any], exposure: reliability.LedgerExposure) -> int:
    """Fail closed unless the authoritative ledger moved only for this run.

    The account ledger is shared with the aggregate USD 25 cap, so a movement
    this run cannot explain is unattributable evidence. Attribution is therefore
    refused rather than approximated, and no custom schema is introduced to track
    it: the frozen baseline, the recorded charges and the ledger's own tables are
    sufficient.
    """
    delta = exposure.total_microusd - baseline_exposure(state)
    recorded = accounted_microusd(state)
    require_accounting(
        delta == recorded,
        f"account ledger moved {delta} microusd since the frozen baseline but this run recorded "
        f"{recorded}; exclusive execution is required for attribution",
    )
    require_accounting(
        exposure.open_holds == 0,
        f"{exposure.open_holds} open reservation hold(s) on the evaluation account",
    )
    return delta


def freeze_baseline(
    state: JsonObject,
    state_path: Path,
    exposure: reliability.LedgerExposure,
    period: str,
    *,
    profile: ExperimentProfile = DEFAULT_PROFILE,
) -> None:
    """Freeze the experiment baseline once, with zero open holds.

    A restart never re-bases: the ceiling is re-derived from the durable rows, so
    an interrupted run cannot hand itself a fresh allowance.
    """
    baseline = state.get("account_ledger_baseline")
    if baseline is None:
        require_accounting(
            exposure.open_holds == 0,
            "no open reservations allowed before freezing the ledger baseline",
        )
        state["account_ledger_baseline"] = {
            "exposure_microusd": exposure.total_microusd,
            "open_holds": exposure.open_holds,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "period_key": period,
        }
    else:
        require_accounting(
            isinstance(baseline, dict) and baseline.get("period_key") == period,
            "ledger baseline period drift",
        )
        require_accounting(
            accounted_microusd(state) <= profile.incremental_cap_microusd,
            "durable experiment accounting already exceeds the incremental ceiling",
        )
    write_state(state_path, state)


# ---------------------------------------------------------------------------
# Request construction and the tool simulator
# ---------------------------------------------------------------------------


def request_for(
    case: CaseFixture, route: RoutePolicy, attempt: Attempt, messages: list[JsonObject]
) -> JsonObject:
    """Build the request for one call of one attempt.

    The ``default`` condition omits ``reasoning_effort`` entirely, with catalog
    injection forbidden by preflight; the ``explicit`` condition sends the
    candidate's reviewed effort verbatim and is validated, never rewritten.
    """
    params: JsonObject = {
        "model": route.model,
        "messages": messages,
        "max_tokens": min(MAX_OUTPUT_TOKENS, route.max_output_tokens),
    }
    if case.tools:
        params["tools"] = case_tools(case)
        params["tool_choice"] = "auto"
    if case.schema is not None:
        params["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": f"followup_{case.case_id}",
                "strict": True,
                "schema": case.schema,
            },
        }
    if attempt.condition == "explicit":
        params["reasoning_effort"] = attempt.effort
    return params


def initial_messages(case: CaseFixture) -> list[JsonObject]:
    """The conversation an attempt opens with: any frozen history, then the prompt."""
    return [*(dict(turn) for turn in case.history), {"role": "user", "content": case.prompt}]


def case_tools(case: CaseFixture) -> list[JsonObject]:
    """The exact tool payload sent to the provider, unmodified."""
    return [dict(tool.function) for tool in case.tools]


def simulate(
    case: CaseFixture, name: str, args: Mapping[str, Any], cursors: dict[str, int]
) -> JsonObject:
    """Answer one tool call from the frozen, argument-sensitive fixture table.

    Every ``match`` key must be present in the arguments and equal, so a fetch for
    an identifier the case never declared receives the typed not-found result
    instead of some other identifier's payload.
    """
    for entry in case.tool_responses.get(name, ()):
        match = entry.get("match")
        if isinstance(match, dict) and all(
            key in args and args[key] == value for key, value in match.items()
        ):
            response = entry.get("response")
            return response if isinstance(response, dict) else {}
    sequences = [entry for entry in case.tool_responses.get(name, ()) if "sequence" in entry]
    if sequences:
        values = sequences[0]["sequence"]
        index = cursors.get(name, 0)
        # The last value repeats: a search that is called again must keep
        # returning the same corpus, never a fabricated extra result.
        value = values[min(index, len(values) - 1)]
        cursors[name] = index + 1
        return value if isinstance(value, dict) else {}
    return not_found_result()


def _reasoning_tokens(usage: Any) -> int | None:
    """Reasoning tokens as reported, or ``None`` when the provider omits them."""

    def field(name: str) -> Any:
        if isinstance(usage, dict):
            return usage.get(name)
        return getattr(usage, name, None)

    value = field("reasoning_tokens")
    if value is None:
        details = field("completion_tokens_details")
        if isinstance(details, dict):
            value = details.get("reasoning_tokens")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _provider_cost(usage: Any) -> float | None:
    """Reported provider invoice cost, or ``None`` when it is absent or untrusted."""
    value = usage.get("cost") if isinstance(usage, dict) else getattr(usage, "cost", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    cost = float(value)
    return cost if 0 <= cost < math.inf else None


def ceiling_bound(route: RoutePolicy) -> int:
    """Worst-case charge for one call at this experiment's context ceiling."""
    return route.estimate_microusd(MAX_CONTEXT_TOKENS, MAX_OUTPUT_TOKENS)


# ---------------------------------------------------------------------------
# Failure classification (typed metadata only)
# ---------------------------------------------------------------------------

_AUTH_CATEGORIES: Final[frozenset[str]] = frozenset(
    {compute_runtime.FAILURE_AUTHENTICATION_FAILED, compute_runtime.FAILURE_PAYMENT_FAILED}
)
_ACCOUNTING_CATEGORIES: Final[frozenset[str]] = frozenset(
    {compute_runtime.FAILURE_SETTLEMENT_FAILED}
)
_ACCOUNTING_CODES: Final[frozenset[str]] = frozenset(
    {"settlement_conflict", "settlement_failed", "budget_exceeded", "limit_exceeded"}
)
_TRANSPORT_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        compute_runtime.FAILURE_TIMEOUT,
        compute_runtime.FAILURE_DEADLINE_EXCEEDED,
        compute_runtime.FAILURE_RATE_LIMITED,
        compute_runtime.FAILURE_UPSTREAM_UNAVAILABLE,
        compute_runtime.FAILURE_CONNECTION_FAILED,
    }
)
_POLICY_CODES: Final[frozenset[str]] = frozenset(
    {
        "route_unavailable",
        "capability_unavailable",
        "capacity_unavailable",
        "context_limit",
        "modality_unavailable",
        "profile_unavailable",
        "account_unavailable",
        "extended_agents_exhausted",
    }
)


def _token(value: object) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else "unknown"


def _opt_status(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def classify_stop(exc: BaseException) -> JsonObject:
    """Structured stop metadata. Raw exception strings are never stored.

    Every category stops the run; the classification exists so the state says
    which investigation is needed, not so a category can be retried.
    """
    record: JsonObject = {
        "category": "unknown",
        "code": None,
        "status_code": None,
        "exception_type": type(exc).__name__,
    }
    if isinstance(exc, asyncio.CancelledError):
        record["category"] = "interrupted"
        return record
    if isinstance(exc, AccountingViolation):
        record["category"] = "accounting"
        record["code"] = "experiment_accounting"
        return record
    if isinstance(exc, PolicyViolation):
        record["category"] = "policy"
        record["code"] = "experiment_policy"
        return record
    if isinstance(exc, (BudgetExceeded, EntitlementsError)):
        record["category"] = "accounting"
        record["code"] = _token(getattr(exc, "code", None))
        return record
    if isinstance(exc, compute_runtime.ComputeUnavailable):
        category = _token(getattr(exc, "category", None))
        if category == "unknown":
            category = _token(getattr(exc, "code", None))
        code = _token(getattr(exc, "code", None))
        status_code = _opt_status(getattr(exc, "status_code", None))
        record["code"] = code
        record["status_code"] = status_code
        if category in _ACCOUNTING_CATEGORIES or code in _ACCOUNTING_CODES:
            record["category"] = "accounting"
        elif category in _AUTH_CATEGORIES:
            record["category"] = "auth"
        elif (
            status_code in compute_runtime.RETRY_ELIGIBLE_STATUS
            or category in _TRANSPORT_CATEGORIES
        ):
            record["category"] = "transport"
        elif category in _POLICY_CODES or code in _POLICY_CODES:
            record["category"] = "policy"
        return record
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        record["category"] = "transport"
        record["code"] = compute_runtime.FAILURE_TIMEOUT
    return record


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RunContext:
    pool: Any
    account: uuid.UUID
    service: EntitlementService
    period: str
    identity: JsonObject
    state: JsonObject
    state_path: Path
    schedule: tuple[Attempt, ...]
    cases: Mapping[str, CaseFixture]
    routes: Mapping[str, RoutePolicy]
    commercial_path: Path
    inference_path: Path
    account_ceiling_microusd: int
    #: Which experiment's ceilings and identity this context enforces. Defaults to
    #: this file's own approved follow-up profile, so existing callers are unchanged.
    profile: ExperimentProfile = DEFAULT_PROFILE


def recorded_calls(state: Mapping[str, Any]) -> int:
    return len(call_rows(state))


def dispatch_headroom(
    state: Mapping[str, Any],
    schedule: Sequence[Attempt],
    *,
    profile: ExperimentProfile = DEFAULT_PROFILE,
) -> int:
    """Remaining hard dispatch allowance; admission also fits this attempt's tail.

    Each immutable scheduled attempt has its own three-call limit. Reserving
    hypothetical calls for every future attempt here double-counted the current
    attempt during pre-admission. The fixed schedule already bounds that total.
    """
    require(len(schedule) <= profile.total_attempts, "schedule exceeds attempt envelope")
    return profile.dispatch_bound - recorded_calls(state)


def verify_candidate(
    case: CaseFixture, route: RoutePolicy, resolved: Any, params: JsonObject
) -> int:
    """This request's own reservation bound, for exactly this pinned route."""
    bound = compute_runtime._request_bound(params)
    # The context ceiling is measured with the runtime's own estimate view; the
    # raw `bound` member of InputSize is the byte-level settlement hold, which
    # the runtime itself never adds to max_tokens.
    require(
        bound.estimate + int(params["max_tokens"]) <= MAX_CONTEXT_TOKENS,
        f"context ceiling exceeded for {case.case_id}",
    )
    candidates = compute_runtime._priced_candidates(
        resolved, bound, params, route.model, check_budget=False
    )
    require(
        len(candidates) == 1 and candidates[0][2].route_id == route.route_id,
        f"pinned route ineligible for {case.case_id}",
    )
    return candidates[0][0]


async def admit_call(ctx: RunContext, reachable: int, calls_left: int) -> int:
    """Admit one call against both caps before any external dispatch.

    ``reachable`` is the conservative maximum this attempt could still charge:
    this call's own bound plus the ceiling bound for each call that could follow.
    Admission therefore rejects whichever cap would be exceeded, instead of
    discovering the refusal after the spend. Both ceilings come from the context's
    own experiment profile, so a reused executor can never charge a stricter
    experiment against a looser one's allowance.
    """
    require(
        dispatch_headroom(ctx.state, ctx.schedule, profile=ctx.profile) >= calls_left,
        "dispatch envelope exhausted",
    )
    exposure = await reliability.read_ledger_exposure(ctx.pool, ctx.account, ctx.period)
    require_exclusive(ctx.state, exposure)
    charged = accounted_microusd(ctx.state)
    require_accounting(
        charged + reachable <= ctx.profile.incremental_cap_microusd,
        "incremental sub-cap would be exceeded",
    )
    require_accounting(
        exposure.total_microusd + reachable <= ctx.account_ceiling_microusd,
        "aggregate account cap would be exceeded",
    )
    return exposure.total_microusd


def _self_check(ctx: RunContext) -> None:
    require(
        live.digest(ctx.commercial_path) == ctx.identity["commercial_sha256"]
        and live.digest(ctx.inference_path) == ctx.identity["inference_sha256"],
        "policy changed during run",
    )
    require(
        implementation_fingerprint(extra_paths=ctx.profile.implementation_paths)
        == ctx.identity["implementation_sha256"],
        "implementation or parameter configuration changed during run",
    )


async def _attempt_preflight(ctx: RunContext) -> None:
    _self_check(ctx)
    ensure_funded_window(ctx.service, ctx.period, ATTEMPT_DEADLINE_S + 10)
    await live.validate_database(ctx.pool, ctx.account, ctx.service, ctx.period)


def _parse_tool_arguments(case: CaseFixture, call: Mapping[str, Any]) -> tuple[str, JsonObject]:
    """Validate one proposed tool call before any of it is executed.

    An undeclared tool, unparseable arguments or arguments that fail the frozen
    schema are a recorded failure of the attempt, never an executed call.
    """
    function = call.get("function")
    if not isinstance(function, dict):
        raise TaskFailure("invalid_tool_call", "tool call has no function object")
    name = function.get("name")
    if not isinstance(name, str) or not name.strip():
        raise TaskFailure("invalid_tool_call", "tool call has no tool name")
    tool = next((item for item in case.tools if item.name == name), None)
    if tool is None:
        raise TaskFailure("undeclared_tool", f"tool {name!r} is not declared by {case.case_id}")
    raw = function.get("arguments")
    if not isinstance(raw, str):
        raise TaskFailure("invalid_tool_arguments", "tool arguments are not a JSON string")
    try:
        args = json.loads(raw)
    except json.JSONDecodeError:
        raise TaskFailure("invalid_tool_arguments", "tool arguments are not valid JSON") from None
    if not isinstance(args, dict):
        raise TaskFailure("invalid_tool_arguments", "tool arguments are not a JSON object")
    undeclared = sorted(set(args) - set(tool.parameter_names))
    if undeclared:
        raise TaskFailure(
            "invalid_tool_arguments", f"undeclared argument(s) {', '.join(undeclared)}"
        )
    if not live.schema_matches(args, tool.parameters):
        raise TaskFailure("invalid_tool_arguments", f"arguments do not match {name!r} schema")
    return name, args


def _response_message(response: Mapping[str, Any]) -> tuple[JsonObject, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise TaskFailure("invalid_response", "expected exactly one response choice")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise TaskFailure("invalid_response", "response choice has no message object")
    return choice, message


def _record_response(call: JsonObject, response: Mapping[str, Any], route: RoutePolicy) -> None:
    """Corroborate the exact served model and provider; never infer them."""
    call["actual_model_reported"] = response.get("model")
    require(
        response.get("model") in {route.model, route.model.removeprefix("openrouter/")},
        "served model drift",
    )
    # ``None`` means unobserved, never inferred into an endpoint claim.
    call["provider_reported"] = response.get("provider")
    providers = route.transport.provider_only
    if not providers:
        raise PolicyViolation("provider pin missing")
    require(
        live.provider_echo_matches(response.get("provider"), str(providers[0])),
        "served provider drift",
    )
    usage = response.get("usage", {})
    call["raw_response"] = dict(response)
    call["usage"] = usage
    call["reasoning_tokens"] = _reasoning_tokens(usage)
    call["provider_cost_usd"] = _provider_cost(usage)


async def run_attempt(ctx: RunContext, attempt: Attempt) -> None:
    """Run one scheduled attempt: at most three pinned calls, then a stop or a record.

    The entry is fsynced before the first call, every call row is fsynced before
    its dispatch, and a recorded attempt is never replayed - including one left
    ``in_progress`` or interrupted by a crash.
    """
    case = ctx.cases[attempt.case_id]
    route = ctx.routes[attempt.candidate_label]
    entry: JsonObject = {
        "attempt_id": attempt.attempt_id,
        "case_id": attempt.case_id,
        "stage": attempt.stage,
        "candidate_label": attempt.candidate_label,
        "condition": attempt.condition,
        "repeat": attempt.repeat,
        "latency_class": attempt.latency_class,
        "status": "in_progress",
        "calls": [],
        "tool_steps": [],
        "tool_call_count": 0,
        "disallowed_tool_proposals": [],
        "schema_valid": None,
        "requested_reasoning_effort": attempt.effort,
        "final_response": None,
        "task_failures": [],
        "stop": None,
    }
    ctx.state["attempts"][attempt.attempt_id] = entry
    write_state(ctx.state_path, ctx.state)
    messages = initial_messages(case)
    cursors: dict[str, int] = {}
    started = time.monotonic()
    deadline = started + ATTEMPT_DEADLINE_S
    try:
        await _attempt_preflight(ctx)
        for index in range(case.max_calls):
            _self_check(ctx)
            ensure_funded_window(ctx.service, ctx.period, deadline - time.monotonic() + 10)
            params = request_for(case, route, attempt, messages)
            resolved = await ctx.service.resolve(ctx.account)
            bound = verify_candidate(case, route, resolved, params)
            remaining_calls = case.max_calls - index - 1
            reachable = bound + remaining_calls * ceiling_bound(route)
            await admit_call(ctx, reachable, remaining_calls + 1)
            # Re-check against the account's own remaining allocation, which the
            # conservative sub-cap check above cannot see.
            require_accounting(
                len(
                    compute_runtime._priced_candidates(
                        resolved, compute_runtime._request_bound(params), params, route.model
                    )
                )
                == 1,
                "pinned route exceeds the remaining account allocation",
            )
            request_bound = compute_runtime._request_bound(params)
            call: JsonObject = {
                "number": index + 1,
                "status": "dispatched_unknown",
                "route_id": route.route_id,
                "requested_model": route.model,
                "provider_pin": list(route.transport.provider_only or ()),
                "reasoning_effort_requested": params.get("reasoning_effort"),
                "max_output_tokens": params["max_tokens"],
                # Two views of the prompt size the runtime quotes for this
                # request: the byte-level settlement hold and the realistic
                # context-window estimate (compute_runtime.InputSize).
                "input_token_bound": request_bound.bound,
                "input_token_estimate": request_bound.estimate,
                "reservation_bound_microusd": bound,
                "account_charge_microusd": None,
                "provider_cost_usd": None,
                "runtime_route_id": None,
                "runtime_model": None,
            }
            entry["calls"].append(call)
            # Intent is durable before the provider is contacted: a crash here can
            # lose one attempt, never duplicate a paid call.
            write_state(ctx.state_path, ctx.state)
            call_start = time.monotonic()
            scope: Any = None
            try:
                # The whole attempt shares one 90s deadline, clipped to what is
                # left of it, so the budget can never be lengthened per call.
                timeout = min(deadline - time.monotonic(), ATTEMPT_DEADLINE_S)
                if timeout <= 0:
                    raise asyncio.TimeoutError("attempt deadline exhausted before dispatch")
                async with compute_runtime.account_compute(
                    ctx.pool,
                    ctx.account,
                    operation="chat",
                    profile="routine",
                    expected_period=ctx.period,
                ) as scope:
                    response = live.as_dict(
                        await compute_runtime.guarded_completion(
                            **params,
                            _route_id=route.route_id,
                            _dispatch_timeout_s=timeout,
                        )
                    )
                    # Attribution reads the live runtime routing state inside the
                    # account scope, where the dispatch recorded what went out.
                    routed = model_routing.current_routing()
                    call["runtime_route_id"] = routed.selected_route_id
                    call["runtime_model"] = routed.selected_model
                    call["runtime_attributed_model"] = compute_runtime.selected_model()
                    require(
                        routed.selected_route_id == route.route_id
                        and routed.selected_model == route.model,
                        "runtime route drift",
                    )
            finally:
                call["elapsed_seconds"] = round(time.monotonic() - call_start, 6)
                if scope is not None:
                    # Each call owns its reservation and settlement. An unknown
                    # usage keeps the full bound: it is never reported as free.
                    charge = sum(scope.settled.values())
                    call["account_charge_microusd"] = charge if scope.settled else None
                write_state(ctx.state_path, ctx.state)
            exposure = await reliability.read_ledger_exposure(ctx.pool, ctx.account, ctx.period)
            require_exclusive(ctx.state, exposure)
            _record_response(call, response, route)
            call["status"] = "completed"
            write_state(ctx.state_path, ctx.state)
            choice, message = _response_message(response)
            if choice.get("finish_reason") in {"length", "content_filter"}:
                raise TaskFailure(
                    "truncated_response",
                    f"finish_reason={choice.get('finish_reason')!r}",
                )
            proposed = message.get("tool_calls") or []
            if not isinstance(proposed, list):
                raise TaskFailure("invalid_response", "tool_calls is not a list")
            if proposed:
                _handle_tool_calls(
                    ctx, entry, case, cursors, messages, proposed, index, message=message
                )
                continue
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                raise TaskFailure("empty_answer", "final answer is empty")
            entry["final_response"] = content
            if case.schema is not None:
                try:
                    parsed = json.loads(content)
                except ValueError:
                    entry["schema_valid"] = False
                else:
                    entry["schema_valid"] = live.schema_matches(parsed, case.schema)
            entry["status"] = "completed"
            break
        else:
            raise TaskFailure("call_budget_exhausted", "no final answer within the call budget")
    except TaskFailure as failure:
        entry["status"] = "failed"
        entry["task_failures"].append({"kind": failure.kind, "detail": failure.detail})
    except BaseException as exc:
        stop = classify_stop(exc)
        entry["stop"] = stop
        entry["status"] = "interrupted" if stop["category"] == "interrupted" else "stopped"
        raise
    finally:
        entry["latency_seconds"] = round(time.monotonic() - started, 6)
        write_state(ctx.state_path, ctx.state)


def _handle_tool_calls(
    ctx: RunContext,
    entry: JsonObject,
    case: CaseFixture,
    cursors: dict[str, int],
    messages: list[JsonObject],
    proposed: Sequence[Any],
    index: int,
    *,
    message: Mapping[str, Any] | None = None,
) -> None:
    """Validate, budget and answer a bundled tool-call response.

    A bundled response is budgeted as a whole, including calls bundled into one
    model response. When the bundle would exceed the declared tool budget the
    proposal is recorded and the attempt fails *without* executing any of it and
    without a synthetic extra completion call.
    """
    parsed: list[tuple[JsonObject, str, JsonObject]] = []
    for raw in proposed:
        if not isinstance(raw, dict):
            raise TaskFailure("invalid_tool_call", "tool call is not an object")
        name, args = _parse_tool_arguments(case, raw)
        parsed.append((raw, name, args))
    if int(entry["tool_call_count"]) + len(parsed) > case.max_tool_calls:
        entry["disallowed_tool_proposals"].append(
            {
                "call_number": index + 1,
                "names": [name for _, name, _ in parsed],
                "arguments": [args for _, _, args in parsed],
                "reason": "tool budget exceeded",
            }
        )
        raise TaskFailure(
            "tool_budget_exceeded",
            f"proposed {len(parsed)} call(s) beyond the {case.max_tool_calls} declared",
        )
    if index + 1 >= case.max_calls:
        raise TaskFailure(
            "tool_call_without_final_answer_budget",
            "a tool call on the last permitted model call leaves no final answer",
        )
    messages.append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": raw.get("id"),
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": raw["function"]["arguments"],
                    },
                }
                for raw, name, _ in parsed
            ],
        }
    )
    if message is not None:
        # Keep provider reasoning/signature metadata required for subsequent
        # Anthropic tool turns, as the baseline runner does. It remains data.
        messages[-1] = {**message, "role": "assistant"}
    for raw, name, args in parsed:
        result = simulate(case, name, args, cursors)
        entry["tool_steps"].append({"name": name, "arguments": args, "result": result})
        entry["tool_call_count"] = int(entry["tool_call_count"]) + 1
        messages.append(
            {
                "role": "tool",
                "tool_call_id": raw.get("id"),
                "content": json.dumps(result, ensure_ascii=False),
            }
        )
    write_state(ctx.state_path, ctx.state)


# ---------------------------------------------------------------------------
# Results import
# ---------------------------------------------------------------------------


def _call_evidence(call: Mapping[str, Any]) -> JsonObject:
    """Per-call evidence for a reviewer. Raw provider payloads stay private."""
    return {
        "number": call.get("number"),
        "status": call.get("status"),
        "route_id": call.get("route_id"),
        "requested_model": call.get("requested_model"),
        "runtime_route_id": call.get("runtime_route_id"),
        "runtime_model": call.get("runtime_model"),
        "actual_model_reported": call.get("actual_model_reported"),
        "provider_reported": call.get("provider_reported"),
        "provider_pin": call.get("provider_pin"),
        "reasoning_effort_requested": call.get("reasoning_effort_requested"),
        "reasoning_tokens": call.get("reasoning_tokens"),
        "max_output_tokens": call.get("max_output_tokens"),
        "input_token_bound": call.get("input_token_bound"),
        "input_token_estimate": call.get("input_token_estimate"),
        "reservation_bound_microusd": call.get("reservation_bound_microusd"),
        "account_charge_microusd": call.get("account_charge_microusd"),
        "provider_cost_usd": call.get("provider_cost_usd"),
        "usage": call.get("usage"),
        "elapsed_seconds": call.get("elapsed_seconds"),
    }


#: Optional per-attempt enrichment for a re-run's results import. It may only add
#: keys: a collision with an accounting or evidence key is refused, so an
#: extension can never rewrite a recorded status, cost or verdict.
AttemptExtension = Callable[[Attempt, CaseFixture, JsonObject], JsonObject]


def pending_results(
    state: Mapping[str, Any],
    cases: Mapping[str, CaseFixture],
    schedule: Sequence[Attempt],
    exposure: reliability.LedgerExposure | None = None,
    *,
    profile: ExperimentProfile = DEFAULT_PROFILE,
    attempt_extension: AttemptExtension | None = None,
    document_extension: Mapping[str, Any] | None = None,
) -> JsonObject:
    """Build the human-adjudication import. No verdict is ever assigned here.

    Every attempt is ``pending`` with a ``null`` reviewer, and every cost that the
    provider or the ledger did not report stays ``null`` rather than becoming
    zero, so an unknown invoice cost can never be ranked as free. The optional
    extensions let a separately identified experiment add its own review metadata;
    neither may replace a key this builder already records.
    """
    attempts: list[JsonObject] = []
    for attempt in schedule:
        entry = state.get("attempts", {}).get(attempt.attempt_id)
        if not isinstance(entry, dict):
            continue
        raw_calls = entry.get("calls")
        calls = raw_calls if isinstance(raw_calls, list) else []
        charges = [call.get("account_charge_microusd") for call in calls]
        known_charges = [charge for charge in charges if isinstance(charge, int)]
        costs = [call.get("provider_cost_usd") for call in calls]
        known_costs = [cost for cost in costs if isinstance(cost, (int, float))]
        case = cases[attempt.case_id]
        row: JsonObject = {
            "attempt_id": attempt.attempt_id,
            "case_id": attempt.case_id,
            "stage": attempt.stage,
            "latency_class": attempt.latency_class,
            "candidate_label": attempt.candidate_label,
            "condition": attempt.condition,
            "requested_reasoning_effort": attempt.effort,
            "repeat": attempt.repeat,
            "status": entry.get("status"),
            "calls_used": len(calls),
            "tool_calls_used": entry.get("tool_call_count"),
            "max_tool_calls": case.max_tool_calls,
            "latency_seconds": entry.get("latency_seconds"),
            "schema_valid": entry.get("schema_valid"),
            "cost_usd": sum(known_costs) if calls and len(known_costs) == len(calls) else None,
            "account_charge_microusd": (
                sum(known_charges) if calls and len(known_charges) == len(calls) else None
            ),
            "calls_with_unknown_charge": sum(
                1 for charge in charges if not isinstance(charge, int)
            ),
            "task_failures": entry.get("task_failures", []),
            "disallowed_tool_proposals": entry.get("disallowed_tool_proposals", []),
            "stop": entry.get("stop"),
            "final_response": entry.get("final_response"),
            "tool_steps": entry.get("tool_steps", []),
            "semantic_verdict": "pending",
            "reviewer": None,
            "calls": [_call_evidence(call) for call in calls],
        }
        if attempt_extension is not None:
            extra = attempt_extension(attempt, case, dict(entry))
            collide = sorted(set(extra) & set(row))
            require(
                not collide,
                f"attempt extension collides with reserved key(s) {', '.join(collide)}",
            )
            row.update(extra)
        attempts.append(row)
    baseline = state.get("account_ledger_baseline")
    document: JsonObject = {
        "artifact_version": profile.results_artifact_version,
        "experiment": profile.experiment,
        "generated_by": profile.generated_by,
        "identity": state.get("identity"),
        "fixtures_sha256": (state.get("identity") or {}).get("fixtures_sha256"),
        "stages": state.get("stages", {}),
        "account_ledger": {
            "baseline": baseline,
            "recorded_microusd": accounted_microusd(state),
            "recorded_calls": recorded_calls(state),
            "observed": None
            if exposure is None
            else {
                "exposure_microusd": exposure.total_microusd,
                "open_holds": exposure.open_holds,
            },
        },
        "caps": {
            "incremental_cap_microusd": profile.incremental_cap_microusd,
            "aggregate_cap_microusd": live.CAP_MICROUSD,
        },
        "counts": {
            "scheduled_attempts": len(schedule),
            "recorded_attempts": len(attempts),
            "dispatch_bound": profile.dispatch_bound,
            "recorded_dispatches": recorded_calls(state),
        },
        "notes": profile.results_notes,
        "attempts": attempts,
    }
    if document_extension is not None:
        collide = sorted(set(document_extension) & set(document))
        require(
            not collide,
            f"document extension collides with reserved key(s) {', '.join(collide)}",
        )
        document.update(document_extension)
    return document


# ---------------------------------------------------------------------------
# Phases and CLI
# ---------------------------------------------------------------------------


def record_stage(state: JsonObject, phase: str, identity: JsonObject) -> None:
    """Record which stage is running against which frozen artifact bytes.

    Both stages are pinned to the same fixture digest and schedule fingerprint, so
    a held-out run can never be paired with a retuned diagnostic corpus, and
    neither stage can be re-planned after results are seen.
    """
    stages = state.setdefault("stages", {})
    recorded = {
        "fixtures_sha256": identity["fixtures_sha256"],
        "schedule_sha256": identity["schedule_sha256"],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    existing = stages.get(phase)
    if isinstance(existing, dict):
        require(
            existing.get("fixtures_sha256") == recorded["fixtures_sha256"]
            and existing.get("schedule_sha256") == recorded["schedule_sha256"],
            "stage artifact drift",
        )
        return
    stages[phase] = recorded


def phase_admission(
    state: Mapping[str, Any],
    schedule: Sequence[Attempt],
    phase: str,
    *,
    profile: ExperimentProfile = DEFAULT_PROFILE,
) -> None:
    """The gated phase opens only after every gate-stage attempt is recorded."""
    require(phase in profile.phase_stages, f"unknown phase {phase!r}")
    planned = {attempt.attempt_id for attempt in schedule}
    for attempt_id, entry in state.get("attempts", {}).items():
        require(attempt_id in planned, "unplanned recorded attempt")
        require(
            isinstance(entry, dict)
            and entry.get("status") in {"completed", "failed"}
            and not entry.get("stop"),
            "recorded stop or interrupted attempt requires investigation; no automatic resume",
        )
    if phase != profile.gated_phase:
        return
    gate = tuple(attempt for attempt in schedule if attempt.stage == profile.gate_stage)
    missing = [
        attempt.attempt_id
        for attempt in gate
        if attempt.attempt_id not in state.get("attempts", {})
    ]
    require(
        not missing,
        f"{profile.phase_labels[phase]} requires all {len(gate)} recorded "
        f"{profile.gate_stage} attempts; {len(missing)} unrecorded",
    )


async def dry_run_report(
    args: argparse.Namespace,
    fixtures: FixtureSet,
    cases: Mapping[str, CaseFixture],
    routes: Mapping[str, RoutePolicy],
    schedule: Sequence[Attempt],
    pool: Any,
    service: EntitlementService,
    identity: JsonObject,
) -> JsonObject:
    """Read-only preflight: eligibility for every case and condition, plus caps.

    No state file is created, no ledger row is written and no inference call is
    made. Eligibility is checked for every (case, condition) pair the phase would
    dispatch, and the maximum reachable bound for the whole frozen schedule is
    checked against both the incremental sub-cap and the shared aggregate cap.
    """
    resolved = await service.resolve(args.account)
    ceiling = reliability.account_ceiling(resolved)
    exposure = await reliability.read_ledger_exposure(pool, args.account, args.period)
    # Every case and condition in the whole frozen corpus is checked, not only
    # the requested phase, so an unreachable held-out case cannot be discovered
    # after the diagnostic stage has been spent.
    conditions: set[str] = set()
    by_candidate: dict[str, int] = {}
    for attempt in schedule:
        case = cases[attempt.case_id]
        route = routes[attempt.candidate_label]
        params = request_for(case, route, attempt, initial_messages(case))
        verify_candidate(case, route, resolved, params)
        conditions.add(attempt.condition)
        by_candidate[attempt.candidate_label] = (
            by_candidate.get(attempt.candidate_label, 0) + ceiling_bound(route) * case.max_calls
        )
    upper_bound = sum(by_candidate.values())
    require_accounting(
        upper_bound <= INCREMENTAL_CAP_MICROUSD,
        "the frozen schedule's maximum reachable charge exceeds the incremental sub-cap",
    )
    require_accounting(
        exposure.total_microusd + upper_bound <= ceiling,
        "the frozen schedule's maximum reachable charge exceeds the remaining account allowance",
    )
    return {
        "dry_run": True,
        "phase": args.phase,
        "identity": identity,
        "cases": len(fixtures.cases),
        "conditions": sorted(conditions),
        "eligibility_checked": len(schedule),
        "phase_dispatch_bound": len(schedule_for_stage(schedule, args.phase))
        * MAX_CALLS_PER_ATTEMPT,
        "whole_experiment_dispatch_bound": DISPATCH_BOUND,
        "whole_experiment_max_reachable_microusd": upper_bound,
        "whole_experiment_max_reachable_microusd_by_candidate": dict(sorted(by_candidate.items())),
        "incremental_cap_microusd": INCREMENTAL_CAP_MICROUSD,
        "account_ceiling_microusd": ceiling,
        "ledger_exposure_microusd": exposure.total_microusd,
        "open_holds": exposure.open_holds,
    }


async def run(args: argparse.Namespace) -> int:
    """Preflight, admit and execute one stage of the frozen schedule."""
    validate_period(args.period)
    commercial_path, inference_path = live.policy_paths()
    policy = load_policy(commercial_path)
    live.validate_commercial(policy)
    inference = load_inference_policy(inference_path)
    fixtures = load_fixtures(args.fixtures)
    try:
        pins = json.loads(args.pins.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FollowupError(f"pins: cannot read {args.pins}: {exc}") from exc
    require(isinstance(pins, dict), "pins must be an object mapping labels to route IDs")
    routes = qualified_routes(pins, inference)
    if any(route.route_class == "premium" for route in routes.values()):
        require(
            Capability.PREMIUM_ROUTING in policy.plan("pro").capabilities,
            "premium route needs evaluation pro-plan capability (no trial funding)",
        )
    schedule = build_schedule(fixtures)
    require(args.results != args.state, "results path must differ from the state path")
    require(not args.results.exists(), "results file already exists")
    require(args.results.parent.is_dir(), "results directory missing")
    require(
        "DATABASE_URL" in Settings.explicit_evaluation_environment()
        and get_settings().database_url
        == Settings.explicit_evaluation_environment()["DATABASE_URL"],
        "explicit evaluation DATABASE_URL required",
    )
    if not args.dry_run:
        require(bool(get_settings().openrouter_api_key), "OpenRouter credential missing")
    pool = await asyncpg.create_pool(
        dsn=Settings.explicit_evaluation_environment()["DATABASE_URL"], min_size=1, max_size=2
    )
    try:
        service = EntitlementService(pool)
        database = await live.validate_database(pool, args.account, service, args.period)
        identity: JsonObject = {
            "experiment": EXPERIMENT,
            "account": str(args.account),
            "period_key": args.period,
            "database": database,
            "database_url_sha256": hashlib.sha256(
                Settings.explicit_evaluation_environment()["DATABASE_URL"].encode("utf-8")
            ).hexdigest(),
            "fixtures_sha256": fixtures.sha256,
            "schedule_sha256": schedule_fingerprint(schedule),
            "implementation_sha256": implementation_fingerprint(),
            "pins": {label: routes[label].route_id for label in sorted(routes)},
            "commercial_sha256": live.digest(commercial_path),
            "inference_sha256": live.digest(inference_path),
        }
        cases = fixtures.by_id()
        if args.dry_run:
            report = await dry_run_report(
                args, fixtures, cases, routes, schedule, pool, service, identity
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0
        with locked_state(args.state, args.account, identity) as state:
            phase_admission(state, schedule, args.phase)
            exposure = await reliability.read_ledger_exposure(pool, args.account, args.period)
            freeze_baseline(state, args.state, exposure, args.period)
            require_exclusive(state, exposure)
            record_stage(state, args.phase, identity)
            write_state(args.state, state)
            ctx = RunContext(
                pool=pool,
                account=args.account,
                service=service,
                period=args.period,
                identity=identity,
                state=state,
                state_path=args.state,
                schedule=schedule,
                cases=cases,
                routes=routes,
                commercial_path=commercial_path,
                inference_path=inference_path,
                account_ceiling_microusd=reliability.account_ceiling(
                    await service.resolve(args.account)
                ),
            )
            try:
                for attempt in schedule_for_stage(schedule, args.phase):
                    if attempt.attempt_id in state["attempts"]:
                        # Recorded, including in-progress: never replayed.
                        continue
                    await run_attempt(ctx, attempt)
            finally:
                final = await reliability.read_ledger_exposure(pool, args.account, args.period)
                write_results(args.results, pending_results(state, cases, schedule, final))
    finally:
        await pool.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES_PATH)
    parser.add_argument(
        "--pins",
        type=Path,
        required=True,
        help=(
            "JSON object mapping exactly luna, sol and sonnet to their qualified route "
            "IDs; each pinned route must serve that candidate's reviewed model"
        ),
    )
    parser.add_argument(
        "--phase",
        choices=STAGES,
        required=True,
        help="diagnostic runs the 72 paired attempts; heldout runs the 48 explicit ones",
    )
    parser.add_argument("--account", type=uuid.UUID, required=True)
    parser.add_argument("--period", required=True, help="original funded UTC month, YYYY-MM")
    parser.add_argument(
        "--state",
        type=Path,
        required=True,
        help="private persistent JSON state; parent directory must be private (0700)",
    )
    parser.add_argument(
        "--results",
        type=Path,
        required=True,
        help="new results artifact for human adjudication; the path must not exist",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read-only preflight: eligibility, caps and ledger reads, no inference",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except (
        FollowupError,
        reliability.ReliabilityError,
        EntitlementsError,
        compute_runtime.ComputeUnavailable,
        OSError,
        ValueError,
        asyncpg.PostgresError,
    ) as exc:
        print(f"follow-up run rejected: {reliability.sanitize(str(exc))}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("follow-up run interrupted", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"follow-up run failed: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
