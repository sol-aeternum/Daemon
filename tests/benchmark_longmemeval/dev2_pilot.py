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

The runtime helpers at the bottom expose the SQL text, file readers and stdlib
resource sampling the driver injects into ``run_arm``; the driver still owns the
connection, the child process and the captured log.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

from orchestrator.routing_log import EVENT_FIELDS
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
    tokens = list(sys.argv[1:] if argv is None else argv)
    if tokens[:1] == ["run-arm"]:
        raise SystemExit(run_arm_main(tokens[1:]))
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

#: Pre-registration §6: isolated retried 429s aren't a stop; more than this per arm is.
DEFAULT_MAX_RATE_LIMITED: Final[int] = 100

STOP_SPEND: Final[str] = "spend_cap"
STOP_RATE_LIMIT: Final[str] = "rate_limit_loop"
STOP_EXTRACTION: Final[str] = "extraction_failed"
# A reader raised, so no gate can be evaluated: hard stop, never a silent zero.
STOP_GUARD_FAILED: Final[str] = "guard_failed"
# The child exited non-zero unprompted, e.g. the harness stopping on a failed session.
STOP_CHILD_FAILED: Final[str] = "child_failed"
# Another arm already holds the benchmark run lock, so nothing may spend here.
STOP_RUN_LOCK_BUSY: Final[str] = "run_lock_busy"

#: Session-scoped advisory lock making one benchmark run exclusive; the cap is whole-database.
BENCHMARK_RUN_LOCK_KEY: Final[int] = 0x646576325F72756E  # ASCII "dev2_run", sign bit clear.
RUN_LOCK_SQL: Final[str] = "SELECT pg_try_advisory_lock($1)"
RUN_LOCK_HELD_SQL: Final[str] = (
    "SELECT count(*) FROM pg_locks "
    "WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND granted"
)
# Hard gate: an arm with at least one failed session and >= 1% failures stops.
FAILED_RATE_GATE: Final[float] = 0.01

SessionExtractor = Callable[[Mapping[str, Any], str], tuple[Sequence[str], Sequence[str]]]


class ProcessHandle(Protocol):
    """The two methods ``run_arm`` needs from an injected child process."""

    def poll(self) -> int | None: ...

    def wait(self) -> int: ...


ROUTING_EVENT_MARKER: Final[str] = "routing_event "


def _routing_records(log_lines: Iterable[str]) -> list[Mapping[str, Any]]:
    """Routing records in captured log lines.

    The routing logger's ``SafeHandler`` writes a bare JSON object per line
    (``orchestrator/safe_logging.py:61-78``); a plain formatter would instead render
    ``routing_event {json}``. Both are read. Only events the routing log declares
    count, so ``legacy_log`` lines and junk are ignored.
    """
    records: list[Mapping[str, Any]] = []
    for line in log_lines:
        if not isinstance(line, str):
            continue
        index = line.find(ROUTING_EVENT_MARKER)
        text = line[index + len(ROUTING_EVENT_MARKER) :] if index >= 0 else line
        try:
            payload = json.loads(text)
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("event") in EVENT_FIELDS:
            records.append(payload)
    return records


def _attempt_outcome_records(log_lines: Iterable[str]) -> list[Mapping[str, Any]]:
    return [
        record for record in _routing_records(log_lines) if record.get("event") == "attempt_outcome"
    ]


def count_rate_limits(log_lines: Iterable[str]) -> dict[str, int]:
    """Count ``attempt_outcome`` routing events by ``failure_category``.

    Only ``routing_event`` JSON objects carrying ``"event": "attempt_outcome"``
    are read; every other line (junk, partial writes, other events) is ignored.
    The result is a plain ``{category: count}`` mapping and carries no message
    text.
    """
    counts: Counter[str] = Counter()
    for record in _attempt_outcome_records(log_lines):
        category = record.get("failure_category")
        if isinstance(category, str) and category:
            counts[category] += 1
    return dict(sorted(counts.items()))


def log_lines_seen_routing_events(lines: Iterable[str]) -> int:
    """How many routing records the captured log actually carried.

    A zero ``rate_limited`` count is only evidence of no 429s when this is
    non-zero: with no routing events the log source was empty or misconfigured,
    so the count is unknown rather than zero.
    """
    return len(_routing_records(lines))


def _ingest_results(checkpoint: Mapping[str, Any]) -> Mapping[str, Any]:
    """``phases.ingest.results``; a missing or foreign shape must not read as zero work."""
    results = checkpoint["phases"]["ingest"]["results"]
    if not isinstance(results, Mapping):
        raise ValueError("checkpoint ingest results must be a mapping")
    return results


def _pending_sessions(checkpoint: Mapping[str, Any]) -> Mapping[str, Any]:
    """``phases.ingest.pending_sessions``; absent means nothing is in flight."""
    ingest = checkpoint["phases"]["ingest"]
    pending = ingest.get("pending_sessions", {})
    if not isinstance(pending, Mapping):
        raise ValueError("checkpoint pending sessions must be a mapping")
    return pending


