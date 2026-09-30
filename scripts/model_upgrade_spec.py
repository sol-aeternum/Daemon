#!/usr/bin/env python3
"""Bounded, reusable spec layer for manifest-driven model-upgrade screens.

This module plans and reports; it never executes. A frozen, strictly validated
manifest (schema version 1) is loaded into an immutable :class:`UpgradeSpec` and
expanded into a deterministic attempt schedule; a recorded results document is
turned into pure, read-only analysis views. Dispatch, accounting, admission and
durability stay in the reviewed accounting executor
(``scripts/model_routing_followup.py``), which an upgrade CLI drives through its
own experiment profile. This layer holds no credentials, opens no connections
and writes no files.

The manifest is data, never authorization. It deliberately carries no approvals
and no qualification keys - the exact-key schema below leaves no room for them -
and nothing here activates a route: the independently supplied inference policy
remains authoritative, and the executor re-checks every pin itself.

Manifest schema (exact keys; unknown or missing keys are refused)::

    {
      "schema_version": 1,
      "experiment": "<safe slug, not a historical experiment>",
      "period": "YYYY-MM",
      "fixtures": {"path": "<repo-relative, no traversal>", "sha256": "<64 hex>"},
      "candidates": [
        {"label": "<safe slug>", "model": "openrouter/<model, no /latest>",
         "route_id": "<safe slug>", "provider_pin": "<non-empty>"}
      ],
      "conditions": [
        {"label": "<safe slug, not 'probe'>", "effort": "<safe slug>",
         "case_ids": ["<fixture case id>", ...], "repeats": <int > 0>}
      ],
      "bounds": {
        "max_context_tokens": 16000, "max_output_tokens": 4096,
        "max_calls_per_attempt": 3, "attempt_deadline_seconds": 90,
        "incremental_cap_microusd": <int > 0, <= aggregate>,
        "aggregate_cap_microusd": 25000000
      },
      "probes": [
        {"candidate_label": "<declared label>", "effort": "<safe slug>",
         "case_id": "<fixture case id>", "max_calls": 2}
      ],
      "screening": {
        "regression_case_ids": ["H01", "...", "H08"],
        "minimum_acceptable_answers": 15,
        "hard_violations_allowed": 0,
        "wrong_identifier_fetches_allowed": 0,
        "latency_screens_seconds": {"utility": 10, "orchestration": 30, "synthesis": 60}
      }
    }

The fixed execution constants are pinned to the accounting executor's own values,
so a manifest cannot lengthen the deadline or widen the token or call envelope;
only the incremental cap is a manifest choice, bounded by the shared aggregate
ceiling. Screening constants are pinned to the approved plan in the same way. A
condition's effort is an opaque measured setting sent verbatim - ``high`` and
``xhigh`` are settings, not internal budgets, and no numeric meaning is assigned
here; whether a model accepts an effort is the executor catalog's check.

Every probe case must come from the declared fixture corpus, declare at most one
tool with a tool budget of at most one, and run at most two model calls; the
derived dispatch bound counts two calls per probe, never the regular three, so
the probe phase cannot widen the envelope. Every declared regression case must
be covered by at least one condition, so the declared screen is executable.

The schedule alternates candidates inside every (case, repeat, setting) group so
matched pairs are adjacent, emits the diagnostic phase (diagnostic-stage cases)
before the regression phase (held-out stage cases, each condition in declared
order, so ``high`` precedes ``xhigh`` when declared so), and appends every probe
last as its own streaming phase. Attempt ids join dot-separated components -
case, candidate, setting, repeat - so different settings of the same case can
never collide. Probes carry the reserved condition label ``probe`` and repeat
``0``.

Reporting is observations only. :func:`result_summary` aggregates recorded
latencies (nearest-rank p95 over every recorded attempt, failures included),
subgroup ``n``/``p95``/``max`` statistics, matched paired deltas across
candidates, and costs only where every contributing attempt is known - unknown
costs stay ``None`` and are never scored as zero, and failed or unanswered
attempts keep their costs in the totals. It assigns no acceptance: semantic
verdicts are counted exactly as found, and a per-acceptable cost is derived only
from explicit verdicts attributed to a named human reviewer. Rows without a
well-formed (case, repeat, setting) identity or without a measured latency
cannot form a matched pair and are excluded from the delta list only; they remain
counted everywhere else. :func:`review_packet` renders the model-blinded human
review packet: opaque, deterministic, key-derived labels replace attempt ids,
candidate, condition and effort identities are omitted, and the label mapping is
returned separately so it never travels with the packet text or items.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Final, NoReturn

from scripts import model_routing_followup as follow

JsonObject = dict[str, Any]

# ---------------------------------------------------------------------------
# Frozen schema constants
# ---------------------------------------------------------------------------

#: The only manifest schema this module accepts. A material manifest change is a
#: new, separately identified schema version, never a silent edit.
SCHEMA_VERSION: Final[int] = 1

_TOP_LEVEL_KEYS: Final[tuple[str, ...]] = (
    "schema_version",
    "experiment",
    "period",
    "fixtures",
    "candidates",
    "conditions",
    "bounds",
    "probes",
    "screening",
)

#: Execution phases, in the order the schedule emits them. ``regression`` is the
#: held-out stage under its measured settings; ``streaming`` is the probe phase.
PHASES: Final[tuple[str, ...]] = ("diagnostic", "regression", "streaming")

#: Attempt stages. ``streaming`` is the probe stage; probes always run last.
STAGES: Final[tuple[str, ...]] = ("diagnostic", "heldout", "streaming")

#: The condition label every probe attempt carries. Reserved: a manifest
#: condition may not reuse it, so probe rows stay identifiable in results and
#: attempt ids cannot collide across the two kinds of attempt.
PROBE_CONDITION_LABEL: Final[str] = "probe"

#: Historical experiment identifiers, in slug form, that a new manifest may not
#: reuse, so a new screen can neither adopt nor be confused with an earlier one:
#: - ``scripts/model_routing_followup.py`` ``EXPERIMENT`` ("model-routing-followup/1")
#: - ``scripts/model_endpoint_reliability.py`` ``STATE_VERSION``
#:   ("model-endpoint-reliability-state/1")
#: - ``scripts/model_roster_pilot.py`` ``FIXTURES_ARTIFACT_VERSION``
#:   ("model-roster-pilot-fixtures/1")
HISTORICAL_EXPERIMENT_SLUGS: Final[frozenset[str]] = frozenset(
    {"model-routing-followup", "model-endpoint-reliability", "model-roster-pilot"}
)

#: The probe envelope. Two model calls and at most one tool per probe case; the
#: derived dispatch bound counts exactly two calls per probe.
PROBE_MAX_CALLS: Final[int] = 2
PROBE_MAX_TOOL_CALLS: Final[int] = 1
PROBE_MAX_TOOLS: Final[int] = 1

#: Approved screening constants, pinned to the plan. A manifest may not loosen
#: them; loosening one is a new plan, not a manifest field.
MINIMUM_ACCEPTABLE_ANSWERS: Final[int] = 15
HARD_VIOLATIONS_ALLOWED: Final[int] = 0
WRONG_IDENTIFIER_FETCHES_ALLOWED: Final[int] = 0
LATENCY_SCREENS_SECONDS: Final[tuple[tuple[str, float], ...]] = (
    ("utility", 10.0),
    ("orchestration", 30.0),
    ("synthesis", 60.0),
)

_BOUNDS_KEYS: Final[tuple[str, ...]] = (
    "max_context_tokens",
    "max_output_tokens",
    "max_calls_per_attempt",
    "attempt_deadline_seconds",
    "incremental_cap_microusd",
    "aggregate_cap_microusd",
)

_SCREENING_KEYS: Final[tuple[str, ...]] = (
    "regression_case_ids",
    "minimum_acceptable_answers",
    "hard_violations_allowed",
    "wrong_identifier_fetches_allowed",
    "latency_screens_seconds",
)

_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")

_HEX_DIGITS: Final[frozenset[str]] = frozenset("0123456789abcdef")


# ---------------------------------------------------------------------------
# Errors and validation helpers
# ---------------------------------------------------------------------------


class SpecError(Exception):
    """A manifest, fixture reference or results document this layer refused."""


def _fail(where: str, message: str) -> NoReturn:
    raise SpecError(f"{where}: {message}")


def _mapping(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(where, f"expected a JSON object, got {type(value).__name__}")
    return value


def _sequence(value: object, where: str) -> list[object]:
    if not isinstance(value, list):
        _fail(where, f"expected a JSON array, got {type(value).__name__}")
    return value


def _exact_keys(obj: Mapping[str, Any], where: str, required: Sequence[str]) -> None:
    missing = [key for key in required if key not in obj]
    if missing:
        _fail(where, f"missing required key(s) {', '.join(missing)}")
    unknown = sorted(key for key in obj if key not in required)
    if unknown:
        _fail(where, f"unknown key(s) {', '.join(unknown)}")


def _string(value: object, where: str) -> str:
    if not isinstance(value, str):
        _fail(where, f"expected a string, got {type(value).__name__}")
    if not value.strip():
        _fail(where, "expected a non-empty string")
    return value


def _slug(value: object, where: str) -> str:
    slug = _string(value, where)
    if not _SLUG_RE.fullmatch(slug):
        _fail(
            where,
            "expected a safe slug of lowercase letters, digits and hyphens "
            f"(not starting or ending with a hyphen), got {slug!r}",
        )
    return slug


def _sha256_hex(value: object, where: str) -> str:
    digest = _string(value, where)
    if len(digest) != 64 or not set(digest) <= _HEX_DIGITS:
        _fail(where, "expected a lowercase 64-character SHA-256 hex digest")
    return digest


def _integer(
    value: object, where: str, *, minimum: int | None = None, maximum: int | None = None
) -> int:
    """A strict JSON integer. Booleans are not numbers and floats are not ints."""
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(where, f"expected an integer, got {value!r}")
    if minimum is not None and value < minimum:
        _fail(where, f"expected an integer >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        _fail(where, f"expected an integer <= {maximum}, got {value}")
    return value


def _number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(where, f"expected a number, got {value!r}")
    return float(value)


def _equal(value: object, expected: object, where: str) -> None:
    """Pin a manifest value to its approved constant; a boolean never passes."""
    if value != expected or isinstance(value, bool) != isinstance(expected, bool):
        _fail(where, f"must equal the approved value {expected!r}")


def _validate_period(value: object) -> str:
    period = _string(value, "manifest.period")
    try:
        wellformed = (
            len(period) == 7 and datetime.strptime(period, "%Y-%m").strftime("%Y-%m") == period
        )
    except ValueError:
        wellformed = False
    if not wellformed:
        _fail("manifest.period", "expected YYYY-MM")
    return period


def _resolve_repo_relative(repo_root: Path, value: object) -> Path:
    """Resolve a repo-relative fixture path that cannot escape the repository."""
    relative = _string(value, "fixtures.path")
    candidate = Path(relative)
    if candidate.is_absolute():
        _fail("fixtures.path", "must be repo-relative, not absolute")
    if ".." in candidate.parts:
        _fail("fixtures.path", "must not traverse outside the repository")
    resolved = (repo_root / candidate).resolve()
    if not resolved.is_relative_to(repo_root.resolve()):
        _fail("fixtures.path", "must resolve inside the repository")
    if not resolved.is_file():
        _fail("fixtures.path", f"fixture file not found: {resolved}")
    return resolved


# ---------------------------------------------------------------------------
# Frozen spec objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FixtureRef:
    """The fixture corpus a manifest pins: a repo-relative path and its digest."""

    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class CandidateSpec:
    """One candidate: an opaque label plus the exact reviewed model identity.

    The model string is an identity, not an approval: the route pin is resolved
    against the independently supplied inference policy by the executor.
    """

    label: str
    model: str
    route_id: str
    provider_pin: str


@dataclass(frozen=True, slots=True)
class ConditionSpec:
    """One measured reasoning-effort setting over an explicit case set.

    ``effort`` is sent verbatim as the setting this condition measures; it is not
    an internal budget and carries no numeric meaning here. ``case_ids`` are the
    explicit fixture cases the setting applies to.
    """

    label: str
    effort: str
    case_ids: tuple[str, ...]
    repeats: int


@dataclass(frozen=True, slots=True)
class ProbeSpec:
    """One streaming probe: a single candidate, effort and fixture case.

    A probe is a narrow live check, not a scored attempt: at most two model calls
    against a case that declares at most one tool with a tool budget of one.
    """

    candidate_label: str
    effort: str
    case_id: str
    max_calls: int


@dataclass(frozen=True, slots=True)
class BoundsSpec:
    """The frozen execution envelope, pinned to the executor's own constants."""

    max_context_tokens: int
    max_output_tokens: int
    max_calls_per_attempt: int
    attempt_deadline_seconds: float
    incremental_cap_microusd: int
    aggregate_cap_microusd: int


