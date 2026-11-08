"""Offline tests for the M2 exposure gate driver. Fakes only: no database, no network."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator.memory.embedding import EmbeddingVectorResult
from orchestrator.memory.multi_query import DECOMPOSITION_ROUTE_ID
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
LINEAGE_SHA = "c" * 64
REMAP_DB_NAME = "daemon_lme_bench_dev2_final_remap"


def _forbidden(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("Inference/embedding collaborator must not be reached")


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
        "target_groups": [[mid] for mid in targets],
        "needs_memory_date": False,
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
    if schema in {"dev2-labelling/1", "dev2-labelling/2"}:
        for entry in entries:
            entry["memory_ids"] = [group[0] for group in entry.pop("target_groups")]
            entry.pop("needs_memory_date")
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


@pytest.mark.parametrize("schema", ["dev2-labelling/1", "dev2-labelling/2"])
def test_historical_labels_load_but_cannot_set_the_threshold(tmp_path: Path, schema: str) -> None:
    frozen = _frozen()
    path = _write_labels(
        tmp_path / "labels-old.json", frozen, answerable=_ids()[:10], schema=schema
    )
    labels = load_labels([path], frozen)
    assert labels.schemas == (schema,)
    output = tmp_path / "out" / "threshold.json"
    with pytest.raises(ValueError, match="dev2-labelling/3"):
        run_threshold(labels=labels, frozen=frozen, output_path=output, repo_root=tmp_path / "repo")
    assert not output.exists()


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


def test_threshold_excludes_answerable_abs_questions_from_the_denominator(
    tmp_path: Path,
) -> None:
    frozen = _frozen()
    abs_id = next(qid for qid in frozen.ids if qid.endswith("_abs"))
    labels = _labels(frozen, [*_ids()[:50], abs_id], tmp_path / "labels.json")
    output = tmp_path / "out" / "threshold.json"
    output.parent.mkdir()
    document = run_threshold(
        labels=labels, frozen=frozen, output_path=output, repo_root=tmp_path / "repo"
    )
    assert len(labels.answerable_ids) == 51
    assert document["answerable_count"] == 50
    assert document["gate1_threshold"]["exact"] == "5"
    assert document["exposure_excluded_abs_count"] == 1
    assert document["exposure_excluded_abs_ids"] == [abs_id]
    assert abs_id not in document["answerable_question_ids"]


@pytest.mark.parametrize(("answerable_count", "expected"), [(49, "5"), (50, "5"), (51, "5.1")])
def test_threshold_boundaries_hold_after_abs_exclusion(
    tmp_path: Path, answerable_count: int, expected: str
) -> None:
    frozen = _frozen()
    abs_id = next(qid for qid in frozen.ids if qid.endswith("_abs"))
    labels = _labels(
        frozen, [*_ids()[:answerable_count], abs_id], tmp_path / f"labels-{answerable_count}.json"
    )
    output = tmp_path / f"out-{answerable_count}" / "threshold.json"
    output.parent.mkdir()
    document = run_threshold(
        labels=labels, frozen=frozen, output_path=output, repo_root=tmp_path / "repo"
    )
    assert document["answerable_count"] == answerable_count
    assert document["gate1_threshold"]["exact"] == expected


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


def _gen_kwargs(fakes: _GenFakes, directory: Path) -> dict[str, Any]:
    async def lock_acquired() -> bool:
        return True

    async def lock_held() -> bool:
        return True

    async def attestation() -> None:
        return None

    return {
        "start_marker": directory / "start-marker.json",
        "remap_lineage_sha256": LINEAGE_SHA,
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
            **_gen_kwargs(fakes, tmp_path),
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
            **_gen_kwargs(fakes, tmp_path),
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
            **_gen_kwargs(fakes, tmp_path),
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
            **_gen_kwargs(fakes, tmp_path),
        )
    )
    assert fakes.call_count == 2
    assert resumed["stop_reason"] is None
    assert resumed["subqueries_sha256"] == first["subqueries_sha256"]


def test_generation_keeps_the_first_start_for_a_new_output_path(tmp_path: Path) -> None:
    frozen = _frozen(2)
    questions = _questions(frozen)
    first_output = tmp_path / "first" / "subqueries.json"
    first_output.parent.mkdir()
    marker = tmp_path / "start-marker.json"
    first = asyncio.run(
        run_generation(
            questions=questions,
            output_path=first_output,
            spend_cap_usd=Decimal("0.50"),
            **_gen_kwargs(_GenFakes(ledger=Decimal("0.10")), tmp_path),
        )
    )
    assert first["start_spend_usd"] == "0.10"
    assert json.loads(marker.read_text(encoding="utf-8"))["start_spend_usd"] == "0.10"

    second_output = tmp_path / "second" / "subqueries.json"
    second_output.parent.mkdir()
    second_fakes = _GenFakes(ledger=Decimal("0.80"), fail_calls=True)
    second = asyncio.run(
        run_generation(
            questions=questions,
            output_path=second_output,
            spend_cap_usd=Decimal("0.50"),
            **_gen_kwargs(second_fakes, tmp_path),
        )
    )
    # The new output path must not re-anchor the ceiling to the current ledger.
    assert second["start_spend_usd"] == "0.10"
    assert second["spend_ceiling_usd"] == "0.60"
    assert second["stop_reason"] == "spend_cap"
    assert second_fakes.call_count == 0


def test_generation_refuses_a_corrupt_start_marker_before_any_spend(tmp_path: Path) -> None:
    frozen = _frozen(2)
    output = tmp_path / "out" / "subqueries.json"
    output.parent.mkdir()
    (tmp_path / "start-marker.json").write_text("not json", encoding="utf-8")
    fakes = _GenFakes()
    with pytest.raises(ValueError, match="corrupt"):
        asyncio.run(
            run_generation(
                questions=_questions(frozen),
                output_path=output,
                spend_cap_usd=Decimal("0.50"),
                **_gen_kwargs(fakes, tmp_path),
            )
        )
    assert fakes.call_count == 0
    journal = m2_exposure._journal_path(output)
    assert not journal.exists() or journal.read_text(encoding="utf-8") == ""


def test_generation_refuses_a_start_marker_that_disagrees_with_its_journal(
    tmp_path: Path,
) -> None:
    frozen = _frozen(2)
    questions = _questions(frozen)
    output = tmp_path / "out" / "subqueries.json"
    output.parent.mkdir()
    marker = tmp_path / "start-marker.json"
    fakes = _GenFakes()
    asyncio.run(
        run_generation(
            questions=questions,
            output_path=output,
            spend_cap_usd=Decimal("0.50"),
            **_gen_kwargs(fakes, tmp_path),
        )
    )
    calls_after_first = fakes.call_count
    output.unlink()
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["start_spend_usd"] = "0.01"
    marker.write_text(json.dumps(payload), encoding="utf-8")
    fakes.fail_calls = True
    with pytest.raises(ValueError, match="disagrees"):
        asyncio.run(
            run_generation(
                questions=questions,
                output_path=output,
                spend_cap_usd=Decimal("0.50"),
                **_gen_kwargs(fakes, tmp_path),
            )
        )
    assert fakes.call_count == calls_after_first


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
                **_gen_kwargs(fakes, tmp_path),
            )
        )
    assert fakes.call_count == 0


def test_generation_refuses_a_document_without_its_journal(tmp_path: Path) -> None:
    frozen = _frozen(2)
    questions = _questions(frozen)
    output = tmp_path / "out" / "subqueries.json"
    output.parent.mkdir()
    output.write_text(json.dumps(_subqueries_document(questions)), encoding="utf-8")
    fakes = _GenFakes()
    with pytest.raises(ValueError, match="without its journal"):
        asyncio.run(
            run_generation(
                questions=questions,
                output_path=output,
                spend_cap_usd=Decimal("0.50"),
                **_gen_kwargs(fakes, tmp_path),
            )
        )
    assert fakes.call_count == 0


def test_generation_refuses_a_cap_other_than_the_frozen_one(tmp_path: Path) -> None:
    frozen = _frozen(2)
    output = tmp_path / "out" / "subqueries.json"
    output.parent.mkdir()
    fakes = _GenFakes()
    with pytest.raises(ValueError, match="frozen"):
        asyncio.run(
            run_generation(
                questions=_questions(frozen),
                output_path=output,
                spend_cap_usd=Decimal("0.60"),
                **_gen_kwargs(fakes, tmp_path),
            )
        )
    assert fakes.call_count == 0


def test_generation_request_matches_productions_normalized_input() -> None:
    question = Question("q0000", "first line\n\n  second   line ", NOW, False, ())
    body = json.loads(m2_exposure.generation_request(question)["messages"][1]["content"])
    assert body["message"] == "first line second line"

    long = Question("q0001", "word " * 5000, NOW, False, ())
    long_body = json.loads(m2_exposure.generation_request(long)["messages"][1]["content"])
    assert len(long_body["message"]) <= m2_exposure.MAX_INPUT_CHARS


def test_generation_declares_its_benchmark_user_writes(tmp_path: Path) -> None:
    frozen = _frozen(2)
    output = tmp_path / "out" / "subqueries.json"
    output.parent.mkdir()
    document = asyncio.run(
        run_generation(
            questions=_questions(frozen),
            output_path=output,
            spend_cap_usd=Decimal("0.50"),
            **_gen_kwargs(_GenFakes(), tmp_path),
        )
    )
    effects = document["side_effects"]
    assert effects["users_inserted"] is True
    assert effects["entitlement_accounts_inserted"] is True


def test_outputs_record_the_route_mapping(tmp_path: Path) -> None:
    frozen = _frozen(2)
    output = tmp_path / "out" / "subqueries.json"
    output.parent.mkdir()
    document = asyncio.run(
        run_generation(
            questions=_questions(frozen),
            output_path=output,
            spend_cap_usd=Decimal("0.50"),
            **_gen_kwargs(_GenFakes(), tmp_path),
        )
    )
    mapping = document["route_mapping"]
    assert mapping["block_d"]["route_id"] == "luna-azure-eu"
    assert mapping["block_d"]["reasoning_effort"] == "none"
    assert mapping["block_d"]["closed"] is True
    assert mapping["production_decomposition"]["route_id"] == DECOMPOSITION_ROUTE_ID
    assert mapping["production_decomposition"]["approved"] is False
    gates_document = _run_gates(tmp_path, _frozen(), newly=6)
    assert gates_document["route_mapping"] == mapping


def test_pinned_subqueries_bind_spend_and_require_their_journal(tmp_path: Path) -> None:
    frozen = _frozen(2)
    questions = _questions(frozen)
    path = tmp_path / "subqueries.json"
    document = _subqueries_document(questions)
    path.write_text(json.dumps(document), encoding="utf-8")
    journal = _generation_journal(path, questions, document)
    load_pinned_subqueries(path, frozen)

    edited_start = json.loads(json.dumps(document))
    edited_start["start_spend_usd"] = "0.00"
    path.write_text(json.dumps(edited_start), encoding="utf-8")
    with pytest.raises(ValueError, match="recorded hash"):
        load_pinned_subqueries(path, frozen)

    edited_cap = json.loads(json.dumps(document))
    edited_cap["spend_cap_usd"] = "5.00"
    path.write_text(json.dumps(edited_cap), encoding="utf-8")
    with pytest.raises(ValueError):
        load_pinned_subqueries(path, frozen)

    path.write_text(json.dumps(document), encoding="utf-8")
    journal.unlink()
    with pytest.raises(ValueError, match="journal"):
        load_pinned_subqueries(path, frozen)


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
    fail_on_question: str | None = None
    embed_calls: list[list[str]] = field(default_factory=list)
    measured_ids: list[str] = field(default_factory=list)

    async def embed(self, texts: Sequence[str], _is_query: bool) -> list[EmbeddingVectorResult]:
        self.embed_calls.append(list(texts))
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
        question: Question,
        components: tuple[str, ...],
        _vectors: Mapping[str, EmbeddingVectorResult],
        merge: str,
        targets: Sequence[str],
    ) -> dict[str, Any]:
        if self.fail_on_question == question.question_id:
            raise RuntimeError("forced mid-run failure")
        self.calls += 1
        self.measured_ids.append(question.question_id)
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
        "subqueries_sha256": m2_exposure.pinned_subqueries_sha256(
            entries, start_spend_usd=start, spend_cap_usd=cap
        ),
        "questions": entries,
    }


def _generation_journal(
    path: Path, questions: Sequence[Question], document: Mapping[str, Any]
) -> Path:
    manifest = m2_exposure._generation_static_manifest(
        questions, Decimal(str(document["start_spend_usd"])), LINEAGE_SHA
    )
    lines = [json.dumps(manifest, sort_keys=True)]
    for entry in document["questions"]:
        lines.append(json.dumps({"kind": "result", **entry}, sort_keys=True))
    journal = m2_exposure._journal_path(path)
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return journal


def _measure_kwargs(fakes: _MeasureFakes, directory: Path) -> dict[str, Any]:
    async def lock_acquired() -> bool:
        return True

    async def lock_held() -> bool:
        return True

    return {
        "start_marker": directory / "start-marker.json",
        "remap_lineage_sha256": LINEAGE_SHA,
        "read_embedding_route": lambda: ("fake-embedding-route", 2),
        "measure_question": fakes.measure,
        "embed_texts": fakes.embed,
        "fingerprint": fakes.fingerprint,
        "retrieval_log_count": fakes.log_count_value,
        "read_ledger_spend_usd": fakes.ledger,
        "embedding_ceiling_usd": fakes.ceiling,
        "acquire_run_lock": lock_acquired,
        "check_run_lock": lock_held,
        "read_retrieval_logging_flags": lambda: (False, False),
    }


def _pinned(frozen: FrozenIds, questions: Sequence[Question], path: Path) -> dict[str, Any]:
    document = _subqueries_document(questions)
    path.write_text(json.dumps(document), encoding="utf-8")
    _generation_journal(path, questions, document)
    m2_exposure._create_start_marker(
        path.parent / "start-marker.json", Decimal(str(document["start_spend_usd"]))
    )
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
            **_measure_kwargs(fakes, tmp_path),
        )
    )
    assert document["questions"][0]["triggered"] is True
    untriggered = next(row for row in document["questions"] if row["question_id"] == frozen.ids[1])
    assert untriggered["triggered"] is False
    assert untriggered["candidate"]["target_exposed"] is True  # baseline, unchanged
    assert document["side_effects"]["fingerprint_before"] == "a"


def _lexical_rows() -> list[dict[str, Any]]:
    from orchestrator.eval.bench_db import question_user_id

    return [
        {
            "user_id": str(question_user_id(qid)),
            "memories": int(index < 99),
            "embedded": 0,
            "modelled": 0,
        }
        for index, qid in enumerate(_ids())
    ]


@dataclass
class _LexicalFakes(_MeasureFakes):
    corpus_rows: list[dict[str, Any]] = field(default_factory=_lexical_rows)
    ledger_state: dict[str, Any] = field(
        default_factory=lambda: {
            "settled_microusd": 306398,
            "held_microusd": 0,
            "open_holds": 0,
            "digest": "d" * 32,
        }
    )
    memory_count: int = 99
    snapshot_calls: int = 0
    mutate_after_retrieval: Any = None

    async def fingerprint(self) -> dict[str, Any]:
        self.snapshot_calls += 1
        return {
            "sha256": self.fingerprint_sha,
            "users": 100,
            "rows": {"memories": self.memory_count},
        }

    async def read_corpus(self) -> Sequence[Mapping[str, Any]]:
        return self.corpus_rows

    async def read_ledger(self) -> Mapping[str, Any]:
        return dict(self.ledger_state)

    async def lexical_question(
        self,
        question: Question,
        components: tuple[str, ...],
        merge: str,
        targets: Sequence[str],
    ) -> dict[str, Any]:
        self.calls += 1
        self.measured_ids.append(question.question_id)
        if self.mutate_after_retrieval is not None:
            self.mutate_after_retrieval(self)
        if question.question_id == self.fail_on_question:
            raise RuntimeError("Synthetic lexical retrieval failed")
        return {"baseline": _arm(targets, targets), "candidate": _arm(targets, targets)}


def _lexical_kwargs(directory: Path, fakes: _LexicalFakes) -> dict[str, Any]:
    frozen = _frozen()
    questions = _questions(frozen)
    return {
        **_measure_kwargs(fakes, directory),
        "questions": questions,
        "labels": _labels(frozen, frozen.ids[:51], directory / "labels.json"),
        "subqueries": _pinned(frozen, questions, directory / "subqueries.json"),
        "output_path": directory / "measure.json",
        "repo_root": directory / "repo",
        "retrieval_mode": "lexical",
        "read_lexical_corpus": fakes.read_corpus,
        "read_ledger_state": fakes.read_ledger,
        "measure_lexical_question": fakes.lexical_question,
        "read_embedding_route": _forbidden,
        "embedding_ceiling_usd": _forbidden,
        "embed_texts": _forbidden,
        "read_ledger_spend_usd": _forbidden,
        "measure_question": _forbidden,
    }


def test_lexical_driver_has_no_embedding_collaborators_and_preserves_budget(tmp_path: Path) -> None:
    fakes = _LexicalFakes()
    kwargs = _lexical_kwargs(tmp_path, fakes)
    preserved = {
        path: path.read_bytes()
        for path in (
            tmp_path / "labels.json",
            tmp_path / "subqueries.json",
            m2_exposure._journal_path(tmp_path / "subqueries.json"),
            tmp_path / "start-marker.json",
        )
    }
    document = asyncio.run(run_measure(**kwargs))
    assert document["stop_reason"] is None
    assert document["retrieval_mode"] == "lexical"
    assert document["retrieval_adapter"] == "m2-lexical/1"
    assert document["completed_question_count"] == 100
    assert document["start_spend_usd"] == "0.30"
    assert document["spend_ceiling_usd"] == "0.80"
    assert document["spend_high_water_usd"] == "0.306398"
    assert document["lexical_safety"]["verified"] is True
    assert document["lexical_safety"]["before"]["corpus"]["memories"] == 99
    assert fakes.snapshot_calls == 2 and fakes.calls == 200
    assert all(path.read_bytes() == value for path, value in preserved.items())
    records = [
        json.loads(line)
        for line in m2_exposure._journal_path(kwargs["output_path"]).read_text().splitlines()
    ]
    assert records[0]["embedding_route_id"] is None
    assert records[0]["retrieval_mode"] == "lexical"
    assert all(row["kind"] != "embedding" for row in records)


@pytest.mark.parametrize(
    "invalid", ["vector", "model", "missing", "duplicate", "foreign", "count", "holds"]
)
def test_lexical_admission_refuses_before_journal_or_retrieval(
    tmp_path: Path, invalid: str
) -> None:
    fakes = _LexicalFakes()
    kwargs = _lexical_kwargs(tmp_path, fakes)
    if invalid == "vector":
        fakes.corpus_rows[0]["embedded"] = 1
    elif invalid == "model":
        fakes.corpus_rows[0]["modelled"] = 1
    elif invalid == "missing":
        fakes.corpus_rows.pop()
    elif invalid == "duplicate":
        fakes.corpus_rows[-1] = fakes.corpus_rows[0]
    elif invalid == "foreign":
        fakes.corpus_rows[-1]["user_id"] = str(uuid.UUID(int=1))
    elif invalid == "count":
        fakes.memory_count = 100
    else:
        fakes.ledger_state.update(open_holds=1, held_microusd=0)
    with pytest.raises((ValueError, m2_exposure.M2SideEffectError)):
        asyncio.run(run_measure(**kwargs))
    assert fakes.calls == 0
    assert not m2_exposure._journal_path(kwargs["output_path"]).exists()


def test_lexical_admission_requires_100_unique_questions_and_readers(tmp_path: Path) -> None:
    kwargs = _lexical_kwargs(tmp_path, _LexicalFakes())
    kwargs["questions"] = kwargs["questions"][:-1]
    with pytest.raises(ValueError, match="100"):
        asyncio.run(run_measure(**kwargs))
    kwargs["questions"] = _questions(_frozen())
    kwargs["read_ledger_state"] = None
    with pytest.raises(ValueError, match="safety readers"):
        asyncio.run(run_measure(**kwargs))


@pytest.mark.parametrize("changed", ["fingerprint", "log", "actual", "hold", "ledger_digest"])
@pytest.mark.parametrize("retrieval_fails", [False, True])
def test_lexical_checks_safety_after_success_and_failure(
    tmp_path: Path,
    changed: str,
    retrieval_fails: bool,
) -> None:
    fakes = _LexicalFakes()
    kwargs = _lexical_kwargs(tmp_path, fakes)

    def mutate(state: _LexicalFakes) -> None:
        if changed == "fingerprint":
            state.fingerprint_sha = "changed"
        elif changed == "log":
            state.log_count = 4
        elif changed == "actual":
            state.ledger_state["settled_microusd"] = 306399
        elif changed == "hold":
            state.ledger_state["open_holds"] = 1
        else:
            state.ledger_state["digest"] = "e" * 32

    fakes.mutate_after_retrieval = mutate
    if retrieval_fails:
        fakes.fail_on_question = _ids()[0]
    with pytest.raises(m2_exposure.M2SideEffectError):
        asyncio.run(run_measure(**kwargs))
    document = json.loads(Path(kwargs["output_path"]).read_text())
    assert document["stop_reason"] == "side_effect"
    assert document["lexical_safety"]["verified"] is False
    assert fakes.snapshot_calls == 2


def test_lexical_resume_rechecks_corpus_and_retains_completed_questions(tmp_path: Path) -> None:
    fakes = _LexicalFakes(fail_on_question=_ids()[1])
    kwargs = _lexical_kwargs(tmp_path, fakes)
    with pytest.raises(RuntimeError, match="Synthetic lexical"):
        asyncio.run(run_measure(**kwargs))
    stopped = json.loads(Path(kwargs["output_path"]).read_text())
    assert stopped["completed_question_count"] == 1
    assert stopped["lexical_safety"]["verified"] is True
    fakes.fail_on_question = None
    fakes.corpus_rows[-1]["modelled"] = 1
    calls = fakes.calls
    with pytest.raises(ValueError, match="model-attributed"):
        asyncio.run(run_measure(**kwargs))
    assert fakes.calls == calls
    fakes.corpus_rows[-1]["modelled"] = 0
    document = asyncio.run(run_measure(**kwargs))
    assert document["completed_question_count"] == 100
    assert fakes.measured_ids.count(_ids()[0]) == 2  # No repeat of either completed arm.


@pytest.mark.parametrize("corruption", ["cross_mode", "untagged", "adapter", "embedding"])
def test_lexical_resume_rejects_mode_and_vector_contamination(
    tmp_path: Path, corruption: str
) -> None:
    fakes = _LexicalFakes(fail_on_question=_ids()[1])
    kwargs = _lexical_kwargs(tmp_path, fakes)
    with pytest.raises(RuntimeError):
        asyncio.run(run_measure(**kwargs))
    journal = m2_exposure._journal_path(kwargs["output_path"])
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    if corruption == "cross_mode":
        rows[0].update(m2_exposure._retrieval_identity("cached_embeddings"))
    elif corruption == "untagged":
        rows[0].pop("retrieval_mode")
        rows[0].pop("retrieval_adapter")
    elif corruption == "adapter":
        rows[0]["retrieval_adapter"] = "m2-lexical/foreign"
    else:
        rows.append({"kind": "embedding", "text_sha256": "synthetic-no-vector"})
    journal.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    calls = fakes.calls
    with pytest.raises(ValueError, match="journal"):
        asyncio.run(run_measure(**kwargs))
    assert fakes.calls == calls


def test_measure_cli_requires_explicit_lexical_mode() -> None:
    args = [
        "measure",
        "--questions",
        "q",
        "--subqueries",
        "s",
        "--labels",
        "l",
        "--start-marker",
        "m",
        "--remap-lineage",
        "r",
        "--output",
        "o",
    ]
    parser = m2_exposure.build_parser()
    assert parser.parse_args(args).retrieval_mode == "cached_embeddings"
    assert parser.parse_args([*args, "--retrieval-mode", "lexical"]).retrieval_mode == "lexical"


@pytest.mark.parametrize("mode", ["cached_embeddings", "untagged"])
def test_stopped_output_cannot_be_repurposed_as_lexical(tmp_path: Path, mode: str) -> None:
    fakes = _LexicalFakes(fail_on_question=_ids()[1])
    kwargs = _lexical_kwargs(tmp_path, fakes)
    with pytest.raises(RuntimeError):
        asyncio.run(run_measure(**kwargs))
    output = Path(kwargs["output_path"])
    data = json.loads(output.read_text())
    if mode == "untagged":
        data.pop("retrieval_mode")
        data.pop("retrieval_adapter")
    else:
        data.update(m2_exposure._retrieval_identity(mode))
    output.write_text(json.dumps(data))
    calls = fakes.calls
    with pytest.raises(ValueError, match="different retrieval mode"):
        asyncio.run(run_measure(**kwargs))
    assert fakes.calls == calls
    assert json.loads(output.read_text()) == data


def test_untagged_historical_embedding_manifest_stays_embedding_only() -> None:
    legacy = {"kind": "manifest", "embedding_route_id": "synthetic", "embedding_dimensions": 2}
    normalized = m2_exposure._legacy_embedding_manifest(legacy)
    assert normalized == {**legacy, **m2_exposure._retrieval_identity("cached_embeddings")}
    assert normalized != {**legacy, **m2_exposure._retrieval_identity("lexical")}


def _native_lexical_setup(monkeypatch: Any) -> tuple[Any, list[dict[str, Any]]]:
    from orchestrator.memory import embedding, injection, retrieval
    from datetime import timedelta

    settings = SimpleNamespace(
        embedding_route_id="",
        embedding_document_model="voyage-4-large",
        embedding_query_model="voyage-4-lite",
        embedding_fallback_providers="",
        retrieval_logging_enabled=False,
        retrieval_logging_debug=False,
    )
    monkeypatch.setattr(embedding, "get_settings", lambda: settings)
    monkeypatch.setattr(retrieval, "get_settings", lambda: settings)
    for module, name in (
        (retrieval, "embed_query_for_configured_storage_models"),
        (injection, "embed_query_with_metadata"),
        (injection, "embed_queries_with_metadata"),
        (m2_exposure, "selected_embedding_route"),
        (m2_exposure, "embedding_route_identity"),
        (m2_exposure, "embedding_ceiling_usd"),
        (m2_exposure, "_embed_texts_live"),
    ):
        monkeypatch.setattr(module, name, _forbidden)
    monkeypatch.setattr(injection.ProductionSubqueryProvider, "get_subqueries", _forbidden)
    monkeypatch.setattr("orchestrator.compute_runtime.account_compute", _forbidden)
    monkeypatch.setattr("orchestrator.compute_runtime.guarded_completion", _forbidden)
    calls: list[dict[str, Any]] = []

    def memory(index: int, **fields: Any) -> dict[str, Any]:
        return {
            "id": uuid.UUID(int=index),
            "content": f"Synthetic fact {index}",
            "category": "fact",
            "local_only": False,
            "status": "active",
            "bm25_score": 1.0,
            "confidence": 1.0,
            "trust_score": 0.5,
            "last_accessed_at": NOW - timedelta(days=1),
            "valid_from": NOW,
            "embedding_model": None,
            **fields,
        }

    class Store:
        async def get_l0_memories(self, user_id: Any, **kwargs: Any) -> list[dict[str, Any]]:
            assert kwargs["include_local"] is False
            return [memory(100, tier="l0"), memory(101, local_only=True)]

        async def get_recent_summaries(self, user_id: Any, **kwargs: Any) -> list[dict[str, Any]]:
            assert kwargs == {"limit": 3, "include_local": False}
            return [memory(200, content="Synthetic summary"), memory(201, local_only=True)]

        async def search_memories_bm25(self, **kwargs: Any) -> list[dict[str, Any]]:
            assert kwargs["match_any_term"] is True
            assert kwargs["include_local"] is False
            query = kwargs["query"]
            numbers = (
                range(1, 7)
                if query == "Question"
                else range(10, 14)
                if query == "one"
                else range(20, 24)
            )
            return [memory(index) for index in numbers]

        async def find_entities_by_alias(self, *args: Any) -> list[Any]:
            return []

        async def get_entity_by_lookup_key(self, *args: Any) -> None:
            return None

        search_memories = staticmethod(_forbidden)
        bulk_touch_memories = staticmethod(_forbidden)
        log_retrieval = staticmethod(_forbidden)

    native = retrieval.retrieve_memories_for_text

    async def spy(*args: Any, **kwargs: Any) -> list[dict[str, object]]:
        assert retrieval.RECENCY_REFERENCE_TIME.get() == NOW
        assert kwargs["query_embedding"] is None
        assert kwargs["allow_query_embedding"] is False
        assert kwargs["touch_results"] is False
        assert kwargs["log_retrieval"] is False
        assert kwargs["query_reference_time"] == NOW
        calls.append(kwargs)
        rows = await native(*args, **kwargs)
        assert all(row["final_score"] == pytest.approx(0.4) for row in rows)
        return rows

    monkeypatch.setattr(retrieval, "retrieve_memories_for_text", spy)
    return Store(), calls


@pytest.mark.parametrize("merge", ["additive", "round_robin"])
@pytest.mark.parametrize("triggered", [False, True])
def test_lexical_adapter_uses_production_scoring_merge_render_and_historical_clock(
    monkeypatch: Any,
    merge: str,
    triggered: bool,
) -> None:
    from orchestrator.memory import retrieval

    store, calls = _native_lexical_setup(monkeypatch)
    question = Question("synthetic", "Question", NOW, False, ())

    async def run() -> dict[str, Any]:
        prior = datetime(2001, 1, 1, tzinfo=UTC)
        token = retrieval.RECENCY_REFERENCE_TIME.set(prior)
        try:
            result = await m2_exposure.measure_question_lexical(
                store,
                question=question,
                subqueries=("one", "two") if triggered else (),
                merge=merge,
                target_ids=[str(uuid.UUID(int=10))],
            )
            assert retrieval.RECENCY_REFERENCE_TIME.get() == prior
            return result
        finally:
            retrieval.RECENCY_REFERENCE_TIME.reset(token)

    result = asyncio.run(run())
    assert [c["limit"] for c in calls if c["query_role"] is None] == [5]
    assert [c["limit"] for c in calls if c["query_role"] is not None] == (
        [10, 10] if triggered else []
    )
    assert result["baseline"]["retrieved_ids"] == [str(uuid.UUID(int=i)) for i in range(1, 6)]
    assert str(uuid.UUID(int=100)) in result["baseline"]["rendered_ids"]
    assert str(uuid.UUID(int=101)) not in result["baseline"]["rendered_ids"]
    if triggered:
        assert len(result["candidate"]["retrieved_ids"]) == 11
        assert result["candidate"]["target_exposed"] is True
        if merge == "additive":
            assert result["candidate"]["retrieved_ids"][:5] == result["baseline"]["retrieved_ids"]
    else:
        assert result["baseline"] == result["candidate"]


@pytest.mark.parametrize("added_kind", ["existing_uuid", "non_uuid"])
def test_lexical_replacement_requires_a_new_uuid_even_for_round_robin(
    monkeypatch: Any,
    added_kind: str,
) -> None:
    from orchestrator.memory import retrieval

    store, _ = _native_lexical_setup(monkeypatch)
    native = retrieval.retrieve_memories_for_text

    async def retrieve(**kwargs: Any) -> list[dict[str, object]]:
        if kwargs["query_role"] is None:
            return await native(**kwargs)
        if added_kind == "non_uuid":
            return [{"id": "not-a-uuid", "content": "Synthetic alternative"}]
        return [
            {"id": uuid.UUID(int=i), "content": "Synthetic duplicate"}
            for i in reversed(range(1, 6))
        ]

    monkeypatch.setattr(retrieval, "retrieve_memories_for_text", retrieve)
    result = asyncio.run(
        m2_exposure.measure_question_lexical(
            store,
            question=Question("synthetic", "Question", NOW, False, ()),
            subqueries=("one",),
            merge="round_robin",
            target_ids=(),
        )
    )
    assert result["candidate"] == result["baseline"]


@pytest.mark.parametrize("stop", ["failure", "timeout", "cancel"])
def test_lexical_component_fallback_drains_tasks_and_resets_clock(
    monkeypatch: Any, stop: str
) -> None:
    from orchestrator.memory import injection, retrieval

    store, _ = _native_lexical_setup(monkeypatch)

    async def run() -> None:
        started = asyncio.Event()
        drained = asyncio.Event()
        blocker = asyncio.Event()
        prior = retrieval.RECENCY_REFERENCE_TIME.get()

        async def retrieve(**kwargs: Any) -> list[dict[str, object]]:
            assert retrieval.RECENCY_REFERENCE_TIME.get() == NOW
            if kwargs["query_role"] is None:
                return []
            if kwargs["query_role"] == "sub:0":
                try:
                    started.set()
                    await blocker.wait()
                finally:
                    drained.set()
            await started.wait()
            if stop == "failure":
                raise RuntimeError("Synthetic component error")
            await blocker.wait()
            return []

        monkeypatch.setattr(retrieval, "retrieve_memories_for_text", retrieve)
        if stop == "timeout":

            async def timeout(task: Any, deadline: float) -> Any:
                await started.wait()
                raise TimeoutError

            monkeypatch.setattr(injection, "_before_component_deadline", timeout)
        task = asyncio.create_task(
            m2_exposure.measure_question_lexical(
                store,
                question=Question("synthetic", "Question", NOW, False, ()),
                subqueries=("one", "two"),
                merge="additive",
                target_ids=(),
            )
        )
        await started.wait()
        if stop == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await task
            assert result["baseline"] == result["candidate"]
        assert drained.is_set()
        assert retrieval.RECENCY_REFERENCE_TIME.get() == prior

    asyncio.run(run())


def test_live_lexical_wiring_reads_only_aggregate_safety_and_native_retrieval(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from orchestrator.eval import bench_db
    from orchestrator.memory import store as store_module
    from tests.benchmark_longmemeval.dev2_pilot import RUN_LOCK_SQL, RUN_LOCK_HELD_SQL

    native_store, native_calls = _native_lexical_setup(monkeypatch)
    fakes = _LexicalFakes()
    kwargs = _lexical_kwargs(tmp_path, fakes)
    closed: list[str] = []
    sqls: list[str] = []

    class Pool:
        async def close(self) -> None:
            closed.append("pool")

    class Connection:
        async def close(self) -> None:
            closed.append("connection")

        async def fetch(self, sql: str, users: Any) -> list[dict[str, Any]]:
            sqls.append(sql)
            if sql == m2_exposure.LEXICAL_CORPUS_SQL:
                assert len(users) == 100
                return fakes.corpus_rows
            assert sql == "SELECT id FROM users WHERE id = ANY($1::uuid[])"
            assert len(users) == 101
            return [{"id": user} for user in users]

        async def fetchrow(self, sql: str) -> Mapping[str, Any]:
            assert sql == m2_exposure.LEXICAL_LEDGER_SQL
            return {**fakes.ledger_state, "settled_microusd": Decimal(306398)}

        async def fetchval(self, sql: str, *args: Any) -> Any:
            if sql == RUN_LOCK_SQL:
                return True
            if sql == RUN_LOCK_HELD_SQL:
                return 1
            assert sql == "SELECT count(*) FROM retrieval_log"
            return 0

    async def pool(*args: Any, **kw: Any) -> Pool:
        return Pool()

    async def connect(*args: Any) -> Connection:
        return Connection()

    async def fingerprint(*args: Any) -> dict[str, Any]:
        return await fakes.fingerprint()

    async def assert_database(*args: Any) -> None:
        return None

    monkeypatch.setattr("asyncpg.create_pool", pool)
    monkeypatch.setattr("asyncpg.connect", connect)
    monkeypatch.setattr(bench_db, "assert_benchmark_database", assert_database)
    monkeypatch.setattr(bench_db, "fingerprint_benchmark_users", fingerprint)
    monkeypatch.setattr(store_module, "MemoryStore", lambda *args: native_store)
    monkeypatch.setattr("orchestrator.memory.encryption.ContentEncryption", lambda *args: object())
    monkeypatch.setattr(
        "orchestrator.config.get_settings",
        lambda: SimpleNamespace(
            database_url=f"postgresql://synthetic@invalid/{REMAP_DB_NAME}",
            daemon_encryption_key="synthetic",
            memory_subquery_quota=3,
            memory_max_subqueries=3,
            retrieval_logging_enabled=False,
            retrieval_logging_debug=False,
        ),
    )
    monkeypatch.setattr(
        m2_exposure,
        "load_remap_lineage",
        lambda _: m2_exposure.RemapLineage("synthetic", LINEAGE_SHA, REMAP_DB_NAME, "a"),
    )
    document = asyncio.run(
        m2_exposure._run_live_measure(
            questions=kwargs["questions"],
            labels=kwargs["labels"],
            subqueries=kwargs["subqueries"],
            output_path=kwargs["output_path"],
            start_marker=kwargs["start_marker"],
            remap_lineage=tmp_path / "unused-lineage",
            retrieval_mode="lexical",
        )
    )
    assert document["stop_reason"] is None
    assert document["lexical_safety"]["verified"] is True
    assert len(native_calls) >= 200
    assert sqls.count(m2_exposure.LEXICAL_CORPUS_SQL) == 2
    assert closed == ["connection", "pool"]


def test_lexical_mode_and_adapter_propagate_through_gates_and_sanitized_report(
    tmp_path: Path,
) -> None:
    threshold, measure, plausibility, labels = _gate_documents(_frozen(), newly=6)
    measure.update(m2_exposure._retrieval_identity("lexical"))
    measure["lexical_safety"] = {"verified": True}
    gates = run_gates(
        threshold=threshold,
        labels=labels,
        measure=measure,
        plausibility=plausibility,
        output_path=tmp_path / "gates.json",
        repo_root=tmp_path / "repo",
    )
    report = run_report(
        measure=measure,
        gates=gates,
        retention_categories=_categories(labels),
        output_path=tmp_path / "report.json",
        repo_root=tmp_path / "repo",
    )
    for document in (gates, report):
        assert document["retrieval_mode"] == "lexical"
        assert document["retrieval_adapter"] == "m2-lexical/1"
    gates.update(m2_exposure._retrieval_identity("cached_embeddings"))
    with pytest.raises(ValueError, match="different retrieval modes"):
        run_report(
            measure=measure,
            gates=gates,
            retention_categories=_categories(labels),
            output_path=tmp_path / "bad-report.json",
            repo_root=tmp_path / "repo",
        )
    measure["lexical_safety"]["verified"] = False
    with pytest.raises(ValueError, match="verified side-effect"):
        run_gates(
            threshold=threshold,
            labels=labels,
            measure=measure,
            plausibility=plausibility,
            output_path=tmp_path / "bad-gates.json",
            repo_root=tmp_path / "repo",
        )


def test_completed_lexical_resume_rechecks_admission_even_without_retrieval(tmp_path: Path) -> None:
    fakes = _LexicalFakes()
    kwargs = _lexical_kwargs(tmp_path, fakes)
    asyncio.run(run_measure(**kwargs))
    # Simulate loss of only the output document after the journal was fully durable.
    Path(kwargs["output_path"]).unlink()
    fakes.corpus_rows[-1]["embedded"] = 1
    with pytest.raises(ValueError, match="embedded"):
        asyncio.run(run_measure(**kwargs))
    assert fakes.calls == 200


def test_lexical_unreadable_postcheck_cannot_succeed(tmp_path: Path) -> None:
    fakes = _LexicalFakes(fail_on_question=_ids()[0])
    kwargs = _lexical_kwargs(tmp_path, fakes)

    async def read() -> Mapping[str, Any]:
        if fakes.calls:
            raise RuntimeError("Synthetic ledger read failure")
        return fakes.ledger_state

    kwargs["read_ledger_state"] = read
    with pytest.raises(m2_exposure.M2SideEffectError, match="safety observations"):
        asyncio.run(run_measure(**kwargs))
    result = json.loads(Path(kwargs["output_path"]).read_text())
    assert result["lexical_safety"]["verified"] is False
    assert result["lexical_safety"]["after"]["errors"] == ["ledger"]
    assert result["stop_reason"] is not None


def test_failed_fingerprint_postcheck_does_not_skip_ledger_or_log_observation(
    tmp_path: Path,
) -> None:
    fakes = _LexicalFakes(fail_on_question=_ids()[0])
    kwargs = _lexical_kwargs(tmp_path, fakes)
    ledger_reads: list[bool] = []

    async def fingerprint() -> dict[str, Any]:
        if fakes.calls:
            raise RuntimeError("Synthetic fingerprint read failure")
        return await fakes.fingerprint()

    async def ledger() -> Mapping[str, Any]:
        ledger_reads.append(True)
        return fakes.ledger_state

    kwargs.update(fingerprint=fingerprint, read_ledger_state=ledger)
    with pytest.raises(m2_exposure.M2SideEffectError):
        asyncio.run(run_measure(**kwargs))
    result = json.loads(Path(kwargs["output_path"]).read_text())
    after = result["lexical_safety"]["after"]
    assert after["ledger"] == fakes.ledger_state
    assert after["retrieval_log_count"] == fakes.log_count
    assert "fingerprint" in after["errors"]
    assert len(ledger_reads) == 3  # admission, pre-question, post-failure


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

    kwargs = _measure_kwargs(fakes, tmp_path)
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
    partial = json.loads(output.read_text(encoding="utf-8"))
    assert partial["stop_reason"] == "side_effect"
    assert partial["stopped_by"] == "M2SideEffectError"


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

    kwargs = _measure_kwargs(fakes, tmp_path)
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
    partial = json.loads(output.read_text(encoding="utf-8"))
    assert partial["stop_reason"] == "side_effect"


def test_measure_journals_completed_work_and_resumes_without_redispatch(
    tmp_path: Path,
) -> None:
    frozen = _frozen(2)
    questions = _questions(frozen)
    labels = _labels(frozen, [frozen.ids[0], frozen.ids[1]], tmp_path / "labels.json")
    subqueries = _pinned(frozen, questions, tmp_path / "subqueries.json")
    output = tmp_path / "out" / "measure.json"
    output.parent.mkdir()
    fakes = _MeasureFakes(fail_on_question=frozen.ids[1])

    with pytest.raises(RuntimeError, match="forced mid-run failure"):
        asyncio.run(
            run_measure(
                questions=questions,
                labels=labels,
                subqueries=subqueries,
                output_path=output,
                **_measure_kwargs(fakes, tmp_path),
            )
        )
    partial = json.loads(output.read_text(encoding="utf-8"))
    assert partial["stop_reason"] == "error"
    assert partial["stopped_by"] == "RuntimeError"
    assert [row["question_id"] for row in partial["questions"]] == [frozen.ids[0]]
    first_batches = [list(batch) for batch in fakes.embed_calls]
    assert len(first_batches) == 3
    assert set(fakes.measured_ids) == {frozen.ids[0]}

    fakes.fail_on_question = None
    resumed = asyncio.run(
        run_measure(
            questions=questions,
            labels=labels,
            subqueries=subqueries,
            output_path=output,
            **_measure_kwargs(fakes, tmp_path),
        )
    )
    assert resumed["stop_reason"] is None
    assert [row["question_id"] for row in resumed["questions"]] == [
        frozen.ids[0],
        frozen.ids[1],
    ]
    later_batches = [list(batch) for batch in fakes.embed_calls[len(first_batches) :]]
    first_texts = {text for batch in first_batches for text in batch}
    later_texts = {text for batch in later_batches for text in batch}
    assert not first_texts & later_texts
    assert fakes.measured_ids.count(frozen.ids[0]) == 2
    assert fakes.measured_ids.count(frozen.ids[1]) == 2
    # The resume re-dispatched nothing: no batch, no charge.
    assert later_batches == []
    assert resumed["spend_high_water_usd"] == partial["spend_high_water_usd"]
    records = [
        json.loads(line)
        for line in m2_exposure._journal_path(output).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    keys = [record["text_sha256"] for record in records if record.get("kind") == "embedding"]
    assert len(keys) == len(set(keys))


def test_measure_resume_refuses_a_mismatched_manifest(tmp_path: Path) -> None:
    frozen = _frozen(2)
    questions = _questions(frozen)
    labels = _labels(frozen, [frozen.ids[0], frozen.ids[1]], tmp_path / "labels.json")
    subqueries = _pinned(frozen, questions, tmp_path / "subqueries.json")
    output = tmp_path / "out" / "measure.json"
    output.parent.mkdir()
    fakes = _MeasureFakes(fail_on_question=frozen.ids[1])
    with pytest.raises(RuntimeError):
        asyncio.run(
            run_measure(
                questions=questions,
                labels=labels,
                subqueries=subqueries,
                output_path=output,
                **_measure_kwargs(fakes, tmp_path),
            )
        )
    journal = m2_exposure._journal_path(output)
    manifest = json.loads(journal.read_text(encoding="utf-8").splitlines()[0])
    manifest["labels_sha256"] = "other"
    journal.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    fakes.fail_on_question = None
    with pytest.raises(ValueError, match="does not match"):
        asyncio.run(
            run_measure(
                questions=questions,
                labels=labels,
                subqueries=subqueries,
                output_path=output,
                **_measure_kwargs(fakes, tmp_path),
            )
        )


def test_measure_refuses_an_absent_mismatched_or_corrupt_start_marker(tmp_path: Path) -> None:
    frozen = _frozen(1)
    questions = _questions(frozen)
    labels = _labels(frozen, [frozen.ids[0]], tmp_path / "labels.json")
    subqueries = _pinned(frozen, questions, tmp_path / "subqueries.json")
    output = tmp_path / "out" / "measure.json"
    output.parent.mkdir()
    marker = tmp_path / "start-marker.json"
    fakes = _MeasureFakes()

    def run() -> None:
        asyncio.run(
            run_measure(
                questions=questions,
                labels=labels,
                subqueries=subqueries,
                output_path=output,
                **_measure_kwargs(fakes, tmp_path),
            )
        )

    marker.unlink()
    with pytest.raises(ValueError, match="missing"):
        run()
    m2_exposure._create_start_marker(marker, Decimal("0.10"))
    with pytest.raises(ValueError, match="disagrees with the generation document"):
        run()
    marker.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        run()
    assert fakes.embed_calls == []
    assert fakes.calls == 0
    assert not m2_exposure._journal_path(output).exists()


@pytest.mark.parametrize(
    ("route_id", "dimensions"),
    [("other-embedding-route", 2), ("fake-embedding-route", 3)],
)
def test_measure_resume_refuses_a_changed_embedding_route(
    tmp_path: Path, route_id: str, dimensions: int
) -> None:
    frozen = _frozen(2)
    questions = _questions(frozen)
    labels = _labels(frozen, [frozen.ids[0], frozen.ids[1]], tmp_path / "labels.json")
    subqueries = _pinned(frozen, questions, tmp_path / "subqueries.json")
    output = tmp_path / "out" / "measure.json"
    output.parent.mkdir()
    fakes = _MeasureFakes(fail_on_question=frozen.ids[1])
    with pytest.raises(RuntimeError, match="forced mid-run failure"):
        asyncio.run(
            run_measure(
                questions=questions,
                labels=labels,
                subqueries=subqueries,
                output_path=output,
                **_measure_kwargs(fakes, tmp_path),
            )
        )
    manifest = json.loads(
        m2_exposure._journal_path(output).read_text(encoding="utf-8").splitlines()[0]
    )
    assert manifest["embedding_route_id"] == "fake-embedding-route"
    assert manifest["embedding_dimensions"] == 2

    fakes.fail_on_question = None
    kwargs = _measure_kwargs(fakes, tmp_path)
    kwargs["read_embedding_route"] = lambda: (route_id, dimensions)
    with pytest.raises(ValueError, match="does not match"):
        asyncio.run(
            run_measure(
                questions=questions,
                labels=labels,
                subqueries=subqueries,
                output_path=output,
                **kwargs,
            )
        )


@pytest.mark.parametrize("flags", [(True, False), (False, True)])
def test_measure_refuses_retrieval_logging_before_any_spend(
    tmp_path: Path, flags: tuple[bool, bool]
) -> None:
    frozen = _frozen(1)
    questions = _questions(frozen)
    labels = _labels(frozen, [frozen.ids[0]], tmp_path / "labels.json")
    subqueries = _pinned(frozen, questions, tmp_path / "subqueries.json")
    output = tmp_path / "out" / "measure.json"
    output.parent.mkdir()
    fakes = _MeasureFakes()
    kwargs = _measure_kwargs(fakes, tmp_path)
    kwargs["read_retrieval_logging_flags"] = lambda: flags
    with pytest.raises(m2_exposure.M2SideEffectError, match="retrieval_logging"):
        asyncio.run(
            run_measure(
                questions=questions,
                labels=labels,
                subqueries=subqueries,
                output_path=output,
                **kwargs,
            )
        )
    assert fakes.embed_calls == []
    assert not m2_exposure._journal_path(output).exists()


def test_measure_refuses_before_spend_when_required_users_are_missing(
    tmp_path: Path,
) -> None:
    frozen = _frozen(1)
    questions = _questions(frozen)
    labels = _labels(frozen, [frozen.ids[0]], tmp_path / "labels.json")
    subqueries = _pinned(frozen, questions, tmp_path / "subqueries.json")
    output = tmp_path / "out" / "measure.json"
    output.parent.mkdir()
    fakes = _MeasureFakes()
    seen: list[str] = []

    async def users() -> None:
        seen.append("checked")
        raise m2_exposure.M2SideEffectError("M2 measure requires existing benchmark users")

    kwargs = _measure_kwargs(fakes, tmp_path)
    kwargs["check_users_present"] = users
    with pytest.raises(m2_exposure.M2SideEffectError, match="benchmark users"):
        asyncio.run(
            run_measure(
                questions=questions,
                labels=labels,
                subqueries=subqueries,
                output_path=output,
                **kwargs,
            )
        )
    assert seen == ["checked"]
    assert fakes.embed_calls == []
    assert fakes.calls == 0


def test_measure_declares_no_user_writes(tmp_path: Path) -> None:
    frozen = _frozen(2)
    questions = _questions(frozen)
    labels = _labels(frozen, [frozen.ids[0], frozen.ids[1]], tmp_path / "labels.json")
    subqueries = _pinned(frozen, questions, tmp_path / "subqueries.json")
    output = tmp_path / "out" / "measure.json"
    output.parent.mkdir()
    document = asyncio.run(
        run_measure(
            questions=questions,
            labels=labels,
            subqueries=subqueries,
            output_path=output,
            **_measure_kwargs(_MeasureFakes(), tmp_path),
        )
    )
    effects = document["side_effects"]
    assert effects["users_created"] is False
    assert effects["entitlement_accounts_created"] is False


def test_wrong_database_is_refused() -> None:
    assert (
        m2_exposure.check_m2_database(f"postgresql://user:secret@host/{REMAP_DB_NAME}")
        == REMAP_DB_NAME
    )
    # The original stays as evidence: it must never be the M2 target.
    for name in ("daemon_lme_bench_dev2_final", "daemon_lme_bench_dev2", "production"):
        with pytest.raises(ValueError, match="exactly"):
            m2_exposure.check_m2_database(f"postgresql://user:secret@host/{name}")


def _lineage_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": "dev2-remap-lineage/1",
        "result": "ok",
        "source": {"database": "daemon_lme_bench_dev2_final", "fingerprint_sha256": "a" * 64},
        "remapped": {"database": REMAP_DB_NAME, "fingerprint_sha256": "b" * 64},
        "dataset": {"path": "/tmp/dataset.json", "sha256": "d" * 64},
        "mapping_script": {"path": "/tmp/dev2_remap.py", "sha256": "e" * 64},
    }
    document.update(overrides)
    return document


def test_remap_lineage_parses_and_binds(tmp_path: Path) -> None:
    path = tmp_path / "lineage.json"
    path.write_text(json.dumps(_lineage_document()), encoding="utf-8")
    lineage = m2_exposure.load_remap_lineage(path)
    assert lineage.database == REMAP_DB_NAME
    assert lineage.remapped_fingerprint_sha256 == "b" * 64
    assert len(lineage.sha256) == 64
    m2_exposure.check_remap_lineage(lineage, {"sha256": "b" * 64})
    with pytest.raises(ValueError, match="no longer matches"):
        m2_exposure.check_remap_lineage(lineage, {"sha256": "f" * 64})


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"schema": "something-else"}, "schema"),
        ({"result": "failed"}, "did not verify"),
        (
            {
                "remapped": {
                    "database": "daemon_lme_bench_dev2_final",
                    "fingerprint_sha256": "b" * 64,
                }
            },
            "different database",
        ),
        ({"remapped": {"database": REMAP_DB_NAME}}, "no remapped fingerprint"),
    ],
)
def test_remap_lineage_refuses_bad_documents(
    tmp_path: Path, overrides: dict[str, Any], message: str
) -> None:
    path = tmp_path / "lineage.json"
    path.write_text(json.dumps(_lineage_document(**overrides)), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        m2_exposure.load_remap_lineage(path)


def test_remap_lineage_refuses_non_json(tmp_path: Path) -> None:
    path = tmp_path / "lineage.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not JSON"):
        m2_exposure.load_remap_lineage(path)


def test_a_different_lineage_refuses_a_measure_resume(tmp_path: Path) -> None:
    frozen = _frozen(2)
    questions = _questions(frozen)
    labels = _labels(frozen, [frozen.ids[0], frozen.ids[1]], tmp_path / "labels.json")
    subqueries = _pinned(frozen, questions, tmp_path / "subqueries.json")
    output = tmp_path / "out" / "measure.json"
    output.parent.mkdir()
    fakes = _MeasureFakes()
    first = asyncio.run(
        run_measure(
            questions=questions,
            labels=labels,
            subqueries=subqueries,
            output_path=output,
            **_measure_kwargs(fakes, tmp_path),
        )
    )
    assert first["corpus"]["kind"] == "historical_clock_remap"
    assert first["corpus"]["database"] == REMAP_DB_NAME
    assert first["corpus"]["remap_lineage_sha256"] == LINEAGE_SHA

    output.unlink()
    other = _measure_kwargs(fakes, tmp_path)
    other["remap_lineage_sha256"] = "9" * 64
    with pytest.raises(ValueError, match="does not match this run"):
        asyncio.run(
            run_measure(
                questions=questions,
                labels=labels,
                subqueries=subqueries,
                output_path=output,
                **other,
            )
        )


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
    abs_answerable: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], LabelSet]:
    answerable_ids = list(_ids()[:51])
    if abs_answerable:
        answerable_ids.append(next(qid for qid in frozen.ids if qid.endswith("_abs")))
    labels = LabelSet(
        {
            qid: LabelEntry(
                qid,
                (("m-1",), ("m-2",)) if qid in set(answerable_ids) else (),
                qid in set(answerable_ids),
            )
            for qid in frozen.ids
        },
        (),
        "labels-hash",
        (m2_exposure.LABEL_SCHEMA,),
    )
    exposure_count = len([qid for qid in labels.answerable_ids if not qid.endswith("_abs")])
    threshold = {
        "schema": m2_exposure.THRESHOLD_SCHEMA,
        "labels_sha256": labels.sha256,
        "answerable_count": exposure_count,
        "gate1_threshold": gate1_threshold(exposure_count),
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
    abs_answerable: bool = False,
) -> dict[str, Any]:
    threshold, measure, plausibility, labels = _gate_documents(
        frozen,
        newly=newly,
        lost=lost,
        partial=partial,
        plausible=plausible,
        abs_answerable=abs_answerable,
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


def test_abs_answerable_question_does_not_change_gates_1_and_2(tmp_path: Path) -> None:
    frozen = _frozen()
    abs_id = next(qid for qid in frozen.ids if qid.endswith("_abs"))
    base = _run_gates(tmp_path, frozen, newly=6, plausible=1)
    threshold, measure, plausibility, labels = _gate_documents(
        frozen, newly=6, plausible=1, abs_answerable=True
    )
    row = next(value for value in measure["questions"] if value["question_id"] == abs_id)
    row["baseline"], row["candidate"] = _gate_arms(("m-1", "m-2"), [], ["m-1", "m-2"])
    output = tmp_path / "out" / "gates.json"
    output.unlink(missing_ok=True)
    with_abs = run_gates(
        threshold=threshold,
        labels=labels,
        measure=measure,
        plausibility=plausibility,
        output_path=output,
        repo_root=tmp_path / "repo",
    )
    assert labels.answerable_ids and abs_id in labels.answerable_ids
    assert with_abs["gate1"]["answerable_count"] == 51
    # The exposed _abs question would make 7 if it entered the denominator.
    assert with_abs["gate1"]["newly_exposed_count"] == 6
    assert with_abs["gate1"] == base["gate1"]
    assert with_abs["gate2"] == base["gate2"]
    assert with_abs["result"] == base["result"]
    # Gate 4 keeps reading exactly the same abstention questions as before.
    assert [item["question_id"] for item in with_abs["gate4"]["rows"]] == [
        item["question_id"] for item in base["gate4"]["rows"]
    ]
    assert abs_id in [item["question_id"] for item in with_abs["gate4"]["rows"]]


def test_strict_metric_does_not_count_partial_exposure(tmp_path: Path) -> None:
    document = _run_gates(tmp_path, _frozen(), newly=5, partial=True)
    assert document["gate1"]["newly_exposed_count"] == 5
    assert document["gate1"]["passed"] is False


def test_group_aggregation_accepts_an_alternative_but_requires_every_group() -> None:
    groups = (("m1", "paraphrase"), ("date1", "same-session-date"))
    flat = [mid for group in groups for mid in group]
    metrics = {"candidate": _arm(flat, ["paraphrase", "same-session-date"])}
    # The unchanged production flat-ID API quite correctly says not all IDs render.
    assert metrics["candidate"]["target_exposed"] is False
    arm = m2_exposure._arm_view(metrics, "candidate", groups)
    assert arm["target_exposed"] is True
    assert arm["exposed_group_indices"] == [0, 1]
    assert arm["target_group_count"] == 2
    metrics["candidate"] = _arm(flat, ["paraphrase"])
    arm = m2_exposure._arm_view(metrics, "candidate", groups)
    assert arm["target_exposed"] is False
    assert arm["target_any_exposed"] is True
    assert arm["exposed_group_indices"] == [0]


def test_measure_driver_flattens_only_at_api_boundary_and_records_groups(tmp_path: Path) -> None:
    frozen = _frozen(2)
    questions = _questions(frozen)
    labels = _labels(frozen, frozen.ids, tmp_path / "labels.json")
    for qid in frozen.ids:
        labels.entries[qid] = LabelEntry(qid, (("m1", "m2"), ("date1", "date2")), True, True)
    subqueries = _pinned(frozen, questions, tmp_path / "subqueries.json")
    kwargs = _measure_kwargs(_MeasureFakes(), tmp_path)

    async def measure(
        _question: Question,
        _components: tuple[str, ...],
        _vectors: Mapping[str, EmbeddingVectorResult],
        _merge: str,
        targets: Sequence[str],
    ) -> dict[str, Any]:
        assert list(targets) == ["date1", "date2", "m1", "m2"]
        baseline = _arm(targets, ["m1"])
        candidate = _arm(targets, ["m2", "date2"])
        assert candidate["target_exposed"] is False  # flat-ID API intentionally unchanged
        return {"baseline": baseline, "candidate": candidate}

    kwargs["measure_question"] = measure
    document = asyncio.run(
        run_measure(
            questions=questions,
            labels=labels,
            subqueries=subqueries,
            output_path=tmp_path / "measure.json",
            repo_root=tmp_path / "repo",
            **kwargs,
        )
    )
    for row in document["questions"]:
        assert row["target_groups"] == [["m1", "m2"], ["date1", "date2"]]
        assert row["needs_memory_date"] is True
        assert row["baseline"]["target_exposed"] is False
        assert row["candidate"]["target_exposed"] is True
        assert row["candidate"]["exposed_group_indices"] == [0, 1]


def test_gate2_preserves_group_when_a_redundant_alternative_disappears(tmp_path: Path) -> None:
    frozen = _frozen()
    threshold, measure, plausibility, labels = _gate_documents(frozen, newly=6)
    qid = _ids()[0]
    labels.entries[qid] = LabelEntry(qid, (("m-1", "alternate"), ("m-2",)), True)
    row = next(row for row in measure["questions"] if row["question_id"] == qid)
    row["baseline"], row["candidate"] = _gate_arms(
        ("m-1", "alternate", "m-2"),
        ["m-1", "alternate"],
        ["alternate", "m-2"],
    )
    output = tmp_path / "gates.json"
    document = run_gates(
        threshold=threshold,
        labels=labels,
        measure=measure,
        plausibility=plausibility,
        output_path=output,
        repo_root=tmp_path / "repo",
    )
    assert document["gate2"]["passed"] is True
    assert document["gate1"]["newly_exposed_count"] == 6
    # Losing all alternatives of even a partially exposed group must still fail.
    row["candidate"] = _arm(("m-1", "alternate", "m-2"), ["m-2"])
    document = run_gates(
        threshold=threshold,
        labels=labels,
        measure=measure,
        plausibility=plausibility,
        output_path=tmp_path / "lost.json",
        repo_root=tmp_path / "repo",
    )
    assert document["gate2"]["passed"] is False
    assert document["gate2"]["losses"] == {qid: [0]}


@pytest.mark.parametrize(
    "update",
    [
        {"target_groups": [[]]},
        {"target_groups": [["m", "m"]]},
        {"target_groups": [[False]]},
        {"target_groups": []},
        {"needs_memory_date": "false"},
        {"needs_memory_date": 0},
        {"memory_ids": ["m"]},
    ],
)
def test_m2_loader_rejects_invalid_v3_contract(tmp_path: Path, update: dict[str, Any]) -> None:
    frozen = _frozen(2)
    path = _write_labels(tmp_path / "labels.json", frozen, answerable=frozen.ids)
    data = json.loads(path.read_text())
    data["questions"][0].update(update)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_labels([path], frozen)


def test_v3_labels_hash_groups_and_date_flag_without_losing_alternatives(tmp_path: Path) -> None:
    frozen = _frozen(2)
    path = _write_labels(tmp_path / "labels.json", frozen, answerable=frozen.ids)
    data = json.loads(path.read_text())
    data["questions"][0]["target_groups"] = [["m1", "alternate"], ["date"]]
    path.write_text(json.dumps(data))
    first = load_labels([path], frozen)
    assert first.entries[frozen.ids[0]].memory_ids == ("alternate", "date", "m1")
    data["questions"][0]["target_groups"] = [["alternate", "m1"], ["date"]]
    path.write_text(json.dumps(data))
    assert first.sha256 == load_labels([path], frozen).sha256
    data["questions"][0]["needs_memory_date"] = True
    path.write_text(json.dumps(data))
    assert first.sha256 != load_labels([path], frozen).sha256
    data["questions"][0]["needs_memory_date"] = False
    data["questions"][0]["target_groups"] = [["alternate"], ["m1", "date"]]
    path.write_text(json.dumps(data))
    assert first.sha256 != load_labels([path], frozen).sha256


@pytest.mark.parametrize("schema", ["dev2-labelling/1", "dev2-labelling/2"])
def test_final_measure_and_gates_refuse_historical_labels_before_effects(
    tmp_path: Path, schema: str
) -> None:
    frozen = _frozen(2)
    path = _write_labels(tmp_path / "labels.json", frozen, answerable=frozen.ids, schema=schema)
    labels = load_labels([path], frozen)
    fakes = _MeasureFakes()
    kwargs = _measure_kwargs(fakes, tmp_path)
    kwargs["labels"] = labels
    kwargs.update(
        questions=_questions(frozen), subqueries={}, output_path=tmp_path / "measure.json"
    )
    with pytest.raises(ValueError, match="dev2-labelling/3"):
        asyncio.run(run_measure(**kwargs))
    assert fakes.embed_calls == [] and fakes.calls == 0
    with pytest.raises(ValueError, match="dev2-labelling/3"):
        run_gates(
            threshold={},
            labels=labels,
            measure={},
            plausibility={},
            output_path=tmp_path / "gates.json",
            repo_root=tmp_path / "repo",
        )


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
    assert one["gate4"]["alternative_reading"]["count"] == 1
    assert one["gate4"]["alternative_reading"]["affects_result"] is False

    two = _run_gates(tmp_path, _frozen(), newly=6, plausible=2)
    assert two["gate4"]["plausible_candidate_only_count"] == 2
    assert two["gate4"]["flagged_for_owner_review"] is True
    assert two["result"] == "flag_for_owner_review"
    assert two["gate4"]["alternative_reading"]["count"] == 2


def test_gate4_alternative_reading_counts_questions_not_items(tmp_path: Path) -> None:
    frozen = _frozen()
    threshold, measure, plausibility, labels = _gate_documents(frozen, newly=6)
    question_id = next(value for value in frozen.ids if value.endswith("_abs"))
    row = next(value for value in measure["questions"] if value["question_id"] == question_id)
    row["candidate_only_rendered_ids"] = ["false-premise-a", "false-premise-b"]
    plausibility["questions"][question_id] = {
        "plausible_ids": ["false-premise-a", "false-premise-b"]
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
    assert document["gate4"]["plausible_candidate_only_count"] == 2
    assert document["gate4"]["alternative_reading"]["count"] == 1
    assert document["gate4"]["flagged_for_owner_review"] is True
    assert document["result"] == "flag_for_owner_review"


def test_gates_refuse_a_stopped_measure_document(tmp_path: Path) -> None:
    frozen = _frozen()
    threshold, measure, plausibility, labels = _gate_documents(frozen, newly=6)
    measure["stop_reason"] = "error"
    output = tmp_path / "out" / "gates.json"
    output.parent.mkdir()
    with pytest.raises(ValueError, match="stopped"):
        run_gates(
            threshold=threshold,
            labels=labels,
            measure=measure,
            plausibility=plausibility,
            output_path=output,
            repo_root=tmp_path / "repo",
        )


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
    bad_labels = LabelSet(labels.entries, (), "other-hash", labels.schemas)
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


def _categories(
    labels: LabelSet, *, category: str = "other", reason: str | None = None
) -> dict[str, Any]:
    return {
        question_id: {"category": category, "reason": reason}
        for question_id in labels.not_answerable_ids
    }


def _report_inputs(
    tmp_path: Path, *, newly: int = 6, plausible: int = 2
) -> tuple[dict[str, Any], dict[str, Any], LabelSet]:
    frozen = _frozen()
    threshold, measure, plausibility, labels = _gate_documents(
        frozen, newly=newly, plausible=plausible
    )
    gates = run_gates(
        threshold=threshold,
        labels=labels,
        measure=measure,
        plausibility=plausibility,
        output_path=tmp_path / "out" / "gates.json",
        repo_root=tmp_path / "repo",
    )
    return measure, gates, labels


def test_report_is_sanitized_and_ignores_benchmark_text(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    measure, gates, labels = _report_inputs(tmp_path)
    for row in measure["questions"]:
        row["question"] = "PLANTED BENCHMARK TEXT"
        row["note"] = "PLANTED NOTE"
        row["baseline"]["rendered_text"] = "PLANTED RENDERED TEXT"
    measure["planted"] = "PLANTED DOCUMENT TEXT"
    output = tmp_path / "out" / "report.json"
    document = run_report(
        measure=measure,
        gates=gates,
        retention_categories=_categories(labels),
        output_path=output,
        repo_root=tmp_path / "repo",
    )
    serialized = output.read_text(encoding="utf-8")
    assert "PLANTED" not in serialized
    assert document["question_count"] == 100
    assert set(document["category_counts"]) == set(m2_exposure.REPORT_CATEGORIES)
    assert set(document["retention_category_counts"]) == set(m2_exposure.RETENTION_CATEGORIES)
    assert all(
        set(row) == {"question_id", "category"}
        for row in document["questions"]
        if row["category"] != "not_answerable"
    )
    assert document["gate4_flagged"] is True


def test_report_requires_a_retention_category_for_every_not_answerable_label(
    tmp_path: Path,
) -> None:
    (tmp_path / "out").mkdir()
    measure, gates, labels = _report_inputs(tmp_path)

    missing = _categories(labels)
    missing.pop(labels.not_answerable_ids[0])
    with pytest.raises(ValueError, match="missing"):
        run_report(
            measure=measure,
            gates=gates,
            retention_categories=missing,
            output_path=tmp_path / "out" / "missing.json",
            repo_root=tmp_path / "repo",
        )

    unknown = _categories(labels)
    unknown[labels.not_answerable_ids[0]] = {"category": "made_up"}
    with pytest.raises(ValueError, match="unknown"):
        run_report(
            measure=measure,
            gates=gates,
            retention_categories=unknown,
            output_path=tmp_path / "out" / "unknown.json",
            repo_root=tmp_path / "repo",
        )


def test_report_keeps_closed_reasons_and_drops_free_text(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    measure, gates, labels = _report_inputs(tmp_path)
    first, second = labels.not_answerable_ids[0], labels.not_answerable_ids[1]

    categories = _categories(labels)
    categories[first] = {"category": "other", "reason": "PLANTED BENCHMARK TEXT"}
    categories[second] = {
        "category": "atomic_decomposition",
        "reason": "detail_lost_in_decomposition",
    }
    output = tmp_path / "out" / "report.json"
    document = run_report(
        measure=measure,
        gates=gates,
        retention_categories=categories,
        output_path=output,
        repo_root=tmp_path / "repo",
    )
    serialized = output.read_text(encoding="utf-8")
    assert "PLANTED" not in serialized
    rows = {row["question_id"]: row for row in document["questions"]}
    assert rows[first]["retention_category"] == "other"
    assert "retention_reason" not in rows[first]
    assert rows[second]["retention_reason"] == "detail_lost_in_decomposition"
    assert document["retention_category_counts"]["atomic_decomposition"] == 1
    assert document["retention_category_counts"]["other"] == len(labels.not_answerable_ids) - 1