def _pending_failures(pending: Mapping[str, Any], question_id: str) -> list[str]:
    """Session IDs of a question's pending entries that recorded a ``failure``.

    ``fact_harness.py:1701-1755`` writes ``pending[question_id] = {corpus_key,
    started_at, state_fingerprint}`` before a session is ingested and adds a
    ``failure`` mapping (``at``, ``session_id``, ``status``, ``error``,
    ``chunks_failed``, ``chunk_errors``) only when the session did not complete.
    The harness then raises, so a failed session never reaches ``results``. An
    entry without ``failure`` is still in flight: neither failed nor complete.
    """
    entry = pending.get(question_id)
    if not isinstance(entry, Mapping):
        return []
    failure = entry.get("failure")
    if not isinstance(failure, Mapping):
        return []
    session_id = failure.get("session_id")
    if isinstance(session_id, str) and session_id:
        return [session_id]
    corpus_key = entry.get("corpus_key")
    return [corpus_key] if isinstance(corpus_key, str) and corpus_key else []


def default_session_extractor(
    checkpoint: Mapping[str, Any], question_id: str
) -> tuple[Sequence[str], Sequence[str]]:
    """Sessions recorded for one question in a harness checkpoint.

    ``phases.ingest.results`` maps ``ingest_result_key`` (``question:corpus_key``,
    ``fact_harness.py:736``) to a result whose ``status`` is ``complete`` on success
    (``fact_harness.py:1631``). An unaccepted failure never reaches ``results``: it
    stays in ``pending`` with a ``failure`` and the harness raises
    (``fact_harness.py:1736-1755``), so pending failures count as failed sessions
    too. An accepted failure (``--accept-failed-session``, owner ruling #691) is a
    saved result with ``status`` ``extraction_failed`` and ``accepted_failure`` set,
    so it counts as exactly one failed session under the 1% gate. Keys are unique,
    so a duplicate can't appear here: the resume check must also compare database
    conversation counts.
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
    for session in _pending_failures(_pending_sessions(checkpoint), question_id):
        sessions.append(session)
        failed.append(session)
    return sessions, failed


def checkpoint_invariants(
    checkpoint: Mapping[str, Any], *, extract: SessionExtractor | None = None
) -> dict[str, Any]:
    """Invariants for every question present in ``checkpoint``.

    A question whose only session is a pending failure has no ``results`` key, so
    the pending question IDs are part of the set as well.
    """
    question_ids = sorted(
        {str(key).split(":", 1)[0] for key in _ingest_results(checkpoint)}
        | {str(key) for key in _pending_sessions(checkpoint)}
    )
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
    (``extraction_failed``). Those hard-stop gates are evaluated first, so they
    win a poll on which a planned interrupt also became due. If
    ``interrupt_after_completed`` is set and no gate fired, one SIGINT is sent
    once that many questions completed and the run is marked
    ``interrupted_for_resume`` with no stop reason. A second SIGINT is sent at
    most once, only after ``grace_seconds`` with the child still alive, and is
    recorded. Once the child has exited the gates are evaluated again on the
    final readings, so spend that crosses during shutdown, or during the drain
    after a planned resume, is a stop rather than a clean resume; an existing
    stop reason is never overwritten. If any reader raises, the run is a hard
    stop: one interrupt is
    sent, ``stop_reason`` is ``guard_failed``, the child is still drained to exit
    on the same grace path, and the unread final numbers are reported as ``None``
    instead of a false zero. The result carries numbers and reason codes only,
    including ``routing_events_seen`` so a zero 429 count can be told apart from
    a log source that carried no routing events at all.
    """
    if poll_seconds <= 0 or not math.isfinite(poll_seconds):
        raise ValueError("poll_seconds must be a positive finite number")
    if grace_seconds < 0 or not math.isfinite(grace_seconds):
        raise ValueError("grace_seconds must be a nonnegative finite number")
    if interrupt_after_completed is not None and interrupt_after_completed < 1:
        raise ValueError("interrupt_after_completed must be at least 1")

    started = clock()
    spend_before = float(read_spend_usd())
    # Already over: start nothing, rather than spawn and stop on the first poll.
    if spend_before >= stop_rule.spend_threshold_usd:
        return {
            **_not_started_result(STOP_SPEND),
            "spend_before_usd": spend_before,
            "spend_after_usd": spend_before,
            "spend_usd": 0.0,
        }
    samples: list[Mapping[str, float]] = []
    interrupts: list[dict[str, Any]] = []
    stop_reason: str | None = None
    interrupted = False
    guard_error: str | None = None
    first_interrupt_at: float | None = None
    second_sent = False
    process = spawn(list(command))

    completed_at_start = 0
    try:
        # Sessions already complete when this invocation starts belong to earlier
        # ones: throughput must divide this invocation's work by its own wall time.
        completed_at_start = int(
            checkpoint_invariants(read_checkpoint()).get("completed_sessions", 0)
        )
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
                # Hard-stop gates are checked before the planned interrupt: a poll
                # that satisfies both must record the gate, not a clean resume.
                reason: str | None = _gate_reason(
                    float(read_spend_usd()), invariants, rate_limits, stop_rule
                )
                resumed = (
                    reason is None
                    and interrupt_after_completed is not None
                    and _completed_questions(invariants, expected_sessions)
                    >= interrupt_after_completed
                )
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
    except Exception as exc:
        # A reader failed, so every gate is unknown: that is a hard stop, not a
        # zero. Interrupt the child and drain it below; it is never left running.
        guard_error = type(exc).__name__
        stop_reason = STOP_GUARD_FAILED
        now = clock()
        send_interrupt(process)
        first_interrupt_at = now
        interrupts.append({"at_seconds": round(now - started, 3), "kind": "guard"})
    finally:
        # Never leave the child running: keep polling to exit, escalating once
        # after the grace period exactly like the normal interrupt path.
        while process.poll() is None:
            now = clock()
            if (
                first_interrupt_at is not None
                and not second_sent
                and now - first_interrupt_at >= grace_seconds
            ):
                send_interrupt(process)
                second_sent = True
                interrupts.append({"at_seconds": round(now - started, 3), "kind": "grace"})
            sleep(poll_seconds)
        exit_code = process.wait()
        if exit_code != 0 and stop_reason is None and not interrupted:
            stop_reason = STOP_CHILD_FAILED

    elapsed = max(clock() - started, 0.0)
    # On a broken reader the final numbers are unknown; report them as absent rather
    # than as a false zero. The child has already drained, so a reader failing here
    # must still produce a record: it stops the arm instead of raising.
    spend_after: float | None = None
    final_invariants: Mapping[str, Any] = {}
    final_rates: Mapping[str, int] = {}
    routing_events_seen = 0
    if guard_error is None:
        try:
            samples.append(sample_resources())
            spend_after = float(read_spend_usd())
            final_invariants = checkpoint_invariants(read_checkpoint())
            lines = list(read_log_lines())
            final_rates = count_rate_limits(lines)
            routing_events_seen = log_lines_seen_routing_events(lines)
        except Exception as exc:
            guard_error = type(exc).__name__
            stop_reason = stop_reason or STOP_GUARD_FAILED
            interrupted = False
            spend_after = None
            final_invariants = {}
            final_rates = {}
            routing_events_seen = 0
    if guard_error is None and stop_reason is None and spend_after is not None:
        # The child can cross a gate between the last poll and exit, and a planned
        # resume drains it without another poll: judge the final readings too. A
        # resume whose drain crossed the cap is a stop, never a clean resume.
        final_reason = _gate_reason(spend_after, final_invariants, final_rates, stop_rule)
        if final_reason is not None:
            stop_reason = final_reason
            interrupted = False
    completed = int(final_invariants.get("completed_sessions", 0))
    # On a guard failure the final figures are unknown (reported as zero above), so
    # this invocation's delta is not derivable and is reported as zero too.
    completed_this_invocation = (
        0 if guard_error is not None else max(completed - completed_at_start, 0)
    )
    hours = elapsed / 3600.0
    return {
        "wall_seconds": elapsed,
        "exit_code": exit_code,
        "stop_reason": stop_reason,
        "interrupted_for_resume": interrupted,
        "interrupts": interrupts,
        "guard_error": guard_error,
        "spend_before_usd": spend_before,
        "spend_after_usd": spend_after,
        "spend_usd": None if spend_after is None else spend_after - spend_before,
        "rate_limits": final_rates,
        "rate_limited_events": int(final_rates.get("rate_limited", 0)),
        "routing_events_seen": routing_events_seen,
        "resource_samples": _resource_bounds(samples),
        "completed_sessions": completed,
        "completed_sessions_this_invocation": completed_this_invocation,
        "failed_sessions": int(final_invariants.get("failed_sessions", 0)),
        "sessions_per_hour": completed_this_invocation / hours if hours > 0 else 0.0,
    }