@dataclass(frozen=True, slots=True)
class ScreeningSpec:
    """The declared human-screening reference numbers.

    These are declarations for human adjudication, not an evaluation this module
    runs: nothing here compares observed results against them.
    """

    regression_case_ids: tuple[str, ...]
    minimum_acceptable_answers: int
    hard_violations_allowed: int
    wrong_identifier_fetches_allowed: int
    latency_screens_seconds: tuple[tuple[str, float], ...]

    def latency_screen_seconds(self, latency_class: str) -> float:
        for name, seconds in self.latency_screens_seconds:
            if name == latency_class:
                return seconds
        _fail("screening.latency_screens_seconds", f"no screen declared for {latency_class!r}")


@dataclass(frozen=True, slots=True)
class UpgradeSpec:
    """One frozen, separately identified upgrade experiment.

    Every field is immutable; collections are tuples. ``manifest_sha256`` freezes
    the exact manifest bytes the schedule was planned from. ``total_attempts``
    and ``dispatch_bound`` are derived from the declared parts - probes count two
    calls each, never the regular three - and are never stored in the manifest.
    """

    schema_version: int
    experiment: str
    period: str
    manifest_sha256: str
    fixtures: FixtureRef
    candidates: tuple[CandidateSpec, ...]
    conditions: tuple[ConditionSpec, ...]
    bounds: BoundsSpec
    probes: tuple[ProbeSpec, ...]
    screening: ScreeningSpec

    @property
    def candidate_labels(self) -> tuple[str, ...]:
        return tuple(candidate.label for candidate in self.candidates)

    @property
    def condition_labels(self) -> tuple[str, ...]:
        return tuple(condition.label for condition in self.conditions)

    @property
    def total_attempts(self) -> int:
        regular = sum(len(c.case_ids) * c.repeats for c in self.conditions)
        return regular * len(self.candidates) + len(self.probes)

    @property
    def dispatch_bound(self) -> int:
        regular = sum(len(c.case_ids) * c.repeats for c in self.conditions)
        return regular * len(self.candidates) * self.bounds.max_calls_per_attempt + sum(
            probe.max_calls for probe in self.probes
        )

    def candidate(self, label: str) -> CandidateSpec:
        for candidate in self.candidates:
            if candidate.label == label:
                return candidate
        _fail("candidates", f"unknown candidate label {label!r}")

    def condition(self, label: str) -> ConditionSpec:
        for condition in self.conditions:
            if condition.label == label:
                return condition
        _fail("conditions", f"unknown condition label {label!r}")


