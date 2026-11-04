"""Select the dev2 pilot set and materialise one dataset per arm.

Selection is metadata-only and follows ``holdout.py``/``dev2.py``: it reads
``question_id``, ``question_type`` and ``answer_session_ids`` through
``project_metadata`` and nothing else. Volume numbers reuse
``dev2.selected_row_aggregates``/``dev2.estimate_volumes`` so a row outside an
arm contributes nothing beyond its ``question_id``.

The pilot size and the arm sizes are fixed constants; the allocation is
largest-remainder over ``QuestionMetadata.stratum`` and every rank is a
SHA-256 digest of ``[ALGORITHM, seed, stratum, question_id]``, so the result is
reproducible from the IDs and the seed alone. Arm files are written with
``open("x")`` and never overwritten; the manifest carries IDs plus numbers, no
question, session or answer text. Nothing here calls a provider, opens a
database, touches the network or reads the benchmark dataset directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from tests.benchmark_longmemeval.dev2 import (
    QuestionAggregate,
    estimate_volumes,
    selected_row_aggregates,
)
from tests.benchmark_longmemeval.holdout import QuestionMetadata, project_metadata

PILOT_SIZE: Final[int] = 14
ARM_SIZES: Final[dict[int, int]] = {1: 2, 4: 4, 8: 8}
# Distinct from ``holdout.ALGORITHM`` and ``dev2.ALGORITHM``.
ALGORITHM: Final[str] = "dev2-pilot-stratified-largest-remainder-round-robin-sha256-v1"
MANIFEST_NAME: Final[str] = "pilot_manifest.json"


def _validated_ids(ids: Collection[str], label: str) -> list[str]:
    if isinstance(ids, (str, bytes)) or not isinstance(ids, Collection):
        raise ValueError(f"{label} must be a collection of IDs")
    output: list[str] = []
    seen: set[str] = set()
    for value in ids:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label} must be nonempty strings")
        if value in seen:
            raise ValueError(f"{label} must be unique")
        seen.add(value)
        output.append(value)
    return output


def _rows_by_id(rows: Sequence[QuestionMetadata]) -> dict[str, QuestionMetadata]:
    index: dict[str, QuestionMetadata] = {}
    for row in rows:
        if not isinstance(row, QuestionMetadata):
            raise ValueError("Rows must be QuestionMetadata values")
        if row.question_id in index:
            raise ValueError("Duplicate question IDs")
        index[row.question_id] = row
    return index


def _rank(seed: int, stratum: str, question_id: str) -> str:
    payload = json.dumps([ALGORITHM, seed, stratum, question_id], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _largest_remainder(counts: Mapping[str, int], size: int) -> dict[str, int]:
    """Allocate ``size`` seats over ``counts``, ties broken by stratum name."""
    total = sum(counts.values())
    if size > total:
        raise ValueError("size exceeds the number of available rows")
    quotas: dict[str, int] = {}
    remainders: list[tuple[int, str]] = []
    assigned = 0
    for stratum, count in counts.items():
        floor, remainder = divmod(size * count, total)
        quotas[stratum] = floor
        assigned += floor
        remainders.append((-remainder, stratum))
    remainders.sort()
    for _, stratum in remainders[: size - assigned]:
        quotas[stratum] += 1
    return quotas


def select_pilot(
    rows: list[QuestionMetadata],
    dev2_ids: Collection[str],
    *,
    size: int = PILOT_SIZE,
    seed: int = 42,
) -> list[str]:
    """Pick ``size`` dev2 IDs, stratified by ``QuestionMetadata.stratum``.

    One seat allocation is computed with largest remainders over the strata of
    the whole dev2 set, so every stratum is represented in proportion. Within a
    stratum, IDs are ordered by the SHA-256 rank of
    ``[ALGORITHM, seed, stratum, question_id]`` and the top ``quota`` are taken.
    """
    ids = _validated_ids(dev2_ids, "dev2_ids")
    if not ids:
        raise ValueError("dev2_ids must not be empty")
    if size <= 0:
        raise ValueError("size must be positive")
    if size > len(ids):
        raise ValueError("size exceeds the number of dev2 IDs")
    index = _rows_by_id(rows)
    missing = [question_id for question_id in ids if question_id not in index]
    if missing:
        raise ValueError(f"{len(missing)} dev2 IDs are absent from the metadata rows")
    strata = Counter(index[question_id].stratum for question_id in ids)
    quotas = _largest_remainder(strata, size)
    selected: list[str] = []
    for stratum in sorted(strata):
        ranked = sorted(
            (question_id for question_id in ids if index[question_id].stratum == stratum),
            key=lambda question_id: (_rank(seed, stratum, question_id), question_id),
        )
        selected.extend(ranked[: quotas[stratum]])
    return sorted(selected)


def assign_arms(
    rows: list[QuestionMetadata],
    pilot_ids: Collection[str],
    *,
    seed: int = 42,
) -> dict[int, list[str]]:
    """Deal the pilot IDs into the fixed arms.

    Rule: order the pilot IDs by ``(stratum, SHA-256 rank)``, then deal them
    round-robin across the arms in ascending arm order, skipping an arm once it
    is full, until every arm holds exactly ``ARM_SIZES[arm]`` IDs. Consecutive
    same-stratum IDs land in different arms, so each arm gets a mix of strata.
    The pilot must contain exactly ``sum(ARM_SIZES.values())`` IDs, and every ID
    lands in exactly one arm.
    """
    ids = _validated_ids(pilot_ids, "pilot_ids")
    index = _rows_by_id(rows)
    missing = [question_id for question_id in ids if question_id not in index]
    if missing:
        raise ValueError(f"{len(missing)} pilot IDs are absent from the metadata rows")
    capacities = dict(sorted(ARM_SIZES.items()))
    if len(ids) != sum(capacities.values()):
        raise ValueError("pilot size must equal the total arm capacity")
    order = sorted(
        (
            (
                index[question_id].stratum,
                _rank(seed, index[question_id].stratum, question_id),
                question_id,
            )
            for question_id in ids
        ),
        key=lambda item: (item[0], item[1], item[2]),
    )
    arms = list(capacities)
    result: dict[int, list[str]] = {arm: [] for arm in arms}
    cursor = 0
    for _, _, question_id in order:
        for _ in arms:
            arm = arms[cursor % len(arms)]
            cursor += 1
            if len(result[arm]) < capacities[arm]:
                result[arm].append(question_id)
                break
        else:  # pragma: no cover - guarded by the capacity check above
            raise ValueError("no arm capacity left")
    return result


def _row_id(row: Any) -> str:
    if not isinstance(row, dict):
        raise ValueError("Dataset rows must be objects")
    question_id = row.get("question_id")
    if not isinstance(question_id, str) or not question_id:
        raise ValueError("Question IDs must be nonempty strings")
    return question_id


def write_arm_datasets(
    dataset_rows: list[Any],
    arms: Mapping[int, Collection[str]],
    out_dir: Path,
    *,
    dev2_ids: Collection[str],
    holdout_ids: Collection[str],
) -> dict[int, str]:
    """Write ``arm_c<n>.json`` per arm and return ``{arm: sha256}``.

    Each file is a JSON list of the full original rows for that arm's IDs, in
    dataset order, serialised as ``json.dumps(rows, ensure_ascii=False)`` plus a
    final newline. Files are opened with ``"x"`` and are never overwritten.
    Everything is validated before the first file is created: an ID outside
    ``dev2_ids``, an ID in ``holdout_ids``, a duplicate arm ID or an ID absent
    from the dataset raises and leaves ``out_dir`` untouched.
    """
    allowed = frozenset(_validated_ids(dev2_ids, "dev2_ids"))
    holdout = frozenset(_validated_ids(holdout_ids, "holdout_ids"))
    plan: dict[int, list[str]] = {}
    seen: set[str] = set()
    for arm in sorted(arms):
        ids = _validated_ids(arms[arm], f"arm {arm} IDs")
        for question_id in ids:
            if question_id in holdout:
                raise ValueError("refusing to write a holdout ID into an arm")
            if question_id not in allowed:
                raise ValueError("refusing to write an ID outside the dev2 set")
            if question_id in seen:
                raise ValueError("an ID may appear in only one arm")
            seen.add(question_id)
        plan[arm] = ids
    wanted = set(seen)
    rows_by_id: dict[str, Any] = {}
    for row in dataset_rows:
        question_id = _row_id(row)
        if question_id in wanted:
            if question_id in rows_by_id:
                raise ValueError("Duplicate question IDs in the dataset")
            rows_by_id[question_id] = row
    if wanted - set(rows_by_id):
        raise ValueError("Arm IDs must exist in the dataset")
    out_dir.mkdir(parents=True, exist_ok=True)
    hashes: dict[int, str] = {}
    for arm in sorted(plan):
        members = frozenset(plan[arm])
        payload = json.dumps(
            [row for row in dataset_rows if row.get("question_id") in members],
            ensure_ascii=False,
        )
        text = payload + "\n"
        with open(out_dir / f"arm_c{arm}.json", "x", encoding="utf-8", newline="") as handle:
            handle.write(text)
        hashes[arm] = hashlib.sha256(text.encode()).hexdigest()
    return hashes


def pilot_volume(dataset_rows: list[Any], arms: Mapping[int, Collection[str]]) -> dict[str, Any]:
    """Per-arm and total ingest volumes, as numbers only.

    Uses ``dev2.selected_row_aggregates`` so only the arm IDs' rows are read
    beyond their ``question_id``; ``estimate_volumes`` supplies the numbers.
    """
    per_arm: dict[int, list[QuestionAggregate]] = {}
    for arm in sorted(arms):
        per_arm[arm] = selected_row_aggregates(dataset_rows, arms[arm])
    total: list[QuestionAggregate] = [row for arm in per_arm for row in per_arm[arm]]
    return {
        "arms": {arm: estimate_volumes(rows) for arm, rows in per_arm.items()},
        "total": estimate_volumes(total),
    }


def spend_projection(
    volume: Mapping[str, Any],
    *,
    usd_per_session: float,
    usd_per_1k_tokens: float,
) -> dict[str, float]:
    """Token- and session-scaled USD for the total, both labelled estimates."""
    for name, value in (
        ("usd_per_session", usd_per_session),
        ("usd_per_1k_tokens", usd_per_1k_tokens),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a number")
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
    total = volume["total"]
    tokens = total["estimated_tokens"]
    sessions = total["estimated_ingest_sessions"]
    return {
        "estimated_token_scaled_usd": float(tokens) / 1000.0 * float(usd_per_1k_tokens),
        "estimated_session_scaled_usd": float(sessions) * float(usd_per_session),
    }


def _load_json(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _manifest_ids(manifest: Any, label: str) -> Any:
    """IDs from a frozen manifest; a bare mapping must never validate as its keys."""
    if not isinstance(manifest, Mapping) or "question_ids" not in manifest:
        raise ValueError(f"{label} must be a manifest with question_ids")
    return _validated_ids(manifest["question_ids"], label)


def prepare(
    *,
    dataset_path: Path,
    dev2_path: Path,
    holdout_path: Path,
    out_dir: Path,
    size: int = PILOT_SIZE,
    seed: int = 42,
) -> dict[str, Any]:
    """Select the pilot, write its arm files and an IDs-plus-numbers manifest."""
    dataset_bytes = dataset_path.read_bytes()
    dev2_bytes = dev2_path.read_bytes()
    holdout_bytes = holdout_path.read_bytes()
    dataset = json.loads(dataset_bytes)
    dev2_ids = _manifest_ids(json.loads(dev2_bytes), "dev2_ids")
    holdout_ids = _manifest_ids(json.loads(holdout_bytes), "holdout_ids")
    rows = project_metadata(dataset)
    pilot_ids = select_pilot(rows, dev2_ids, size=size, seed=seed)
    arms = assign_arms(rows, pilot_ids, seed=seed)
    hashes = write_arm_datasets(dataset, arms, out_dir, dev2_ids=dev2_ids, holdout_ids=holdout_ids)
    manifest: dict[str, Any] = {
        "algorithm": ALGORITHM,
        "seed": seed,
        "size": size,
        "pilot_ids": pilot_ids,
        "arms": {str(arm): ids for arm, ids in sorted(arms.items())},
        "arm_sha256": {str(arm): digest for arm, digest in sorted(hashes.items())},
        "volumes": pilot_volume(dataset, arms),
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "dev2_ids_sha256": hashlib.sha256(dev2_bytes).hexdigest(),
        "holdout_ids_sha256": hashlib.sha256(holdout_bytes).hexdigest(),
    }
    path = out_dir / MANIFEST_NAME
    with open(path, "x", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return manifest


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--dev2", type=Path, required=True)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--size", type=int, default=PILOT_SIZE)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    prepare(
        dataset_path=args.dataset,
        dev2_path=args.dev2,
        holdout_path=args.holdout,
        out_dir=args.out_dir,
        size=args.size,
        seed=args.seed,
    )


# --- Pilot runtime instrumentation -------------------------------------------
#
# Everything below is injected-only: no process, database, network or provider
# call is made here. Counters return numbers (or IDs already present in an
# injected checkpoint); the runner never logs question or session text.

STOP_SPEND: Final[str] = "spend_cap"
STOP_RATE_LIMIT: Final[str] = "rate_limit_loop"
STOP_EXTRACTION: Final[str] = "extraction_failed"
# Hard gate: an arm with at least one failed session and >= 1% failures stops.
FAILED_RATE_GATE: Final[float] = 0.01

SessionExtractor = Callable[[Mapping[str, Any], str], tuple[Sequence[str], Sequence[str]]]


class ProcessHandle(Protocol):
    """The two methods ``run_arm`` needs from an injected child process."""

    def poll(self) -> int | None: ...

    def wait(self) -> int: ...


def count_rate_limits(log_lines: Iterable[str]) -> dict[str, int]:
    """Count ``attempt_outcome`` lines by ``failure_category``.

    Only JSON object lines carrying ``"event": "attempt_outcome"`` are read;
    every other line (junk, partial writes, other events) is ignored. The result
    is a plain ``{category: count}`` mapping and carries no message text.
    """
    counts: Counter[str] = Counter()
    for line in log_lines:
        if not isinstance(line, str):
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if not isinstance(payload, dict) or payload.get("event") != "attempt_outcome":
            continue
        category = payload.get("failure_category")
        if isinstance(category, str) and category:
            counts[category] += 1
    return dict(sorted(counts.items()))


def _ingest_results(checkpoint: Mapping[str, Any]) -> Mapping[str, Any]:
    """``phases.ingest.results``; a missing or foreign shape must not read as zero work."""
    results = checkpoint["phases"]["ingest"]["results"]
    if not isinstance(results, Mapping):
        raise ValueError("checkpoint ingest results must be a mapping")
    return results


def default_session_extractor(
    checkpoint: Mapping[str, Any], question_id: str
) -> tuple[Sequence[str], Sequence[str]]:
    """Sessions recorded for one question in a harness checkpoint.

    ``phases.ingest.results`` maps ``ingest_result_key`` (``question:corpus_key``,
    ``fact_harness.py:736``) to a result whose ``status`` is ``complete`` on success
    (``fact_harness.py:1631``). Keys are unique, so a duplicate can't appear here: the
    resume check must also compare database conversation counts.
    """
    results = _ingest_results(checkpoint)
    prefix = f"{question_id}:"
    sessions: list[str] = []
    failed: list[str] = []
    for key, result in results.items():
        if not isinstance(key, str) or not key.startswith(prefix):
            continue
        session = key[len(prefix) :]
        sessions.append(session)
        if not isinstance(result, Mapping) or result.get("status") != "complete":
            failed.append(session)
    return sessions, failed


def checkpoint_invariants(
    checkpoint: Mapping[str, Any], *, extract: SessionExtractor | None = None
) -> dict[str, Any]:
    """Invariants for every question present in ``checkpoint``."""
    question_ids = sorted({str(key).split(":", 1)[0] for key in _ingest_results(checkpoint)})
    return session_invariants(checkpoint, question_ids, extract=extract)


def session_invariants(
    checkpoint: Mapping[str, Any],
    question_ids: Collection[str],
    *,
    extract: SessionExtractor | None = None,
) -> dict[str, Any]:
    """Per-question session counts, failed counts and duplicate detection.

    ``extract`` returns ``(session_ids, failed_session_ids)`` for one question
    and defaults to :func:`default_session_extractor`. Duplicates are repeated
    session IDs *within* a question. ``completed`` is ``sessions - failed``.
    """
    reader: SessionExtractor = extract or default_session_extractor
    per_question: dict[str, dict[str, int]] = {}
    total = 0
    failed_total = 0
    duplicates = 0
    for question_id in question_ids:
        sessions, failed_ids = reader(checkpoint, question_id)
        ids = [str(value) for value in sessions]
        counts = Counter(ids)
        duplicate_count = sum(count - 1 for count in counts.values() if count > 1)
        duplicates += duplicate_count
        total += len(ids)
        failed_total += len(failed_ids)
        per_question[question_id] = {
            "sessions": len(ids),
            "failed": len(failed_ids),
            "completed": len(ids) - len(failed_ids),
            "duplicate_sessions": duplicate_count,
        }
    return {
        "questions": per_question,
        "total_sessions": total,
        "failed_sessions": failed_total,
        "completed_sessions": total - failed_total,
        "duplicate_sessions": duplicates,
    }


def failed_rate(invariants: Mapping[str, Any]) -> float:
    """Failed sessions divided by all sessions; ``0.0`` when there are none."""
    total = int(invariants.get("total_sessions", 0))
    if total <= 0:
        return 0.0
    return int(invariants.get("failed_sessions", 0)) / total


def verify_resume(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    expected_sessions: int | Mapping[str, int],
) -> list[str]:
    """Compare invariants before and after a resume. Empty list means pass.

    Violations, in order: any duplicate session record after resume, any
    completed session lost, a session count that differs from
    ``expected_sessions`` (a total, or per question ID), or more
    ``extraction_failed`` sessions than before.
    """
    violations: list[str] = []
    duplicates = int(after.get("duplicate_sessions", 0))
    if duplicates > 0:
        violations.append(f"resume duplicated {duplicates} session record(s)")
    lost = int(before.get("completed_sessions", 0)) - int(after.get("completed_sessions", 0))
    if lost > 0:
        violations.append(f"resume lost {lost} completed session(s)")
    if isinstance(expected_sessions, int):
        observed = int(after.get("total_sessions", 0))
        if observed != expected_sessions:
            violations.append(
                f"session count {observed} does not match expected {expected_sessions}"
            )
    else:
        observed_questions = after.get("questions", {})
        for question_id, expected in sorted(expected_sessions.items()):
            record = observed_questions.get(question_id)
            actual = int(record["sessions"]) if isinstance(record, Mapping) else 0
            if actual != expected:
                violations.append(
                    f"question {question_id} has {actual} sessions, expected {expected}"
                )
        if not isinstance(observed_questions, Mapping):
            violations.append("resumed checkpoint has no per-question records")
    extra_failures = int(after.get("failed_sessions", 0)) - int(before.get("failed_sessions", 0))
    if extra_failures > 0:
        violations.append(f"resume added {extra_failures} extraction_failed session(s)")
    return violations


@dataclass(frozen=True)
class StopRule:
    """Stopping limits for one arm: spend cap, safety margin, 429 tolerance."""

    cap_usd: float
    margin_usd: float
    max_rate_limited: int

    def __post_init__(self) -> None:
        for name in ("cap_usd", "margin_usd"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be a finite nonnegative number")
        if self.cap_usd <= 0:
            raise ValueError("cap_usd must be positive")
        if self.margin_usd >= self.cap_usd:
            raise ValueError("margin_usd must be smaller than cap_usd")
        if (
            isinstance(self.max_rate_limited, bool)
            or not isinstance(self.max_rate_limited, int)
            or self.max_rate_limited < 0
        ):
            raise ValueError("max_rate_limited must be a nonnegative integer")

    @property
    def spend_threshold_usd(self) -> float:
        return self.cap_usd - self.margin_usd


def _resource_bounds(samples: Sequence[Mapping[str, float]]) -> dict[str, dict[str, float]]:
    bounds: dict[str, dict[str, float]] = {}
    for sample in samples:
        for name, value in sample.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                continue
            number = float(value)
            current = bounds.get(name)
            if current is None:
                bounds[name] = {"min": number, "max": number}
            else:
                if number < current["min"]:
                    current["min"] = number
                if number > current["max"]:
                    current["max"] = number
    return bounds


def _gate_reason(
    spend_usd: float,
    invariants: Mapping[str, Any],
    rate_limits: Mapping[str, int],
    stop_rule: StopRule,
    expected_sessions: Mapping[str, int] | None = None,
) -> str | None:
    if spend_usd >= stop_rule.spend_threshold_usd:
        return STOP_SPEND
    if int(rate_limits.get("rate_limited", 0)) > stop_rule.max_rate_limited:
        return STOP_RATE_LIMIT
    failed = int(invariants.get("failed_sessions", 0))
    if failed >= 1 and failed_rate(invariants) >= FAILED_RATE_GATE:
        return STOP_EXTRACTION
    return None


def _completed_questions(
    invariants: Mapping[str, Any], expected_sessions: Mapping[str, int] | None
) -> int:
    """Questions whose every expected session finished complete."""
    if expected_sessions is None:
        raise ValueError("interrupt_after_completed requires expected_sessions")
    questions = invariants.get("questions", {})
    return sum(
        1
        for question_id, expected in expected_sessions.items()
        if int(questions.get(question_id, {}).get("completed", 0)) >= expected > 0
    )


def run_arm(
    *,
    command: Sequence[str],
    spawn: Callable[[Sequence[str]], ProcessHandle],
    read_spend_usd: Callable[[], float],
    read_log_lines: Callable[[], Sequence[str]],
    read_checkpoint: Callable[[], Mapping[str, Any]],
    sample_resources: Callable[[], Mapping[str, float]],
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    poll_seconds: float,
    stop_rule: StopRule,
    interrupt_after_completed: int | None = None,
    expected_sessions: Mapping[str, int] | None = None,
    send_interrupt: Callable[[ProcessHandle], None],
    grace_seconds: float = 60.0,
) -> dict[str, Any]:
    """Spawn one ingest arm, poll it, and stop it on a documented trigger.

    Every side effect is injected. The loop samples resources and reads spend,
    log counts and checkpoint invariants each poll. One SIGINT is sent when the
    spend threshold ``cap_usd - margin_usd`` is reached (``spend_cap``), when
    ``rate_limited`` exceeds ``max_rate_limited`` (``rate_limit_loop``), or when
    at least one session has failed and the failed rate is >= 1%
    (``extraction_failed``). If ``interrupt_after_completed`` is set, one SIGINT
    is sent once that many questions completed and the run is marked
    ``interrupted_for_resume`` with no stop reason. A second SIGINT is sent at
    most once, only after ``grace_seconds`` with the child still alive, and is
    recorded. The result carries numbers and reason codes only.
    """
    if poll_seconds <= 0 or not math.isfinite(poll_seconds):
        raise ValueError("poll_seconds must be a positive finite number")
    if grace_seconds < 0 or not math.isfinite(grace_seconds):
        raise ValueError("grace_seconds must be a nonnegative finite number")
    if interrupt_after_completed is not None and interrupt_after_completed < 1:
        raise ValueError("interrupt_after_completed must be at least 1")

    started = clock()
    spend_before = float(read_spend_usd())
    samples: list[Mapping[str, float]] = []
    interrupts: list[dict[str, Any]] = []
    stop_reason: str | None = None
    interrupted = False
    first_interrupt_at: float | None = None
    second_sent = False
    process = spawn(list(command))

    while process.poll() is None:
        samples.append(sample_resources())
        now = clock()
        if first_interrupt_at is not None:
            if not second_sent and now - first_interrupt_at >= grace_seconds:
                send_interrupt(process)
                second_sent = True
                interrupts.append({"at_seconds": round(now - started, 3), "kind": "grace"})
        else:
            invariants = checkpoint_invariants(read_checkpoint())
            rate_limits = count_rate_limits(read_log_lines())
            reason: str | None = None
            resumed = False
            if (
                interrupt_after_completed is not None
                and _completed_questions(invariants, expected_sessions) >= interrupt_after_completed
            ):
                resumed = True
            else:
                reason = _gate_reason(float(read_spend_usd()), invariants, rate_limits, stop_rule)
            if resumed or reason is not None:
                send_interrupt(process)
                first_interrupt_at = now
                if resumed:
                    interrupted = True
                    kind = "resume"
                else:
                    stop_reason = reason
                    kind = "stop"
                interrupts.append({"at_seconds": round(now - started, 3), "kind": kind})
        sleep(poll_seconds)

    exit_code = process.wait()
    samples.append(sample_resources())
    elapsed = max(clock() - started, 0.0)
    spend_after = float(read_spend_usd())
    final_invariants = checkpoint_invariants(read_checkpoint())
    final_rates = count_rate_limits(read_log_lines())
    completed = int(final_invariants.get("completed_sessions", 0))
    hours = elapsed / 3600.0
    return {
        "wall_seconds": elapsed,
        "exit_code": exit_code,
        "stop_reason": stop_reason,
        "interrupted_for_resume": interrupted,
        "interrupts": interrupts,
        "spend_before_usd": spend_before,
        "spend_after_usd": spend_after,
        "spend_usd": spend_after - spend_before,
        "rate_limits": final_rates,
        "rate_limited_events": int(final_rates.get("rate_limited", 0)),
        "resource_samples": _resource_bounds(samples),
        "completed_sessions": completed,
        "failed_sessions": int(final_invariants.get("failed_sessions", 0)),
        "sessions_per_hour": completed / hours if hours > 0 else 0.0,
    }


if __name__ == "__main__":  # pragma: no cover
    main()