# --- Live wiring --------------------------------------------------------------
#
# Each reader below is still injected into ``run_arm``; nothing here opens a
# connection, calls a provider or reads the dataset. The driver supplies the
# database callable, the checkpoint path and the captured child log path.

MICROUSD_PER_USD: Final[float] = 1_000_000.0

#: Cumulative spend for the whole benchmark database, ids included.
#:
#: Settled rows contribute ``actual_microusd``; ``open`` rows contribute the
#: amount they still hold (``reserved_microusd``) so in-flight calls are counted
#: before they settle. No ``since`` and no user filter: the cap is a whole-run
#: cap, cumulative across arms.
SPEND_SQL: Final[str] = (
    "SELECT "
    "coalesce(sum(actual_microusd) FILTER (WHERE status = 'settled'), 0) AS settled_microusd, "
    "coalesce(sum(reserved_microusd) FILTER (WHERE status = 'open'), 0) AS held_microusd "
    "FROM entitlement_reservations"
)

#: Conversations per benchmark user, for the duplicate check.
CONVERSATION_COUNTS_SQL: Final[str] = (
    "SELECT user_id::text AS user_id, count(*) AS conversations FROM conversations GROUP BY user_id"
)


@dataclass(frozen=True)
class SpendBreakdown:
    """Cumulative spend split into settled actuals and still-open holds."""

    settled_usd: float
    held_usd: float

    @property
    def total_usd(self) -> float:
        return self.settled_usd + self.held_usd