@dataclass(frozen=True, slots=True)
class PlannedAttempt:
    """One immutable scheduled attempt of a manifest-driven upgrade screen.

    This is the planning-layer record. The execution CLI converts it into the
    accounting executor's own ``Attempt`` - with an explicit condition and a
    concrete effort - so dispatch, admission and durability stay there. A probe
    attempt's ``max_calls`` (two) is part of the plan; the conversion must honor
    it instead of the case's regular three-call envelope.
    """

    attempt_id: str
    case_id: str
    stage: str
    phase: str
    candidate_label: str
    condition_label: str
    effort: str
    repeat: int
    max_calls: int
    max_tool_calls: int
    latency_class: str
    is_probe: bool


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------


def _parse_fixture_ref(value: object) -> FixtureRef:
    obj = _mapping(value, "fixtures")
    _exact_keys(obj, "fixtures", ("path", "sha256"))
    return FixtureRef(
        path=_string(obj["path"], "fixtures.path"),
        sha256=_sha256_hex(obj["sha256"], "fixtures.sha256"),
    )


def _parse_candidates(value: object) -> tuple[CandidateSpec, ...]:
    items = _sequence(value, "candidates")
    if not items:
        _fail("candidates", "expected at least one candidate")
    labels: set[str] = set()
    models: set[str] = set()
    route_ids: set[str] = set()
    candidates: list[CandidateSpec] = []
    for index, item in enumerate(items):
        where = f"candidates[{index}]"
        obj = _mapping(item, where)
        _exact_keys(obj, where, ("label", "model", "route_id", "provider_pin"))
        label = _slug(obj["label"], f"{where}.label")
        if label in labels:
            _fail(f"{where}.label", f"duplicate candidate label {label!r}")
        labels.add(label)
        model = _string(obj["model"], f"{where}.model")
        if not model.startswith("openrouter/") or len(model) <= len("openrouter/"):
            _fail(f"{where}.model", "expected an exact openrouter/ model identity")
        if "/latest" in model or any(char.isspace() for char in model):
            _fail(f"{where}.model", "must be an exact model identity without /latest aliases")
        if model in models:
            _fail(f"{where}.model", f"duplicate candidate model {model!r}")
        models.add(model)
        route_id = _slug(obj["route_id"], f"{where}.route_id")
        if route_id in route_ids:
            _fail(f"{where}.route_id", f"duplicate candidate route id {route_id!r}")
        route_ids.add(route_id)
        candidates.append(
            CandidateSpec(
                label=label,
                model=model,
                route_id=route_id,
                provider_pin=_string(obj["provider_pin"], f"{where}.provider_pin"),
            )
        )
    return tuple(candidates)


