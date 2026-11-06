"""Offline tests for the M2 exposure gate driver. Fakes only: no database, no network."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from orchestrator.memory.embedding import EmbeddingVectorResult
from tests.benchmark_longmemeval import m2_exposure
from tests.benchmark_longmemeval.dev2_labelling import _NullScope
from tests.benchmark_longmemeval.m2_exposure import (
    FrozenIds,
    LabelEntry,
    LabelSet,
    Question,
    gate1_threshold,
    load_labels,
    load_pinned_subqueries,
    load_questions,
    run_gates,
    run_generation,
    run_measure,
    run_report,
    run_retention,
    run_threshold,
)

NOW = datetime(2023, 5, 30, 23, 18, tzinfo=UTC)


def _ids(count: int = 100) -> tuple[str, ...]:
    if count <= 6:
        return tuple(f"q{index:04d}" for index in range(count))
    return tuple(
        [f"q{index:04d}" for index in range(count - 6)] + [f"a{index}_abs" for index in range(6)]
    )


def _frozen(count: int = 100) -> FrozenIds:
    return FrozenIds(_ids(count), "f" * 64)


def _label_entry(qid: str, answerable: bool, targets: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "question_id": qid,
        "schema_valid": True,
        "answerable_from_store": answerable,
        "memory_ids": list(targets),
        "note": "",
    }


def _write_labels(
    path: Path,
    frozen: FrozenIds,
    *,
    answerable: Sequence[str],
    targets: Mapping[str, Sequence[str]] | None = None,
    schema: str = m2_exposure.LABEL_SCHEMA,
    stop_reason: str | None = None,
    total_cost_complete: bool = True,
) -> Path:
    answerable_set = set(answerable)
    entries = []
    for qid in frozen.ids:
        if qid in answerable_set:
            entries.append(_label_entry(qid, True, (targets or {}).get(qid, ("m-1",))))
        else:
            entries.append(_label_entry(qid, False))
    document = {
        "schema": schema,
        "route_id": "luna-azure-eu",
        "stop_reason": stop_reason,
        "total_cost_complete": total_cost_complete,
        "questions": entries,
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _labels(frozen: FrozenIds, answerable: Sequence[str], path: Path) -> LabelSet:
    _write_labels(path, frozen, answerable=answerable)
    return load_labels([path], frozen)


def _questions(frozen: FrozenIds, *, sessions: bool = True) -> list[Question]:
    return [
        Question(
            question_id=qid,
            message=f"planted question text for {qid}",
            reference_time=NOW,
            abstention=qid.endswith("_abs"),
            answer_session_ids=("s1", "s2") if sessions else (),
        )
        for qid in frozen.ids
    ]


# --- threshold ----------------------------------------------------------------


def test_gate1_threshold_is_exact_at_boundaries() -> None:
    assert gate1_threshold(49)["exact"] == "5"
    assert gate1_threshold(50)["exact"] == "5"
    assert gate1_threshold(51)["exact"] == "5.1"
    assert gate1_threshold(51)["numerator"] == 51
    assert gate1_threshold(51)["denominator"] == 10
    assert gate1_threshold(60)["exact"] == "6"
    assert gate1_threshold(0)["exact"] == "5"


def test_threshold_document_records_labels_hash_before_retrieval(tmp_path: Path) -> None:
    frozen = _frozen()
    labels = _labels(frozen, _ids()[:51], tmp_path / "labels.json")
    output = tmp_path / "out" / "threshold.json"
    output.parent.mkdir()
    document = run_threshold(
        labels=labels, frozen=frozen, output_path=output, repo_root=tmp_path / "repo"
    )
    assert document["answerable_count"] == 51
    assert document["gate1_threshold"]["exact"] == "5.1"
    assert document["labels_sha256"] == labels.sha256
    assert document["retrieval_performed"] is False
    assert json.loads(output.read_text(encoding="utf-8")) == document


def test_threshold_refuses_incomplete_duplicate_and_invalid_labels(tmp_path: Path) -> None:
    frozen = _frozen()
    complete = _write_labels(tmp_path / "complete.json", frozen, answerable=_ids()[:10])
    data = json.loads(complete.read_text(encoding="utf-8"))
    data["questions"] = data["questions"][:-1]
    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete"):
        load_labels([incomplete], frozen)

    duplicate = tmp_path / "duplicate.json"
    data = json.loads(complete.read_text(encoding="utf-8"))
    data["questions"] = [data["questions"][0], data["questions"][0]]
    duplicate.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        load_labels([duplicate], frozen)

    invalid = tmp_path / "invalid.json"
    data = json.loads(complete.read_text(encoding="utf-8"))
    data["questions"][0]["schema_valid"] = False
    invalid.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="schema-valid"):
        load_labels([invalid], frozen)

    wrong = _write_labels(tmp_path / "wrong.json", frozen, answerable=[], schema="other/1")
    with pytest.raises(ValueError, match="schema"):
        load_labels([wrong], frozen)

    stopped = _write_labels(
        tmp_path / "stopped.json", frozen, answerable=[], stop_reason="uncertain_dispatch"
    )
    with pytest.raises(ValueError, match="stopped"):
        load_labels([stopped], frozen)


def test_labels_merge_across_documents_and_refuse_cross_document_duplicates(
    tmp_path: Path,
) -> None:
    frozen = _frozen()
    answerable = set(_ids()[:50])

    def split_document(subset: Sequence[str]) -> dict[str, Any]:
        return {
            "schema": m2_exposure.LABEL_SCHEMA,
            "route_id": "luna-azure-eu",
            "stop_reason": None,
            "total_cost_complete": True,
            "questions": [
                _label_entry(qid, qid in answerable, ("m-1",) if qid in answerable else ())
                for qid in subset
            ],
        }

    parts = [_ids()[:50], _ids()[50:]]
    paths = []
    for index, subset in enumerate(parts):
        path = tmp_path / f"part{index}.json"
        path.write_text(json.dumps(split_document(subset)), encoding="utf-8")
        paths.append(path)
    merged = load_labels(paths, frozen)
    assert len(merged.entries) == 100
    with pytest.raises(ValueError, match="Duplicate"):
        load_labels([paths[0], paths[0]], frozen)


def test_outputs_must_be_outside_the_repository_and_never_overwritten(tmp_path: Path) -> None:
    frozen = _frozen()
    labels = _labels(frozen, _ids()[:10], tmp_path / "labels.json")
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ValueError, match="outside the repository"):
        run_threshold(labels=labels, frozen=frozen, output_path=repo / "x.json", repo_root=repo)
    existing = tmp_path / "existing.json"
    existing.write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_threshold(labels=labels, frozen=frozen, output_path=existing, repo_root=repo)


def test_frozen_ids_fixture_verifies() -> None:
    frozen = m2_exposure.load_frozen_ids()
    assert len(frozen.ids) == 100
    assert len(set(frozen.ids)) == 100


def test_question_fixture_requires_the_frozen_set_and_six_abstentions(tmp_path: Path) -> None:
    frozen = _frozen()
    rows = [
        {
            "question_id": question.question_id,
            "question": question.message,
            "question_date": "2023/05/30 (Tue) 23:18",
            "answer_session_ids": ["s1"],
        }
        for question in _questions(frozen)
    ]
    path = tmp_path / "questions.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    loaded = load_questions(path, frozen)
    assert len(loaded) == 100
    assert sum(question.abstention for question in loaded) == 6


# --- generation ---------------------------------------------------------------


class _Route:
    model = "fake/model"


@dataclass
class _GenFakes:
    call_count: int = 0
    ledger: Decimal = Decimal("0")
    cost: Decimal = Decimal("0.01")
    fail_calls: bool = False

    async def call(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.call_count += 1
        if self.fail_calls:
            raise RuntimeError("must not dispatch")
        return {
            "choices": [
                {
                    "message": {"content": json.dumps({"queries": ["one", "two"]})},
                    "finish_reason": "stop",
                }
            ],
            "cost_usd": str(self.cost),
        }

    async def read_ledger(self) -> Decimal:
        return self.ledger

    async def ceiling(self, _payload: Mapping[str, Any]) -> Decimal:
        return Decimal("0.02")


def _gen_kwargs(fakes: _GenFakes) -> dict[str, Any]:
    async def lock_acquired() -> bool:
        return True

    async def lock_held() -> bool:
        return True

    async def attestation() -> None:
        return None

    return {
        "call": fakes.call,
        "check_attestation": attestation,
        "verify_route": lambda: _Route(),
        "account_scope": lambda _user: _NullScope(),
        "acquire_run_lock": lock_acquired,
        "check_run_lock": lock_held,
        "read_ledger_spend_usd": fakes.read_ledger,
        "next_call_ceiling_usd": fakes.ceiling,
    }


def test_generation_spend_cap_stops_before_dispatch_and_survives_resume(tmp_path: Path) -> None:
    frozen = _frozen(2)
    questions = _questions(frozen)
    output = tmp_path / "out" / "subqueries.json"
    output.parent.mkdir()
    fakes = _GenFakes(ledger=Decimal("0.30"), cost=Decimal("0.49"))
    document = asyncio.run(
        run_generation(
            questions=questions,
            output_path=output,
            spend_cap_usd=Decimal("0.50"),
            **_gen_kwargs(fakes),
        )
    )
    assert document["stop_reason"] == "spend_cap"
    assert fakes.call_count == 1
    assert document["start_spend_usd"] == "0.30"

    output.unlink()
    fakes.ledger = Decimal("0")
    fakes.fail_calls = True
    resumed = asyncio.run(
        run_generation(
            questions=questions,
            output_path=output,
            spend_cap_usd=Decimal("0.50"),
            **_gen_kwargs(fakes),
        )
    )
    assert resumed["start_spend_usd"] == "0.30"
    assert resumed["stop_reason"] == "spend_cap"
    assert fakes.call_count == 1


def test_generation_resumes_without_regenerating_recorded_questions(tmp_path: Path) -> None:
    frozen = _frozen(2)
    questions = _questions(frozen)
    output = tmp_path / "out" / "subqueries.json"
    output.parent.mkdir()
    fakes = _GenFakes()
    first = asyncio.run(
        run_generation(
            questions=questions,
            output_path=output,
            spend_cap_usd=Decimal("0.50"),
            **_gen_kwargs(fakes),
        )
    )
    assert fakes.call_count == 2
    assert len(first["questions"]) == 2
    assert first["subqueries_sha256"]

    output.unlink()
    fakes.fail_calls = True
    resumed = asyncio.run(
        run_generation(
            questions=questions,
            output_path=output,
            spend_cap_usd=Decimal("0.50"),
            **_gen_kwargs(fakes),
        )
    )
    assert fakes.call_count == 2
    assert resumed["stop_reason"] is None
    assert resumed["subqueries_sha256"] == first["subqueries_sha256"]


def test_generation_refuses_an_existing_output(tmp_path: Path) -> None:
    frozen = _frozen(2)
    output = tmp_path / "out" / "subqueries.json"
    output.parent.mkdir()
    output.write_text("{}", encoding="utf-8")
    fakes = _GenFakes()
    with pytest.raises(FileExistsError):
        asyncio.run(
            run_generation(
                questions=_questions(frozen),
                output_path=output,
                spend_cap_usd=Decimal("0.50"),
                **_gen_kwargs(fakes),
            )
        )
    assert fakes.call_count == 0


# --- measure ------------------------------------------------------------------


def _vector() -> EmbeddingVectorResult:
    return EmbeddingVectorResult(
        embedding=[0.1, 0.2], provider="fake", model="fake", storage_model="fake"
    )


def _arm(targets: Sequence[str], rendered: Sequence[str]) -> dict[str, Any]:
    rendered_set = set(rendered)
    return {
        "targets": {target: {"rendered": target in rendered_set} for target in targets},
        "target_exposed": bool(targets) and set(targets) <= rendered_set,
        "target_any_exposed": bool(set(targets) & rendered_set),
        "rendered_target_ids": sorted(rendered_set & set(targets)),
        "rendered_count": len(rendered),
        "rendered_ids": list(rendered),
    }


@dataclass
class _MeasureFakes:
    fingerprint_sha: str = "a"
    log_count: int = 3
    calls: int = 0

    async def embed(self, texts: Sequence[str], _is_query: bool) -> list[EmbeddingVectorResult]:
        return [_vector() for _ in texts]

    async def ceiling(self, _texts: Sequence[str]) -> Decimal:
        return Decimal("0.01")

    async def ledger(self) -> Decimal:
        return Decimal("0")

    async def fingerprint(self) -> dict[str, Any]:
        return {"sha256": self.fingerprint_sha, "users": 2, "rows": {}}

    async def log_count_value(self) -> int:
        return self.log_count

    async def measure(
        self,
        _question: Question,
        components: tuple[str, ...],
        _vectors: Mapping[str, EmbeddingVectorResult],
        merge: str,
        targets: Sequence[str],
    ) -> dict[str, Any]:
        self.calls += 1
        baseline = _arm(targets, [targets[0]] if targets else [])
        candidate = _arm(targets, list(targets)) if merge == "additive" else _arm(targets, [])
        return {"baseline": baseline, "candidate": candidate, "merge": merge}


def _subqueries_document(
    questions: Sequence[Question], *, start: str = "0.30", cap: str = "0.50"
) -> dict[str, Any]:
    entries = [
        {
            "question_id": question.question_id,
            "subqueries": ["one", "two"] if index == 0 else [],
            "triggered": index == 0,
            "schema_valid": True,
            "latency_seconds": 0.5,
            "error_category": None,
        }
        for index, question in enumerate(questions)
    ]
    return {
        "schema": m2_exposure.GENERATION_SCHEMA,
        "stop_reason": None,
        "start_spend_usd": start,
        "spend_cap_usd": cap,
        "subqueries_sha256": m2_exposure.pinned_subqueries_sha256(entries),
        "questions": entries,
    }


def _measure_kwargs(fakes: _MeasureFakes) -> dict[str, Any]:
    async def lock_acquired() -> bool:
        return True

    async def lock_held() -> bool:
        return True

    return {
        "measure_question": fakes.measure,
        "embed_texts": fakes.embed,
        "fingerprint": fakes.fingerprint,
        "retrieval_log_count": fakes.log_count_value,
        "read_ledger_spend_usd": fakes.ledger,
        "embedding_ceiling_usd": fakes.ceiling,
        "acquire_run_lock": lock_acquired,
        "check_run_lock": lock_held,
    }


def _pinned(frozen: FrozenIds, questions: Sequence[Question], path: Path) -> dict[str, Any]:
    path.write_text(json.dumps(_subqueries_document(questions)), encoding="utf-8")
    return load_pinned_subqueries(path, frozen)


def test_measure_no_trigger_stays_in_the_denominator_as_baseline(tmp_path: Path) -> None:
    frozen = _frozen(2)
    questions = _questions(frozen)
    labels = _labels(frozen, [frozen.ids[0], frozen.ids[1]], tmp_path / "labels.json")
    subqueries = _pinned(frozen, questions, tmp_path / "subqueries.json")
    output = tmp_path / "out" / "measure.json"
    output.parent.mkdir()
    fakes = _MeasureFakes()
    document = asyncio.run(
        run_measure(
            questions=questions,
            labels=labels,
            subqueries=subqueries,
            output_path=output,
            **_measure_kwargs(fakes),
        )
    )
    assert document["questions"][0]["triggered"] is True
    untriggered = next(row for row in document["questions"] if row["question_id"] == frozen.ids[1])
    assert untriggered["triggered"] is False
    assert untriggered["candidate"]["target_exposed"] is True  # baseline, unchanged
    assert document["side_effects"]["fingerprint_before"] == "a"


def test_measure_fingerprint_change_is_a_hard_failure(tmp_path: Path) -> None:
    frozen = _frozen(2)
    questions = _questions(frozen)
    labels = _labels(frozen, [frozen.ids[0], frozen.ids[1]], tmp_path / "labels.json")
    subqueries = _pinned(frozen, questions, tmp_path / "subqueries.json")
    output = tmp_path / "out" / "measure.json"
    output.parent.mkdir()
    fakes = _MeasureFakes()
    calls = {"n": 0}

    async def fingerprint() -> dict[str, Any]:
        calls["n"] += 1
        return {"sha256": "a" if calls["n"] == 1 else "b", "users": 2, "rows": {}}

    kwargs = _measure_kwargs(fakes)
    kwargs["fingerprint"] = fingerprint
    with pytest.raises(m2_exposure.M2SideEffectError, match="fingerprint"):
        asyncio.run(
            run_measure(
                questions=questions,
                labels=labels,
                subqueries=subqueries,
                output_path=output,
                **kwargs,
            )
        )
    assert not output.exists()


def test_measure_retrieval_log_change_is_a_hard_failure(tmp_path: Path) -> None:
    frozen = _frozen(2)
    questions = _questions(frozen)
    labels = _labels(frozen, [frozen.ids[0], frozen.ids[1]], tmp_path / "labels.json")
    subqueries = _pinned(frozen, questions, tmp_path / "subqueries.json")
    output = tmp_path / "out" / "measure.json"
    output.parent.mkdir()
    fakes = _MeasureFakes()
    counts = iter([3, 4])

    async def log_count() -> int:
        return next(counts)

    kwargs = _measure_kwargs(fakes)
    kwargs["retrieval_log_count"] = log_count
    with pytest.raises(m2_exposure.M2SideEffectError, match="retrieval_log"):
        asyncio.run(
            run_measure(
                questions=questions,
                labels=labels,
                subqueries=subqueries,
                output_path=output,
                **kwargs,
            )
        )
    assert not output.exists()


def test_wrong_database_is_refused() -> None:
    assert (
        m2_exposure.check_m2_database("postgresql://user:secret@host/daemon_lme_bench_dev2_final")
        == "daemon_lme_bench_dev2_final"
    )
    with pytest.raises(ValueError, match="exactly"):
        m2_exposure.check_m2_database("postgresql://user:secret@host/daemon_lme_bench_dev2")
    with pytest.raises(ValueError):
        m2_exposure.check_m2_database("postgresql://user:secret@host/production")


# --- gates --------------------------------------------------------------------


def _gate_arms(
    targets: Sequence[str], baseline_rendered: Sequence[str], candidate_rendered: Sequence[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        _arm(targets, baseline_rendered),
        _arm(targets, candidate_rendered),
    )


def _measure_row(
    qid: str,
    *,
    answerable: bool,
    target_ids: Sequence[str],
    baseline_rendered: Sequence[str],
    candidate_rendered: Sequence[str],
    triggered: bool = True,
) -> dict[str, Any]:
    baseline, candidate = _gate_arms(target_ids, baseline_rendered, candidate_rendered)
    return {
        "question_id": qid,
        "answerable_from_store": answerable,
        "abstention": qid.endswith("_abs"),
        "triggered": triggered,
        "latency_seconds": 1.0,
        "error_category": None,
        "baseline": baseline,
        "candidate": candidate,
        "round_robin": _arm(target_ids, list(target_ids)),
        "candidate_only_rendered_ids": sorted(set(candidate_rendered) - set(baseline_rendered)),
    }


def _gate_documents(
    frozen: FrozenIds,
    *,
    newly: int,
    lost: bool = False,
    partial: bool = False,
    plausible: int = 0,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], LabelSet]:
    answerable_ids = _ids()[:51]
    labels = LabelSet(
        {qid: LabelEntry(qid, ("m-1", "m-2"), qid in set(answerable_ids)) for qid in frozen.ids},
        (),
        "labels-hash",
    )
    threshold = {
        "schema": m2_exposure.THRESHOLD_SCHEMA,
        "labels_sha256": labels.sha256,
        "answerable_count": 51,
        "gate1_threshold": gate1_threshold(51),
    }
    rows = []
    for qid in frozen.ids:
        answerable = qid in set(answerable_ids)
        targets = ("m-1", "m-2") if answerable else ()
        baseline_rendered: list[str] = []
        candidate_rendered: list[str] = []
        if answerable and qid in set(answerable_ids[:newly]):
            candidate_rendered = ["m-1", "m-2"]
        if lost and qid == answerable_ids[0]:
            baseline_rendered = ["m-1"]
            candidate_rendered = []
        if partial and qid == answerable_ids[newly]:
            candidate_rendered = ["m-1"]
        rows.append(
            _measure_row(
                qid,
                answerable=answerable,
                target_ids=targets,
                baseline_rendered=baseline_rendered,
                candidate_rendered=candidate_rendered,
            )
        )
    plausibility = {
        "schema": m2_exposure.PLAUSIBILITY_SCHEMA,
        "questions": {},
    }
    added = 0
    for qid in [value for value in frozen.ids if value.endswith("_abs")]:
        row = next(value for value in rows if value["question_id"] == qid)
        if added < plausible:
            row["candidate_only_rendered_ids"] = [f"false-premise-{qid}"]
            plausibility["questions"][qid] = {"plausible_ids": [f"false-premise-{qid}"]}
            added += 1
        else:
            plausibility["questions"][qid] = {"plausible_ids": []}
    measure = {
        "schema": m2_exposure.MEASURE_SCHEMA,
        "stop_reason": None,
        "labels_sha256": labels.sha256,
        "questions": rows,
    }
    return threshold, measure, plausibility, labels


def _run_gates(
    tmp_path: Path,
    frozen: FrozenIds,
    *,
    newly: int,
    lost: bool = False,
    partial: bool = False,
    plausible: int = 0,
) -> dict[str, Any]:
    threshold, measure, plausibility, labels = _gate_documents(
        frozen, newly=newly, lost=lost, partial=partial, plausible=plausible
    )
    output = tmp_path / "out" / "gates.json"
    output.parent.mkdir(exist_ok=True)
    output.unlink(missing_ok=True)
    return run_gates(
        threshold=threshold,
        labels=labels,
        measure=measure,
        plausibility=plausibility,
        output_path=output,
        repo_root=tmp_path / "repo",
    )


def test_gate1_passes_and_fails_against_the_exact_non_integer_threshold(tmp_path: Path) -> None:
    frozen = _frozen()
    passed = _run_gates(tmp_path, frozen, newly=6)
    assert passed["gate1"]["threshold_exact"] == "5.1"
    assert passed["gate1"]["passed"] is True
    assert passed["result"] == "pass"

    failed = _run_gates(tmp_path, frozen, newly=5)
    assert failed["gate1"]["newly_exposed_count"] == 5
    assert failed["gate1"]["passed"] is False
    assert failed["result"] == "fail"


def test_gate2_catches_a_single_lost_target(tmp_path: Path) -> None:
    document = _run_gates(tmp_path, _frozen(), newly=6, lost=True)
    assert document["gate2"]["passed"] is False
    assert document["gate2"]["losses"]
    assert document["result"] == "fail"


def test_strict_metric_does_not_count_partial_exposure(tmp_path: Path) -> None:
    document = _run_gates(tmp_path, _frozen(), newly=5, partial=True)
    assert document["gate1"]["newly_exposed_count"] == 5
    assert document["gate1"]["passed"] is False


def test_round_robin_cannot_change_the_result(tmp_path: Path) -> None:
    frozen = _frozen()
    threshold, measure, plausibility, labels = _gate_documents(frozen, newly=6)
    for row in measure["questions"]:
        row["round_robin"] = {
            "targets": {},
            "target_exposed": False,
            "target_any_exposed": False,
            "rendered_ids": [],
        }
    output = tmp_path / "out" / "gates.json"
    output.parent.mkdir()
    document = run_gates(
        threshold=threshold,
        labels=labels,
        measure=measure,
        plausibility=plausibility,
        output_path=output,
        repo_root=tmp_path / "repo",
    )
    assert document["result"] == "pass"
    assert document["gate3"] == {"covered": True, "test": m2_exposure.GATE3_TEST}


def test_gate4_flags_at_two_and_not_before(tmp_path: Path) -> None:
    one = _run_gates(tmp_path, _frozen(), newly=6, plausible=1)
    assert one["gate4"]["plausible_candidate_only_count"] == 1
    assert one["gate4"]["flagged_for_owner_review"] is False
    assert one["result"] == "pass"

    two = _run_gates(tmp_path, _frozen(), newly=6, plausible=2)
    assert two["gate4"]["plausible_candidate_only_count"] == 2
    assert two["gate4"]["flagged_for_owner_review"] is True
    assert two["result"] == "flag_for_owner_review"


def test_gate4_refuses_to_finish_without_the_plausibility_input(tmp_path: Path) -> None:
    frozen = _frozen()
    threshold, measure, plausibility, labels = _gate_documents(frozen, newly=6)
    plausibility["questions"].pop(next(iter(plausibility["questions"])))
    output = tmp_path / "out" / "gates.json"
    output.parent.mkdir()
    with pytest.raises(ValueError, match="missing"):
        run_gates(
            threshold=threshold,
            labels=labels,
            measure=measure,
            plausibility=plausibility,
            output_path=output,
            repo_root=tmp_path / "repo",
        )


def test_gates_refuse_mismatched_labels_and_output_overwrite(tmp_path: Path) -> None:
    frozen = _frozen()
    threshold, measure, plausibility, labels = _gate_documents(frozen, newly=6)
    output = tmp_path / "out" / "gates.json"
    output.parent.mkdir()
    bad_labels = LabelSet(labels.entries, (), "other-hash")
    with pytest.raises(ValueError, match="different labels"):
        run_gates(
            threshold=threshold,
            labels=bad_labels,
            measure=measure,
            plausibility=plausibility,
            output_path=output,
            repo_root=tmp_path / "repo",
        )
    output.write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_gates(
            threshold=threshold,
            labels=labels,
            measure=measure,
            plausibility=plausibility,
            output_path=output,
            repo_root=tmp_path / "repo",
        )


# --- retention and report -----------------------------------------------------


def test_retention_reads_only_not_answerable_labels_without_spend(tmp_path: Path) -> None:
    frozen = _frozen()
    questions = _questions(frozen)
    labels = _labels(frozen, _ids()[:90], tmp_path / "labels.json")
    output = tmp_path / "out" / "retention.json"
    output.parent.mkdir()
    seen: list[str] = []

    async def reader(
        _user_id: Any, question_id: str, sessions: Sequence[str]
    ) -> list[dict[str, Any]]:
        seen.append(question_id)
        return [
            {
                "conversation_id": "c1",
                "input_snippet": "x" * 1000,
                "dedup_results": {"chunk": 1},
                "model_used": "fake",
                "created_at": None,
            }
        ]

    document = asyncio.run(
        run_retention(
            questions=questions,
            labels=labels,
            reader=reader,
            output_path=output,
            repo_root=tmp_path / "repo",
        )
    )
    assert set(seen) == set(labels.not_answerable_ids)
    assert document["spend_usd"] == "0"
    assert "first 1000 characters" in document["limits"]["input_snippet"]
    assert document["question_count"] == len(labels.not_answerable_ids)


def test_report_is_sanitized_and_ignores_benchmark_text(tmp_path: Path) -> None:
    frozen = _frozen()
    threshold, measure, plausibility, labels = _gate_documents(frozen, newly=6, plausible=2)
    for row in measure["questions"]:
        row["question"] = "PLANTED BENCHMARK TEXT"
        row["note"] = "PLANTED NOTE"
        row["baseline"]["rendered_text"] = "PLANTED RENDERED TEXT"
    measure["planted"] = "PLANTED DOCUMENT TEXT"
    (tmp_path / "out").mkdir()
    gates = run_gates(
        threshold=threshold,
        labels=labels,
        measure=measure,
        plausibility=plausibility,
        output_path=tmp_path / "out" / "gates.json",
        repo_root=tmp_path / "repo",
    )
    output = tmp_path / "out" / "report.json"
    document = run_report(
        measure=measure,
        gates=gates,
        output_path=output,
        repo_root=tmp_path / "repo",
    )
    serialized = output.read_text(encoding="utf-8")
    assert "PLANTED" not in serialized
    assert document["question_count"] == 100
    assert set(document["category_counts"]) == set(m2_exposure.REPORT_CATEGORIES)
    assert all(set(row) == {"question_id", "category"} for row in document["questions"])
    assert document["gate4_flagged"] is True