def spend_from_row(row: Mapping[str, Any] | None) -> SpendBreakdown:
    """USD from one :data:`SPEND_SQL` row; a missing row reads as zero."""
    if row is None:
        return SpendBreakdown(0.0, 0.0)
    settled = int(row["settled_microusd"] or 0)
    held = int(row["held_microusd"] or 0)
    return SpendBreakdown(
        settled_usd=settled / MICROUSD_PER_USD,
        held_usd=held / MICROUSD_PER_USD,
    )


def make_spend_reader(
    fetchrow: Callable[[str], Mapping[str, Any] | None],
) -> Callable[[], float]:
    """A ``run_arm`` spend reader from a synchronous ``fetchrow`` callable.

    The driver owns the connection and event loop; this module never awaits.
    Use :func:`spend_breakdown_reader` when the settled/held split is reported.
    """

    def read() -> float:
        return spend_from_row(fetchrow(SPEND_SQL)).total_usd

    return read


def spend_breakdown_reader(
    fetchrow: Callable[[str], Mapping[str, Any] | None],
) -> Callable[[], SpendBreakdown]:
    """A reader that returns the split, for the per-arm report."""

    def read() -> SpendBreakdown:
        return spend_from_row(fetchrow(SPEND_SQL))

    return read


def empty_checkpoint() -> dict[str, Any]:
    """The shell the harness leaves before its first save."""
    return {"phases": {"ingest": {"results": {}, "pending_sessions": {}}}}


def read_checkpoint_file(path: Path | str) -> dict[str, Any]:
    """Read the harness checkpoint; before the first save it is an empty shell.

    A missing file is normal, not an error: the harness creates the checkpoint on
    its first save, so a poll before that must read as no sessions rather than
    trip the guard-failure path.
    """
    target = Path(path)
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        return empty_checkpoint()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("checkpoint must be a JSON object")
    return payload


def make_log_reader(path: Path | str) -> Callable[[], list[str]]:
    """A ``run_arm`` log reader over a captured child log file.

    A missing file reads as no lines: ``routing_events_seen`` then reports that
    the log source carried nothing, which the report must treat as unknown.
    """
    target = Path(path)

    def read() -> list[str]:
        try:
            return target.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            return []

    return read


def _proc_value(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict").strip()


def sample_resources_psutil_free(
    proc_root: Path | str = "/proc", *, pid: int | None = None
) -> dict[str, float]:
    """CPU load, memory, RSS and free disk, using only stdlib and ``/proc``.

    Missing or unreadable sources are omitted rather than raised: this runs
    inside the poll loop, where a raise is a guard failure.
    """
    root = Path(proc_root)
    sample: dict[str, float] = {}
    try:
        sample["load1"] = float(_proc_value(root / "loadavg").split()[0])
    except (OSError, ValueError, IndexError):
        pass
    try:
        for line in (root / "meminfo").read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0] in {"MemTotal:", "MemAvailable:"}:
                name = "mem_total_bytes" if fields[0] == "MemTotal:" else "mem_available_bytes"
                sample[name] = float(int(fields[1]) * 1024)
    except (OSError, ValueError):
        pass
    try:
        resident_pages = int(_proc_value(root / (str(pid) if pid else "self") / "statm").split()[1])
        sample["rss_bytes"] = float(resident_pages * os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, IndexError):
        pass
    try:
        usage = shutil.disk_usage("/")
        sample["disk_free_bytes"] = float(usage.free)
        sample["disk_total_bytes"] = float(usage.total)
    except OSError:
        pass
    return sample


def conversation_counts_sql() -> str:
    """The whole-database conversation count per benchmark user."""
    return CONVERSATION_COUNTS_SQL


def verify_db_counts(rows: Iterable[Mapping[str, Any]], expected: Mapping[str, int]) -> list[str]:
    """Compare conversations per benchmark user with the expected session count.

    ``expected`` is keyed by benchmark ``user_id`` (``question_user_id``). Each
    question has its own user, so an arm's total is one row per question; a row
    above ``expected`` means a duplicated conversation survived a resume. Users
    outside ``expected`` are not reported: another arm's users legitimately exist
    in the same database.
    """
    observed = {str(row["user_id"]): int(row["conversations"]) for row in rows}
    violations: list[str] = []
    for user_id, count in sorted(expected.items()):
        actual = observed.get(str(user_id), 0)
        if actual != int(count):
            violations.append(f"user {user_id} has {actual} conversations, expected {int(count)}")
    return violations