def _parse_case_ids(value: object, where: str) -> tuple[str, ...]:
    items = _sequence(value, where)
    if not items:
        _fail(where, "expected at least one case id")
    case_ids: list[str] = []
    for index, item in enumerate(items):
        case_id = _string(item, f"{where}[{index}]")
        if case_id in case_ids:
            _fail(f"{where}[{index}]", f"duplicate case id {case_id!r}")
        case_ids.append(case_id)
    return tuple(case_ids)


def _parse_conditions(value: object) -> tuple[ConditionSpec, ...]:
    items = _sequence(value, "conditions")
    if not items:
        _fail("conditions", "expected at least one condition")
    labels: set[str] = set()
    conditions: list[ConditionSpec] = []
    for index, item in enumerate(items):
        where = f"conditions[{index}]"
        obj = _mapping(item, where)
        _exact_keys(obj, where, ("label", "effort", "case_ids", "repeats"))
        label = _slug(obj["label"], f"{where}.label")
        if label == PROBE_CONDITION_LABEL:
            _fail(f"{where}.label", f"{PROBE_CONDITION_LABEL!r} is a reserved condition label")
        if label in labels:
            _fail(f"{where}.label", f"duplicate condition label {label!r}")
        labels.add(label)
        conditions.append(
            ConditionSpec(
                label=label,
                # An opaque measured setting, sent verbatim. Whether the model
                # accepts it is the executor catalog's check, not this schema's.
                effort=_slug(obj["effort"], f"{where}.effort"),
                case_ids=_parse_case_ids(obj["case_ids"], f"{where}.case_ids"),
                repeats=_integer(obj["repeats"], f"{where}.repeats", minimum=1),
            )
        )
    return tuple(conditions)


def _parse_bounds(value: object) -> BoundsSpec:
    obj = _mapping(value, "bounds")
    _exact_keys(obj, "bounds", _BOUNDS_KEYS)
    # The execution envelope is pinned to the executor's own constants, so a
    # manifest cannot lengthen the deadline or widen the token or call budget;
    # only the incremental cap is a manifest choice under the shared ceiling.
    for field, parse, expected in (
        ("max_context_tokens", _integer, follow.MAX_CONTEXT_TOKENS),
        ("max_output_tokens", _integer, follow.MAX_OUTPUT_TOKENS),
        ("max_calls_per_attempt", _integer, follow.MAX_CALLS_PER_ATTEMPT),
        ("attempt_deadline_seconds", _number, follow.ATTEMPT_DEADLINE_S),
        ("aggregate_cap_microusd", _integer, follow.live.CAP_MICROUSD),
    ):
        where = f"bounds.{field}"
        _equal(parse(obj[field], where), expected, where)
    incremental = _integer(
        obj["incremental_cap_microusd"], "bounds.incremental_cap_microusd", minimum=1
    )
    if incremental > follow.live.CAP_MICROUSD:
        _fail(
            "bounds.incremental_cap_microusd",
            f"must not exceed the aggregate ceiling of {follow.live.CAP_MICROUSD}",
        )
    return BoundsSpec(
        max_context_tokens=follow.MAX_CONTEXT_TOKENS,
        max_output_tokens=follow.MAX_OUTPUT_TOKENS,
        max_calls_per_attempt=follow.MAX_CALLS_PER_ATTEMPT,
        attempt_deadline_seconds=follow.ATTEMPT_DEADLINE_S,
        incremental_cap_microusd=incremental,
        aggregate_cap_microusd=follow.live.CAP_MICROUSD,
    )


def _parse_probes(value: object, candidate_labels: set[str]) -> tuple[ProbeSpec, ...]:
    items = _sequence(value, "probes")
    seen: set[tuple[str, str, str]] = set()
    probes: list[ProbeSpec] = []
    for index, item in enumerate(items):
        where = f"probes[{index}]"
        obj = _mapping(item, where)
        _exact_keys(obj, where, ("candidate_label", "effort", "case_id", "max_calls"))
        candidate_label = _string(obj["candidate_label"], f"{where}.candidate_label")
        if candidate_label not in candidate_labels:
            _fail(f"{where}.candidate_label", f"unknown candidate label {candidate_label!r}")
        effort = _slug(obj["effort"], f"{where}.effort")
        case_id = _string(obj["case_id"], f"{where}.case_id")
        max_calls = _integer(obj["max_calls"], f"{where}.max_calls")
        if max_calls != PROBE_MAX_CALLS:
            _fail(f"{where}.max_calls", f"a probe runs exactly {PROBE_MAX_CALLS} model calls")
        identity = (candidate_label, effort, case_id)
        if identity in seen:
            _fail(where, f"duplicate probe {candidate_label!r}/{effort!r}/{case_id!r}")
        seen.add(identity)
        probes.append(
            ProbeSpec(
                candidate_label=candidate_label, effort=effort, case_id=case_id, max_calls=max_calls
            )
        )
    return tuple(probes)


def _parse_screening(value: object) -> ScreeningSpec:
    obj = _mapping(value, "screening")
    _exact_keys(obj, "screening", _SCREENING_KEYS)
    declared = [
        _string(item, f"screening.regression_case_ids[{index}]")
        for index, item in enumerate(
            _sequence(obj["regression_case_ids"], "screening.regression_case_ids")
        )
    ]
    if tuple(declared) != follow.HELD_OUT_CASE_IDS:
        _fail(
            "screening.regression_case_ids",
            "must be exactly the held-out set " + ", ".join(follow.HELD_OUT_CASE_IDS),
        )
    for field, expected in (
        ("minimum_acceptable_answers", MINIMUM_ACCEPTABLE_ANSWERS),
        ("hard_violations_allowed", HARD_VIOLATIONS_ALLOWED),
        ("wrong_identifier_fetches_allowed", WRONG_IDENTIFIER_FETCHES_ALLOWED),
    ):
        where = f"screening.{field}"
        _equal(_integer(obj[field], where), expected, where)
    screens_obj = _mapping(obj["latency_screens_seconds"], "screening.latency_screens_seconds")
    _exact_keys(
        screens_obj,
        "screening.latency_screens_seconds",
        tuple(name for name, _seconds in LATENCY_SCREENS_SECONDS),
    )
    for name, seconds in LATENCY_SCREENS_SECONDS:
        where = f"screening.latency_screens_seconds.{name}"
        _equal(_number(screens_obj[name], where), seconds, where)
    return ScreeningSpec(
        regression_case_ids=tuple(declared),
        minimum_acceptable_answers=MINIMUM_ACCEPTABLE_ANSWERS,
        hard_violations_allowed=HARD_VIOLATIONS_ALLOWED,
        wrong_identifier_fetches_allowed=WRONG_IDENTIFIER_FETCHES_ALLOWED,
        latency_screens_seconds=LATENCY_SCREENS_SECONDS,
    )


_ParsedManifest = tuple[
    str,
    str,
    FixtureRef,
    tuple[CandidateSpec, ...],
    tuple[ConditionSpec, ...],
    BoundsSpec,
    tuple[ProbeSpec, ...],
    ScreeningSpec,
]


def _parse_manifest(document: object) -> _ParsedManifest:
    obj = _mapping(document, "manifest")
    _exact_keys(obj, "manifest", _TOP_LEVEL_KEYS)
    _equal(
        _integer(obj["schema_version"], "manifest.schema_version"),
        SCHEMA_VERSION,
        "manifest.schema_version",
    )
    experiment = _slug(obj["experiment"], "manifest.experiment")
    if experiment in HISTORICAL_EXPERIMENT_SLUGS:
        _fail(
            "manifest.experiment",
            f"{experiment!r} is a historical experiment identifier; use a new one",
        )
    candidates = _parse_candidates(obj["candidates"])
    return (
        experiment,
        _validate_period(obj["period"]),
        _parse_fixture_ref(obj["fixtures"]),
        candidates,
        _parse_conditions(obj["conditions"]),
        _parse_bounds(obj["bounds"]),
        _parse_probes(obj["probes"], set(candidate.label for candidate in candidates)),
        _parse_screening(obj["screening"]),
    )


def _validate_spec_against_fixtures(spec: UpgradeSpec, fixtures: follow.FixtureSet) -> None:
    """Check every declared reference against the actual corpus.

    Called by both :func:`load_spec` and :func:`build_schedule`, so a directly
    constructed spec is held to the same rules as a loaded one.
    """
    cases = fixtures.by_id()
    for index, condition in enumerate(spec.conditions):
        for case_id in condition.case_ids:
            if case_id not in cases:
                _fail(
                    f"conditions[{index}].case_ids",
                    f"unknown case id {case_id!r} in the fixture corpus",
                )
    covered = {case_id for condition in spec.conditions for case_id in condition.case_ids}
    missing = [case_id for case_id in spec.screening.regression_case_ids if case_id not in covered]
    if missing:
        _fail(
            "screening.regression_case_ids",
            f"no condition covers {', '.join(missing)}",
        )
    for index, probe in enumerate(spec.probes):
        where = f"probes[{index}]"
        if probe.candidate_label not in spec.candidate_labels:
            _fail(f"{where}.candidate_label", f"unknown candidate label {probe.candidate_label!r}")
        case = cases.get(probe.case_id)
        if case is None:
            _fail(f"{where}.case_id", f"unknown case id {probe.case_id!r} in the fixture corpus")
        if len(case.tools) > PROBE_MAX_TOOLS or case.max_tool_calls > PROBE_MAX_TOOL_CALLS:
            _fail(
                f"{where}.case_id",
                f"case {probe.case_id} must declare at most {PROBE_MAX_TOOLS} tool(s) "
                f"with a tool budget of {PROBE_MAX_TOOL_CALLS}",
            )
        if probe.max_calls > case.max_calls:
            _fail(
                f"{where}.max_calls",
                f"probe calls exceed case {probe.case_id}'s {case.max_calls}-call envelope",
            )