def expected_sessions_from_dataset(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    """Expected session count per question, de-duplicated exactly as the harness.

    ``fact_harness.py:692-733`` builds one ``corpus_key`` per session with
    ``build_corpus_key`` and keeps the first occurrence of each key, so the count
    is the number of distinct corpus keys among the list-valued sessions. The
    derivation is imported, never re-implemented; the import is local because
    those modules pull the database stack and this module stays import-light.
    """
    from orchestrator.eval.chunk_harness import normalize_question_id
    from tests.longmemeval.ingest import build_corpus_key

    counts: dict[str, int] = {}
    for idx, entry in enumerate(rows):
        question_id = normalize_question_id(dict(entry), idx)
        if question_id in counts:
            raise ValueError("Duplicate question IDs in the dataset")
        keys = {
            build_corpus_key(messages)
            for messages in entry.get("haystack_sessions") or []
            if isinstance(messages, list)
        }
        counts[question_id] = len(keys)
    return counts


def answer_conversation_ids(
    checkpoint: Mapping[str, Any],
    question_id: str,
    answer_session_ids: Sequence[str],
) -> dict[str, str]:
    """Map a question's answer sessions to their ingested ``conversation_id``s.

    Sessions are matched on each result's ``session_id`` field, not on the result
    key (that key is the ``corpus_key``). An answer session with no completed
    result raises: labelling must never silently drop an answer source.
    """
    results = _ingest_results(checkpoint)
    prefix = f"{question_id}:"
    by_session: dict[str, str] = {}
    for key, result in results.items():
        if not isinstance(key, str) or not key.startswith(prefix):
            continue
        if not isinstance(result, Mapping) or result.get("status") != "complete":
            continue
        session_id = result.get("session_id")
        conversation_id = result.get("conversation_id")
        if isinstance(session_id, str) and isinstance(conversation_id, str):
            by_session[session_id] = conversation_id
    mapping: dict[str, str] = {}
    missing: list[str] = []
    for session_id in answer_session_ids:
        conversation_id = by_session.get(str(session_id))
        if conversation_id is None:
            missing.append(str(session_id))
        else:
            mapping[str(session_id)] = conversation_id
    if missing:
        raise ValueError(
            f"{len(missing)} answer session(s) of {question_id} have no completed ingest result"
        )
    return mapping


# --- ``run-arm`` command ------------------------------------------------------
#
# This is the only place a process is spawned, a connection is opened and a
# signal is sent. ``run_arm_command`` takes every one of those as a factory so
# the tests drive it with fakes: the test suite starts no process, opens no
# connection, reads no dataset and calls no provider.

#: The final-pin dev2 database (owner ruling). The pilot's ``daemon_lme_bench_dev2``
#: stays untouched as evidence, so this driver refuses it.
RUN_ARM_DB_NAME: Final[str] = "daemon_lme_bench_dev2_final"
SHARED_DB_NAME: Final[str] = "daemon_lme_bench"
CHECKPOINT_NAME: Final[str] = "longmemeval_checkpoint.json"
CHILD_LOG_NAME: Final[str] = "child.log"
#: Written once at the start of every invocation. A resume appends, so earlier
#: invocations' routing events (and the rate-limit guard's count) survive; the
#: line is not a ``routing_event`` record and is ignored by every log reader.
CHILD_LOG_SEPARATOR: Final[str] = "--- dev2 ingest invocation"
POLL_SECONDS: Final[float] = 15.0
GRACE_SECONDS: Final[float] = 60.0
#: Never passed to the child: each one would weaken the arm's evidence.
#: ``--allow-code-change`` is not here: this run resumes a checkpoint pinned to an
#: earlier SHA, so ``run-arm --allow-code-change`` passes it through deliberately.
FORBIDDEN_CHILD_FLAGS: Final[tuple[str, ...]] = (
    "--retry-failed-sessions",
    "--allow-state-mismatch",
)


def db_name_from_dsn(dsn: str) -> str | None:
    """Database name of a postgres URL, ignoring credentials and query string."""
    parts = urlsplit(dsn)
    name = parts.path.lstrip("/")
    # libpq honours a dbname query parameter, and the harness decodes percent escapes,
    # so either could address a database other than the one compared here.
    if parts.query or "%" in name:
        return None
    return name or None


def check_run_arm_database(dsn: str | None) -> str:
    """Refuse unless ``DATABASE_URL`` names exactly the final-pin dev2 database."""
    name = db_name_from_dsn(dsn or "")
    if name == SHARED_DB_NAME:
        raise ValueError(f"run-arm refuses the shared {SHARED_DB_NAME} database")
    if name != RUN_ARM_DB_NAME:
        raise ValueError(f"run-arm requires DATABASE_URL to name exactly {RUN_ARM_DB_NAME}")
    return name


def check_output_outside_repo(output_dir: Path, repo_root: Path) -> None:
    """Run output inside the checkout would dirty the tree and refuse its own resume."""
    if output_dir.resolve().is_relative_to(repo_root.resolve()):
        raise ValueError("run-arm requires --output-dir outside the repository")


def check_clean_tree(porcelain: str) -> None:
    """Refuse to measure an arm built from uncommitted state."""
    if porcelain.strip():
        raise ValueError("run-arm requires a clean git tree")


def checkpoint_path(output_dir: Path | str) -> Path:
    return Path(output_dir) / CHECKPOINT_NAME


def check_interrupt_target(
    output_dir: Path | str, *, interrupt_after_completed: int | None, resume: bool
) -> None:
    """An interrupt run starts in a fresh directory unless it is a resume."""
    if interrupt_after_completed is None or resume:
        return
    if checkpoint_path(output_dir).exists():
        raise ValueError("output dir already holds a checkpoint; pass --resume to continue")


def file_sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def child_command(
    *,
    arm_dataset: Path | str,
    output_dir: Path | str,
    concurrency: int,
    python: str,
    accept_failed_sessions: Sequence[str] = (),
    allow_code_change: bool = False,
) -> list[str]:
    """The exact ingest command, with none of the evidence-weakening flags.

    ``--accept-failed-session`` names one session the owner ruled may fail
    (issue #691) and ``--allow-code-change`` resumes the checkpoint pinned to an
    earlier SHA; the two prohibited flags are never added.
    """
    if isinstance(concurrency, bool) or concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    command = [
        str(python),
        "-m",
        "orchestrator.eval.longmemeval",
        "ingest",
        "--dataset",
        str(arm_dataset),
        "--output-dir",
        str(output_dir),
        "--concurrency",
        str(concurrency),
    ]
    for corpus_key in accept_failed_sessions:
        command.extend(["--accept-failed-session", str(corpus_key)])
    if allow_code_change:
        command.append("--allow-code-change")
    if any(flag in command for flag in FORBIDDEN_CHILD_FLAGS):
        raise ValueError("child command must not weaken the arm")
    return command


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_record(
    *,
    result: Mapping[str, Any],
    code_sha: str,
    dataset_sha256: str,
    started_utc: str,
    ended_utc: str,
) -> dict[str, Any]:
    """Numbers, hashes and reason codes only: no question or session text."""
    return {
        "schema": "dev2-pilot-run-arm/1",
        "code_sha": code_sha,
        "arm_dataset_sha256": dataset_sha256,
        "started_utc": started_utc,
        "ended_utc": ended_utc,
        "run_arm": dict(result),
    }


def write_record(path: Path | str, payload: Mapping[str, Any]) -> None:
    """``open(..., "x")``: a record is never overwritten, resume included."""
    with open(path, "x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def claim_record(path: Path | str) -> Path:
    """Refuse a taken record path before any child starts, so no paid run loses its record.

    Returns the claim file, removed once the record is written. A leftover claim
    means a run died without a record; use a new record path, never reuse one.
    """
    claim = Path(f"{path}.claim")
    if Path(path).exists():
        raise FileExistsError(f"Record already exists: {path}")
    with open(claim, "x", encoding="utf-8"):
        pass
    return claim


def child_resource_sampling(
    spawn: Callable[[Sequence[str]], ProcessHandle],
) -> tuple[Callable[[Sequence[str]], ProcessHandle], Callable[[], Mapping[str, float]]]:
    """A spawn and a sampler whose ``rss_bytes`` is the ingestion child's, not the driver's."""
    child: dict[str, int] = {}

    def spawn_tracking(command: Sequence[str]) -> ProcessHandle:
        process = spawn(command)
        pid = getattr(process, "pid", None)
        if isinstance(pid, int):
            child["pid"] = pid
        return process

    return spawn_tracking, lambda: sample_resources_psutil_free(pid=child.get("pid"))


def make_spawn(log_path: Path | str) -> Callable[[Sequence[str]], ProcessHandle]:
    """A spawn that appends the child's stdout and stderr to one log file.

    Append, not truncate: a resumed invocation must keep the routing events of
    the invocations before it, and the rate-limit guard counts over the whole
    arm rather than restarting from zero. One separator line marks where this
    invocation began; no reader parses it as a routing event.
    """
    target = Path(log_path)

    def spawn(command: Sequence[str]) -> ProcessHandle:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = open(target, "ab")  # kept open until the child exits
        try:
            handle.write(f"{CHILD_LOG_SEPARATOR} {_utc_now()}\n".encode())
            handle.flush()
            return subprocess.Popen(list(command), stdout=handle, stderr=subprocess.STDOUT)
        except BaseException:
            handle.close()
            raise

    return spawn


def send_sigint(process: Any) -> None:
    """SIGINT is the harness's documented graceful stop; the child exposes ``pid``."""
    os.kill(int(process.pid), signal.SIGINT)


class RunLock(Protocol):
    """An exclusive benchmark run lock held for the whole child lifetime."""

    def acquire(self) -> bool: ...

    def assert_held(self) -> None:
        """Raise unless this run still holds the lock."""
        ...

    def release(self) -> None: ...


def spend_while_holding(lock: RunLock, read_spend: Callable[[], float]) -> Callable[[], float]:
    """Check the lock on every spend poll; a lost lock takes the guard-failed stop."""

    def read() -> float:
        lock.assert_held()
        return read_spend()

    return read


async def acquire_benchmark_run_lock(fetchval: Callable[..., Any]) -> bool:
    """``True`` only when this session was granted the benchmark run lock.

    ``fetchval`` is the injected ``pg_try_advisory_lock`` callable. Any error
    propagates to the caller, which must treat it as not acquired.
    """
    return (await fetchval(RUN_LOCK_SQL, BENCHMARK_RUN_LOCK_KEY)) is True


class AsyncpgRunLock:
    """A dedicated asyncpg connection holding :func:`acquire_benchmark_run_lock`.

    The lock is session-scoped, so closing the connection is the release path;
    the connection is kept open for the whole child lifetime and never shared
    with the short-lived per-poll readers.
    """

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._loop = asyncio.new_event_loop()
        self._connection: Any = None

    def acquire(self) -> bool:
        async def open_and_lock() -> bool:
            import asyncpg

            self._connection = await asyncpg.connect(self._url)
            return await acquire_benchmark_run_lock(self._connection.fetchval)

        return self._loop.run_until_complete(open_and_lock())

    def assert_held(self) -> None:
        """The session lock ends with its connection, so a lost one must surface."""

        async def held() -> bool:
            count = await self._connection.fetchval(RUN_LOCK_HELD_SQL)
            return int(count or 0) >= 1

        if self._connection is None or not self._loop.run_until_complete(held()):
            raise RuntimeError("Benchmark run lock is no longer held")

    def release(self) -> None:
        async def close() -> None:
            connection, self._connection = self._connection, None
            if connection is not None:
                await connection.close()

        if self._loop.is_closed():
            return
        try:
            self._loop.run_until_complete(close())
        finally:
            self._loop.close()


def make_sync_fetchrow(database_url: str) -> Callable[[str], Mapping[str, Any] | None]:
    """One short-lived asyncpg connection per poll; none is held between polls."""

    def fetchrow(sql: str) -> Mapping[str, Any] | None:
        async def read() -> Mapping[str, Any] | None:
            import asyncpg

            connection = await asyncpg.connect(database_url)
            try:
                return await connection.fetchrow(sql)
            finally:
                await connection.close()

        return asyncio.run(read())

    return fetchrow


def _not_started_result(stop_reason: str) -> dict[str, Any]:
    """The record body when a run was refused before any child started."""
    return {
        "wall_seconds": 0.0,
        "exit_code": None,
        "stop_reason": stop_reason,
        "interrupted_for_resume": False,
        "interrupts": [],
        "guard_error": None,
        "spend_before_usd": None,
        "spend_after_usd": None,
        "spend_usd": None,
        "rate_limits": {},
        "rate_limited_events": 0,
        "routing_events_seen": 0,
        "resource_samples": {},
        "completed_sessions": 0,
        "completed_sessions_this_invocation": 0,
        "failed_sessions": 0,
        "sessions_per_hour": 0.0,
    }


def run_arm_command(
    *,
    arm_dataset: Path | str,
    output_dir: Path | str,
    concurrency: int,
    cap_usd: float,
    margin_usd: float,
    max_rate_limited: int,
    record_path: Path | str,
    database_url: str | None,
    status_porcelain: str,
    code_sha: str,
    dataset_rows: Sequence[Mapping[str, Any]],
    spawn: Callable[[Sequence[str]], ProcessHandle],
    fetchrow_factory: Callable[[], Callable[[str], Mapping[str, Any] | None]],
    sample_resources: Callable[[], Mapping[str, float]],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    send_interrupt: Callable[[ProcessHandle], None],
    interrupt_after_completed: int | None = None,
    resume: bool = False,
    accept_failed_sessions: Sequence[str] = (),
    allow_code_change: bool = False,
    now: Callable[[], str] = _utc_now,
    poll_seconds: float = POLL_SECONDS,
    run_lock: RunLock | None = None,
) -> tuple[dict[str, Any], int]:
    """Gate, then wire ``run_arm`` with real readers and write the record.

    The three refusals happen before ``spawn`` is called at all: the wrong
    database, a dirty tree, and an interrupt run pointed at an existing
    checkpoint without ``--resume``. The exclusive benchmark run lock is
    acquired on its own dedicated connection before the child is spawned and
    held until it exits. A busy or unreadable lock spawns nothing and records
    ``stop_reason`` ``run_lock_busy`` with exit code 1.
    """
    check_run_arm_database(database_url)
    check_clean_tree(status_porcelain)
    check_interrupt_target(
        output_dir, interrupt_after_completed=interrupt_after_completed, resume=resume
    )

    claim = claim_record(record_path)
    expected_sessions = expected_sessions_from_dataset(dataset_rows)
    started_utc = now()
    lock = run_lock if run_lock is not None else AsyncpgRunLock(database_url or "")
    try:
        try:
            acquired = lock.acquire()
        except Exception:
            # An unreadable lock state is not acquisition: refuse to spend.
            acquired = False
        if acquired:
            result = run_arm(
                command=child_command(
                    arm_dataset=arm_dataset,
                    output_dir=output_dir,
                    concurrency=concurrency,
                    python=sys.executable,
                    accept_failed_sessions=accept_failed_sessions,
                    allow_code_change=allow_code_change,
                ),
                spawn=spawn,
                read_spend_usd=spend_while_holding(lock, make_spend_reader(fetchrow_factory())),
                read_log_lines=make_log_reader(Path(output_dir) / CHILD_LOG_NAME),
                read_checkpoint=lambda: read_checkpoint_file(checkpoint_path(output_dir)),
                sample_resources=sample_resources,
                clock=clock,
                sleep=sleep,
                poll_seconds=poll_seconds,
                stop_rule=StopRule(
                    cap_usd=cap_usd, margin_usd=margin_usd, max_rate_limited=max_rate_limited
                ),
                # A resume finishes the arm: the planned interrupt already happened, and
                # the checkpoint's completed questions would fire it again at once.
                interrupt_after_completed=None if resume else interrupt_after_completed,
                expected_sessions=expected_sessions,
                send_interrupt=send_interrupt,
                grace_seconds=GRACE_SECONDS,
            )
        else:
            result = _not_started_result(STOP_RUN_LOCK_BUSY)
    finally:
        lock.release()
    record = build_record(
        result=result,
        code_sha=code_sha,
        dataset_sha256=file_sha256(arm_dataset),
        started_utc=started_utc,
        ended_utc=now(),
    )
    write_record(record_path, record)
    claim.unlink()
    return record, (1 if result["stop_reason"] else 0)


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def run_arm_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dev2_pilot.py run-arm",
        description="Run one ingest arm with the pilot guards wired to real readers.",
    )
    parser.add_argument("--arm-dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=1)
    # Required: each block sets its own cap, so no run can fall back on an old figure.
    parser.add_argument("--cap-usd", type=float, required=True)
    parser.add_argument("--margin-usd", type=float, default=1.00)
    parser.add_argument("--max-rate-limited", type=int, default=DEFAULT_MAX_RATE_LIMITED)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--interrupt-after-completed", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--accept-failed-session",
        action="append",
        dest="accept_failed_sessions",
        metavar="CORPUS_KEY",
        default=None,
        help="Pass --accept-failed-session CORPUS_KEY to the child (repeatable); the named "
        "session may end in extraction_failed and the run continues, counted as failed.",
    )
    parser.add_argument(
        "--allow-code-change",
        action="store_true",
        help="Pass --allow-code-change to the child so a checkpoint pinned to an earlier SHA "
        "can resume; the manifest records the pin change and disqualifies the baseline.",
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    rows = json.loads(args.arm_dataset.read_text(encoding="utf-8"))
    check_output_outside_repo(args.output_dir, Path(_git("rev-parse", "--show-toplevel")))
    spawn, sample_child_resources = child_resource_sampling(
        make_spawn(args.output_dir / CHILD_LOG_NAME)
    )
    record, exit_code = run_arm_command(
        arm_dataset=args.arm_dataset,
        output_dir=args.output_dir,
        concurrency=args.concurrency,
        cap_usd=args.cap_usd,
        margin_usd=args.margin_usd,
        max_rate_limited=args.max_rate_limited,
        record_path=args.record,
        database_url=database_url,
        status_porcelain=_git("status", "--porcelain"),
        code_sha=_git("rev-parse", "HEAD"),
        dataset_rows=rows,
        spawn=spawn,
        fetchrow_factory=lambda: make_sync_fetchrow(database_url or ""),
        sample_resources=sample_child_resources,
        sleep=time.sleep,
        clock=time.monotonic,
        send_interrupt=send_sigint,
        interrupt_after_completed=args.interrupt_after_completed,
        resume=args.resume,
        accept_failed_sessions=tuple(args.accept_failed_sessions or ()),
        allow_code_change=args.allow_code_change,
    )
    print(f"run-arm stop_reason={record['run_arm']['stop_reason']} record={args.record}")
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    main()