def load_spec(
    path: Path, repo_root: Path, fixtures: follow.FixtureSet | None = None
) -> UpgradeSpec:
    """Load, validate and freeze one upgrade manifest into an immutable spec.

    The manifest bytes are hashed into the spec's identity. When ``fixtures`` is
    omitted the declared corpus is loaded from its repo-relative path; either
    way the corpus digest must match the manifest's declared SHA-256, so a
    schedule can never be planned against different bytes than the manifest pins.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SpecError(f"manifest: cannot read {path}: {exc}") from exc
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SpecError(f"manifest: not valid UTF-8 JSON: {exc}") from exc
    (
        experiment,
        period,
        fixture_ref,
        candidates,
        conditions,
        bounds,
        probes,
        screening,
    ) = _parse_manifest(document)
    fixture_path = _resolve_repo_relative(repo_root, fixture_ref.path)
    if fixtures is None:
        fixtures = follow.load_fixtures(fixture_path)
    if fixtures.sha256 != fixture_ref.sha256:
        _fail(
            "fixtures.sha256",
            f"declared {fixture_ref.sha256} but the corpus digest is {fixtures.sha256}",
        )
    spec = UpgradeSpec(
        schema_version=SCHEMA_VERSION,
        experiment=experiment,
        period=period,
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
        fixtures=fixture_ref,
        candidates=candidates,
        conditions=conditions,
        bounds=bounds,
        probes=probes,
        screening=screening,
    )
    _validate_spec_against_fixtures(spec, fixtures)
    return spec


# ---------------------------------------------------------------------------
# Frozen schedule
# ---------------------------------------------------------------------------


def _planned_id(case_id: str, candidate_label: str, setting: str, tail: str) -> str:
    """Dot-separated, collision-safe id: no component may contain a dot.

    Case ids, safe slugs and the ``r<repeat>``/``probe`` markers never contain a
    dot, so the components stay unambiguous and two settings of the same case
    can never produce the same id.
    """
    return ".".join((case_id, candidate_label, setting, tail))


def build_schedule(spec: UpgradeSpec, fixtures: follow.FixtureSet) -> tuple[PlannedAttempt, ...]:
    """Expand a frozen spec into its deterministic attempt schedule.

    Order: every declared condition in turn (so a declared ``high`` precedes a
    declared ``xhigh``), its declared cases in order, each repeat, and inside
    each (case, repeat, setting) group the candidates alternating - which is
    what makes matched candidate pairs adjacent. Every probe is appended last as
    the streaming phase, in manifest order.
    """
    if fixtures.sha256 != spec.fixtures.sha256:
        _fail(
            "fixtures.sha256",
            f"the schedule's corpus digest {fixtures.sha256} does not match the "
            f"manifest's declared {spec.fixtures.sha256}",
        )
    _validate_spec_against_fixtures(spec, fixtures)
    cases = fixtures.by_id()
    attempts: list[PlannedAttempt] = []
    for condition in spec.conditions:
        for case_id in condition.case_ids:
            case = cases[case_id]
            for repeat in range(1, condition.repeats + 1):
                for candidate in spec.candidates:
                    attempts.append(
                        PlannedAttempt(
                            attempt_id=_planned_id(
                                case_id, candidate.label, condition.label, f"r{repeat}"
                            ),
                            case_id=case_id,
                            stage=case.stage,
                            phase="diagnostic" if case.stage == "diagnostic" else "regression",
                            candidate_label=candidate.label,
                            condition_label=condition.label,
                            effort=condition.effort,
                            repeat=repeat,
                            max_calls=case.max_calls,
                            max_tool_calls=case.max_tool_calls,
                            latency_class=case.latency_class,
                            is_probe=False,
                        )
                    )
    for probe in spec.probes:
        case = cases[probe.case_id]
        attempts.append(
            PlannedAttempt(
                attempt_id=_planned_id(
                    probe.case_id, probe.candidate_label, PROBE_CONDITION_LABEL, probe.effort
                ),
                case_id=probe.case_id,
                stage="streaming",
                phase="streaming",
                candidate_label=probe.candidate_label,
                condition_label=PROBE_CONDITION_LABEL,
                effort=probe.effort,
                repeat=0,
                max_calls=probe.max_calls,
                max_tool_calls=case.max_tool_calls,
                latency_class=case.latency_class,
                is_probe=True,
            )
        )
    ids = [attempt.attempt_id for attempt in attempts]
    if len(set(ids)) != len(ids):
        duplicates = sorted({attempt_id for attempt_id in ids if ids.count(attempt_id) > 1})
        _fail("schedule", f"duplicate attempt id(s) {', '.join(duplicates)}")
    if len(attempts) != spec.total_attempts:
        _fail(
            "schedule",
            f"expected {spec.total_attempts} derived attempts, built {len(attempts)}",
        )
    if sum(attempt.max_calls for attempt in attempts) != spec.dispatch_bound:
        _fail("schedule", "built call total does not match the derived dispatch bound")
    return tuple(attempts)


def schedule_payload(schedule: Sequence[PlannedAttempt]) -> tuple[JsonObject, ...]:
    """The frozen schedule as plain dicts (``dataclasses.asdict``), for identity."""
    return tuple(asdict(attempt) for attempt in schedule)


def schedule_sha256(schedule: Sequence[PlannedAttempt]) -> str:
    """A digest over the whole frozen schedule, stable across runs."""
    payload = json.dumps(list(schedule_payload(schedule)), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Results document views (pure: no writes, no mutation)
# ---------------------------------------------------------------------------


def _document_rows(document: Mapping[str, Any]) -> list[JsonObject]:
    attempts = document.get("attempts")
    if not isinstance(attempts, list):
        _fail("document.attempts", "expected a JSON array of attempt rows")
    rows: list[JsonObject] = []
    for index, row in enumerate(attempts):
        if not isinstance(row, dict):
            _fail(f"document.attempts[{index}]", "expected a JSON object row")
        attempt_id = row.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            _fail(f"document.attempts[{index}].attempt_id", "expected a non-empty string")
        rows.append(row)
    return rows


def _latency_of(row: Mapping[str, Any]) -> float | None:
    value = row.get("latency_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    """Nearest-rank percentile: rank ``ceil(q * n)`` into the sorted values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _latency_stats(values: Sequence[float]) -> JsonObject:
    return {
        "n": len(values),
        "p95": _percentile(values, 0.95),
        "max": max(values) if values else None,
    }


def _subgroup_stats(
    rows: Sequence[Mapping[str, Any]], key_of: Callable[[Mapping[str, Any]], object]
) -> dict[str, JsonObject]:
    buckets: dict[str, list[float]] = {}
    for row in rows:
        latency = _latency_of(row)
        if latency is None:
            continue
        key = key_of(row)
        name = key if isinstance(key, str) and key.strip() else "unknown"
        buckets.setdefault(name, []).append(latency)
    return {name: _latency_stats(values) for name, values in sorted(buckets.items())}


def _human_verdict(row: Mapping[str, Any]) -> str | None:
    """The row's verdict only when it is explicit and attributed to a reviewer.

    ``pending`` rows, missing verdicts and verdicts without a named human
    reviewer are not evidence of acceptance; they return ``None``.
    """
    verdict = row.get("semantic_verdict")
    reviewer = row.get("reviewer")
    if (
        isinstance(verdict, str)
        and verdict.strip()
        and verdict in {"acceptable", "unacceptable"}
        and isinstance(reviewer, str)
        and reviewer.strip()
    ):
        return verdict
    return None


def _counts(rows: Sequence[Mapping[str, Any]], key: str, missing: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(key)
        name = value if isinstance(value, str) and value.strip() else missing
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


def _paired_deltas(rows: Sequence[Mapping[str, Any]], spec: UpgradeSpec) -> list[JsonObject]:
    """Latency deltas for candidate pairs matched on case, repeat and setting.

    Probe rows are single-shot streaming checks, not paired settings, and rows
    without a measurable latency cannot form a pair; both are excluded here only.
    """
    groups: dict[tuple[str, int, str], dict[str, Mapping[str, Any]]] = {}
    for row in rows:
        if row.get("setting_label", row.get("condition")) == PROBE_CONDITION_LABEL:
            continue
        latency = _latency_of(row)
        case_id = row.get("case_id")
        condition = row.get("setting_label", row.get("condition"))
        repeat = row.get("repeat")
        candidate_label = row.get("candidate_label")
        if (
            latency is None
            or not isinstance(case_id, str)
            or not isinstance(condition, str)
            or not condition.strip()
            or not isinstance(repeat, int)
            or isinstance(repeat, bool)
            or not isinstance(candidate_label, str)
            or not candidate_label.strip()
        ):
            continue
        group = groups.setdefault((case_id, repeat, condition), {})
        if candidate_label in group:
            _fail("paired results", "duplicate case/repeat/setting/candidate")
        group[candidate_label] = row
    order = {label: index for index, label in enumerate(spec.candidate_labels)}
    deltas: list[JsonObject] = []
    for (case_id, repeat, condition), by_candidate in groups.items():
        labels = sorted(by_candidate, key=lambda label: (order.get(label, len(order)), label))
        for first in range(len(labels)):
            for second in range(first + 1, len(labels)):
                before, after = labels[first], labels[second]
                old, new = by_candidate[before], by_candidate[after]

                def cost_delta(key: str) -> float | int | None:
                    a, b = old.get(key), new.get(key)
                    if isinstance(a, bool) or isinstance(b, bool):
                        return None
                    return (
                        b - a
                        if isinstance(a, (int, float)) and isinstance(b, (int, float))
                        else None
                    )

                deltas.append(
                    {
                        "case_id": case_id,
                        "repeat": repeat,
                        "condition": condition,
                        "candidates": [before, after],
                        "latency_seconds": [old["latency_seconds"], new["latency_seconds"]],
                        "delta_seconds": new["latency_seconds"] - old["latency_seconds"],
                        "cost_delta_usd": cost_delta("cost_usd"),
                        "charge_delta_microusd": cost_delta("account_charge_microusd"),
                        "status": [old.get("status"), new.get("status")],
                    }
                )
    deltas.sort(
        key=lambda delta: (
            str(delta["case_id"]),
            delta["repeat"],
            str(delta["condition"]),
            delta["candidates"],
        )
    )
    return deltas


def result_summary(document: Mapping[str, Any], spec: UpgradeSpec) -> JsonObject:
    """Pure observational summary of a recorded results document.

    Latency statistics cover every recorded attempt, failures included. Cost and
    charge totals stay ``None`` unless every recorded attempt's own value is
    known - an unknown cost is never scored as zero - while failed and
    unanswered attempts keep contributing their known costs. Semantic verdicts
    are only counted as found: nothing here decides acceptance, and
    ``cost_per_acceptable`` stays ``None`` until every recorded attempt carries
    an explicit verdict attributed to a named human reviewer.
    """
    rows = _document_rows(document)
    recorded = len(rows)
    latencies = [latency for row in rows if (latency := _latency_of(row)) is not None]
    cost_values = [row.get("cost_usd") for row in rows]
    known_costs = [
        value
        for value in cost_values
        if not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value >= 0
    ]
    charge_values = [row.get("account_charge_microusd") for row in rows]
    known_charges = [
        value
        for value in charge_values
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
    ]
    unknown_charge_rows = sum(
        1 for value in charge_values if not isinstance(value, int) or isinstance(value, bool)
    )
    calls_with_unknown_charge = [row.get("calls_with_unknown_charge") for row in rows]
    unknown_call_counts: list[int] = []
    unknown_calls_all_known = True
    for value in calls_with_unknown_charge:
        if isinstance(value, int) and not isinstance(value, bool):
            unknown_call_counts.append(value)
        else:
            # A row that does not quantify its unknown calls leaves the total
            # unknown; it is never treated as zero.
            unknown_calls_all_known = False
    unknown_calls: int | None = (
        sum(unknown_call_counts) if unknown_calls_all_known and rows else None
    )
    human_reviewed = sum(1 for row in rows if _human_verdict(row) is not None)
    acceptable = [row for row in rows if _human_verdict(row) == "acceptable"]
    all_reviewed = recorded > 0 and human_reviewed == recorded
    total_cost: float | None = (
        float(sum(known_costs)) if rows and len(known_costs) == len(rows) else None
    )
    cost_per_acceptable: float | None = None
    if all_reviewed and total_cost is not None and acceptable:
        cost_per_acceptable = total_cost / len(acceptable)
    total_charge: int | None = (
        sum(known_charges) if rows and len(known_charges) == len(rows) else None
    )
    buckets: dict[tuple[str, str, str], list[JsonObject]] = {}
    for row in rows:
        group = (
            str(row.get("candidate_label")),
            str(row.get("setting_label", row.get("condition"))),
            str(row.get("phase", row.get("stage"))),
        )
        buckets.setdefault(group, []).append(row)
    comparison_groups = []
    for (candidate, setting, phase), group_rows in sorted(buckets.items()):
        times = [t for r in group_rows if (t := _latency_of(r)) is not None]
        costs = [r.get("cost_usd") for r in group_rows]
        charges = [r.get("account_charge_microusd") for r in group_rows]
        valid_costs = [
            c
            for c in costs
            if isinstance(c, (int, float))
            and not isinstance(c, bool)
            and math.isfinite(c)
            and c >= 0
        ]
        valid_charges = [
            c for c in charges if isinstance(c, int) and not isinstance(c, bool) and c >= 0
        ]
        cost = sum(valid_costs) if len(valid_costs) == len(costs) else None
        charge = sum(valid_charges) if len(valid_charges) == len(charges) else None
        accepted = sum(_human_verdict(r) == "acceptable" for r in group_rows)
        reviewed = all(_human_verdict(r) is not None for r in group_rows)
        comparison_groups.append(
            {
                "candidate_label": candidate,
                "setting": setting,
                "phase": phase,
                "attempts": len(group_rows),
                "status": _counts(group_rows, "status", "unknown"),
                "latency_seconds": {
                    **_latency_stats(times),
                    "median": median(times) if times else None,
                },
                "latency_classes": _subgroup_stats(group_rows, lambda r: r.get("latency_class")),
                "cost_usd": cost,
                "account_charge_microusd": charge,
                "semantic_verdict": _counts(group_rows, "semantic_verdict", "missing"),
                "cost_per_acceptable_usd": cost / accepted
                if reviewed and accepted and cost is not None
                else None,
            }
        )
    return {
        "experiment": spec.experiment,
        "period": spec.period,
        "notes": (
            "Observations only. No semantic verdict is assigned or implied here; "
            "acceptance is a human decision against the declared screening values."
        ),
        "counts": {
            "scheduled_attempts": spec.total_attempts,
            "recorded_attempts": recorded,
            "unrecorded_attempts": spec.total_attempts - recorded,
            "probe_attempts": len(spec.probes),
            "status": _counts(rows, "status", "unknown"),
            "semantic_verdict": _counts(rows, "semantic_verdict", "missing"),
            "human_reviewed": human_reviewed,
            "pending_or_unreviewed": recorded - human_reviewed,
        },
        "latency_seconds": {
            "all": _latency_stats(latencies),
            "by_stage": _subgroup_stats(rows, lambda row: row.get("stage")),
            "by_latency_class": _subgroup_stats(rows, lambda row: row.get("latency_class")),
            "by_candidate": _subgroup_stats(rows, lambda row: row.get("candidate_label")),
            "by_condition": _subgroup_stats(
                rows, lambda row: row.get("setting_label", row.get("condition"))
            ),
        },
        "paired_deltas_seconds": _paired_deltas(rows, spec),
        "comparison_groups": comparison_groups,
        "cost_usd": {
            "total": total_cost,
            "known_attempts": len(known_costs),
            "attempts_with_unknown_cost": recorded - len(known_costs),
            "cost_per_acceptable": cost_per_acceptable,
        },
        "account_charge_microusd": {
            "total": total_charge,
            "attempts_with_unknown_charge": unknown_charge_rows,
            "calls_with_unknown_charge": unknown_calls,
        },
    }


# ---------------------------------------------------------------------------
# Model-blinded human review packet
# ---------------------------------------------------------------------------


def _blind_label(key: str, attempt_id: str) -> str:
    """An opaque, deterministic label: keyed HMAC, not a reversible encoding."""
    digest = hmac.new(key.encode("utf-8"), attempt_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"R{digest[:12]}"


def _observed_tool_steps(row: Mapping[str, Any], where: str) -> list[JsonObject]:
    value = row.get("tool_steps")
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        _fail(f"{where}.tool_steps", "expected a JSON array of step objects")
    return list(value)


def review_packet(
    document: Mapping[str, Any],
    spec: UpgradeSpec,
    fixtures: follow.FixtureSet,
    *,
    key: str | None = None,
) -> tuple[str, list[JsonObject], dict[str, str]]:
    """Build the model-blinded human review packet.

    Returns the packet text, the blinded review items, and - separately, so it
    never travels with the packet - the mapping from each opaque label back to
    its attempt id. Items carry only the case prompt, rubric, declared tools and
    schema, the observed tool steps, the recorded status and the final response,
    plus the pending-verdict template; model, candidate, condition and effort
    identities are omitted. Labels are deterministic under a key (the frozen
    manifest digest by default, overridable for an independently keyed packet)
    and items are ordered by label, so the presentation order is a deterministic
    shuffle rather than the schedule order.
    """
    rows = _document_rows(document)
    digest_key = spec.manifest_sha256 if key is None else key
    if not isinstance(digest_key, str) or not digest_key.strip():
        _fail("review_packet.key", "expected a non-empty key")
    cases = fixtures.by_id()
    items: list[JsonObject] = []
    label_to_attempt: dict[str, str] = {}
    for index, row in enumerate(rows):
        where = f"document.attempts[{index}]"
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or case_id not in cases:
            _fail(f"{where}.case_id", f"unknown case id {case_id!r} in the fixture corpus")
        assert case_id is not None  # narrowed for the type checker
        case = cases[case_id]
        label = _blind_label(digest_key, row["attempt_id"])
        if label in label_to_attempt:
            _fail("review_packet", f"opaque label collision for {row['attempt_id']!r}")
        label_to_attempt[label] = row["attempt_id"]
        items.append(
            {
                "label": label,
                "case_id": case.case_id,
                "prompt": case.prompt,
                "rubric": case.expected.get("rubric"),
                "expected": case.expected,
                "tools": [dict(tool.function) for tool in case.tools],
                "schema": case.schema,
                "observed_tool_steps": _observed_tool_steps(row, where),
                "status": row.get("status"),
                "final_response": row.get("final_response"),
                "semantic_verdict": "pending",
                "reviewer": None,
                "hard_violation": None,
                "wrong_identifier_fetch": None,
                "failed_assertions": [],
                "notes": "",
            }
        )
    items.sort(key=lambda item: str(item["label"]))
    header = "\n".join(
        (
            "Model upgrade review packet",
            "===========================",
            "",
            f"period: {spec.period}",
            f"manifest_sha256: {spec.manifest_sha256}",
            f"attempts: {len(items)}",
            "",
            "Every verdict below is pending and no reviewer is recorded; record a human",
            "decision per item. This packet deliberately omits model, candidate, condition",
            "and effort identities. The opaque-label mapping is distributed separately.",
        )
    )
    blocks = [json.dumps(item, ensure_ascii=False, indent=2, sort_keys=True) for item in items]
    packet = "\n\n---\n\n".join([header, *blocks])
    return packet + "\n", items, label_to_attempt
