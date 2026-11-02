"""Synthetic-only v3 scoring and legacy-isolation contracts; no execution wiring."""

from __future__ import annotations

import ast
import copy
from fractions import Fraction
import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.eval import campaign_v3 as v3, campaign_validation as legacy
from orchestrator.eval.campaign_guard import CampaignStopped
from tests.longmemeval.official_judge import require_complete_official_coverage

PIN = {"identity": "synthetic-passed-pilot", "sha256": "a" * 64}


def dataset() -> list[dict[str, Any]]:
    return [
        {
            "question_id": f"synthetic-{i}" + ("_abs" if i == 24 else ""),
            "question": f"Fictional question {i}?",
            "answer": f"Fictional reference {i}",
            "question_type": "single-session-user",
        }
        for i in range(50)
    ]


def aggregate(row: dict[str, Any]) -> None:
    # Independent, test-local oracle; never call the production majority helper.
    for target, source in (
        ("official_majority", "official_judgment"),
        ("generic_strict_majority", "judgment"),
    ):
        row[target] = (
            "correct" if sum(s[source] == "correct" for s in row["samples"]) >= 2 else "incorrect"
        )


def rows(correct: set[int] | None = None) -> list[dict[str, Any]]:
    correct = set(range(20, 45)) if correct is None else correct
    result = []
    for i, source in enumerate(dataset()):
        label = "correct" if i in correct else "incorrect"
        row = {
            **source,
            "reference": source["answer"],
            "category": "IE-user",
            "label_aggregation": "majority_of_3",
            "samples": [
                {
                    "sample": sample,
                    "hypothesis": "fictional answer",
                    "judgment": label,
                    "official_judgment": label,
                }
                for sample in (1, 2, 3)
            ],
        }
        aggregate(row)
        result.append(row)
    return result


def set_labels(row: dict[str, Any], bits: list[int]) -> None:
    for sample, bit in zip(row["samples"], bits, strict=True):
        sample["official_judgment"] = sample["judgment"] = "correct" if bit else "incorrect"
    aggregate(row)


def candidate(w: int = 5, losses: int = 0) -> dict[str, list[dict[str, Any]]]:
    arms = {name: rows() for name in ("A0", "A1", "A2", "C0")}
    for i in range(w):
        set_labels(arms["C0"][i], [1, 1, 1])
    for i in range(20, 20 + losses):
        set_labels(arms["C0"][i], [0, 0, 0])
    return arms


def ratio(value: dict[str, Any]) -> Fraction:
    return Fraction(value["numerator"], value["denominator"])


@pytest.mark.parametrize(
    "w,losses,expected",
    [
        (5, 1, Fraction(7, 64)),
        (5, 0, Fraction(1, 32)),
        (4, 0, Fraction(1, 16)),
        (1, 2, Fraction(7, 8)),
        (0, 0, Fraction(1)),
        (15, 3, Fraction(988, 262144)),
        (3, 0, Fraction(1, 8)),
        (6, 1, Fraction(1, 16)),
        (4, 1, Fraction(3, 16)),
        (0, 5, Fraction(1)),
    ],
)
def test_exact_sign_test(w: int, losses: int, expected: Fraction) -> None:
    assert v3.sign_test_p(w, losses) == expected
    assert isinstance(v3.sign_test_p(w, losses), Fraction)


@pytest.mark.parametrize(
    "w,losses,expected", [(5, 0, True), (4, 0, False), (6, 1, False), (4, 1, False), (3, 0, False)]
)
def test_criterion_one_boundaries(w: int, losses: int, expected: bool) -> None:
    assert v3.criterion_one(w, losses) is expected


@pytest.mark.parametrize("bad", [-1, 1.0, True, False, "1", None])
@pytest.mark.parametrize("position", [0, 1])
def test_invalid_sign_counts(bad: Any, position: int) -> None:
    arguments = [5, 0]
    arguments[position] = bad
    for function in (v3.sign_test_p, v3.criterion_one):
        with pytest.raises(CampaignStopped, match="Sign test counts"):
            function(*arguments)


@pytest.mark.parametrize(
    "change,message",
    [
        ("two", "exactly 3 samples"),
        ("four", "exactly 3 samples"),
        ("order", "indices"),
        ("duplicate_sample", "indices"),
        ("bool_sample", "indices"),
        ("float_sample", "indices"),
        ("sample_record", "sample record"),
        ("hypothesis_type", "hypothesis missing"),
        ("generic", "Generic label missing"),
        ("official", "Official label missing"),
        ("official_majority", "official majority differs"),
        ("generic_strict_majority", "generic strict majority differs"),
        ("aggregation", "aggregation differs"),
        ("hypothesis", "top-level hypothesis"),
        ("judgment", "top-level judgment"),
        ("official_judgment", "top-level official_judgment"),
        ("duplicate_id", "identities differ"),
        ("unknown_id", "identities differ"),
        ("missing_id", "exactly one row"),
        ("category", "category missing"),
    ],
)
def test_coverage_and_aggregate_rejections(change: str, message: str) -> None:
    result = rows()
    row = result[0]
    if change == "two":
        row["samples"].pop()
    elif change == "four":
        row["samples"].append(copy.deepcopy(row["samples"][0]))
    elif change == "order":
        row["samples"].reverse()
    elif change == "duplicate_sample":
        row["samples"][1]["sample"] = 1
    elif change == "bool_sample":
        row["samples"][0]["sample"] = True
    elif change == "float_sample":
        row["samples"][0]["sample"] = 1.0
    elif change == "sample_record":
        row["samples"][0] = None
    elif change == "hypothesis_type":
        row["samples"][0]["hypothesis"] = None
    elif change == "generic":
        row["samples"][0]["judgment"] = "unknown"
    elif change == "official":
        row["samples"][0]["official_judgment"] = "partially_correct"
    elif change in {"official_majority", "generic_strict_majority"}:
        row[change] = "correct"
    elif change == "aggregation":
        del row["label_aggregation"]
    elif change in {"hypothesis", "judgment", "official_judgment"}:
        row[change] = None
    elif change == "duplicate_id":
        result[1]["question_id"] = row["question_id"]
    elif change == "unknown_id":
        row["question_id"] = "unknown"
    elif change == "missing_id":
        result.pop()
    elif change == "category":
        row["category"] = ""
    with pytest.raises(CampaignStopped, match=message):
        v3.validate_v3_rows(result, dataset())


def test_frozen_dataset_ids_must_be_fifty_unique() -> None:
    source = dataset()
    with pytest.raises(CampaignStopped, match="50 unique frozen"):
        v3.validate_v3_rows(rows(), source[:-1])
    source[1]["question_id"] = source[0]["question_id"]
    with pytest.raises(CampaignStopped, match="Duplicate dev identity"):
        v3.validate_v3_rows(rows(), source)


def test_summary_counts_and_strict_majority() -> None:
    result = rows()
    # A 2/3 majority is correct; partially-correct stays binary strict incorrect.
    set_labels(result[0], [1, 1, 0])
    result[1]["samples"][0]["judgment"] = "partially_correct"
    aggregate(result[1])
    summary = v3.v3_summaries(result)
    for key, total, correct in (("samples", 150, 77), ("majority", 50, 26)):
        assert summary[key] == {
            "total": total,
            "judged": total,
            "missing": 0,
            "correct": correct,
            "strict_accuracy": {
                "numerator": correct,
                "denominator": total,
                "display": str(Fraction(correct, total)),
            },
        }
    assert summary["official_by_category"]["IE-user"] == summary["majority"]
    assert summary["abstention_subset"]["official"]["total"] == 1
    assert summary["abstention_subset"]["official"]["correct"] == 1
    assert summary["abstention_subset"]["overlaps_original_categories"] is True
    assert summary["paired"]["total"] == 150
    assert ratio(summary["paired"]["strict_disagreement_rate"]) == 0


def test_empty_abstention_is_not_zero_accuracy() -> None:
    result = rows()
    result[24]["question_id"] = "synthetic-24"
    assert v3.v3_summaries(result)["abstention_subset"]["official"]["strict_accuracy"] is None


def test_partial_labels_cannot_form_a_generic_strict_majority() -> None:
    result = rows()
    for sample in result[0]["samples"][:2]:
        sample["judgment"] = "partially_correct"
    aggregate(result[0])
    assert [sample["judgment"] for sample in result[0]["samples"]] == [
        "partially_correct",
        "partially_correct",
        "incorrect",
    ]
    assert result[0]["generic_strict_majority"] == "incorrect"
    v3.validate_v3_rows(result, dataset())
    result[0]["generic_strict_majority"] = "correct"
    with pytest.raises(CampaignStopped, match="^V3 generic strict majority differs$"):
        v3.validate_v3_rows(result, dataset())


@pytest.mark.parametrize(
    "count,expected,passes", [(30, Fraction(1, 5), True), (31, Fraction(31, 150), False)]
)
def test_partial_labels_count_as_strict_disagreements(
    count: int, expected: Fraction, passes: bool
) -> None:
    result = rows()
    samples = [sample for row in result for sample in row["samples"]]
    correct_samples = [sample for sample in samples if sample["official_judgment"] == "correct"]
    for sample in correct_samples[:count]:
        sample["judgment"] = "partially_correct"
    for row in result:
        aggregate(row)
    summary = v3.v3_summaries(result)
    assert summary["paired"]["strict_disagreements"] == count
    assert ratio(summary["paired"]["strict_disagreement_rate"]) == expected
    partial_as_correct_rate = Fraction(
        sum(
            (sample["judgment"] in {"correct", "partially_correct"})
            != (sample["official_judgment"] == "correct")
            for sample in samples
        ),
        150,
    )
    assert partial_as_correct_rate == Fraction(0, 1)
    assert partial_as_correct_rate != expected
    if passes:
        assert v3.qualification_v3(result, dataset()) == summary
    else:
        with pytest.raises(CampaignStopped, match="^Judge disagreement exceeds 20 percent$"):
            v3.qualification_v3(result, dataset())


@pytest.mark.parametrize("count,passes", [(30, True), (31, False)])
def test_per_sample_disagreement_boundary(count: int, passes: bool) -> None:
    result = rows()
    for sample in [sample for row in result for sample in row["samples"]][:count]:
        sample["judgment"] = "incorrect" if sample["official_judgment"] == "correct" else "correct"
    for row in result:
        aggregate(row)
    summary = v3.v3_summaries(result)
    assert summary["paired"]["strict_disagreements"] == count
    assert ratio(summary["paired"]["strict_disagreement_rate"]) == Fraction(count, 150)
    if passes:
        assert v3.qualification_v3(result, dataset()) == summary
    else:
        with pytest.raises(CampaignStopped, match="Judge disagreement exceeds 20 percent"):
            v3.qualification_v3(result, dataset())


@pytest.mark.parametrize("size,passes", [(3, True), (4, True), (5, False)])
def test_v2_minimum_category_sanity(size: int, passes: bool) -> None:
    source, result = dataset(), rows()
    for item, row in zip(source[:size], result[:size], strict=True):
        item["question_type"] = row["question_type"] = "single-session-preference"
        row["category"] = "IE-preference"
    if passes:
        assert (
            v3.qualification_v3(result, source)["official_by_category"]["IE-preference"]["correct"]
            == 0
        )
    else:
        with pytest.raises(CampaignStopped, match="Zero official category; investigate"):
            v3.qualification_v3(result, source)


@pytest.mark.parametrize("correct,passes", [(2, False), (3, True), (47, True), (48, False)])
def test_accuracy_bounds_at_fifty_question_resolution(correct: int, passes: bool) -> None:
    # Exactly 5% or 95% is impossible on 50 majority rows; test both adjacent counts.
    result = rows(set(range(correct)))
    if passes:
        assert v3.qualification_v3(result, dataset())["majority"]["correct"] == correct
    else:
        with pytest.raises(CampaignStopped, match="Implausible primary accuracy"):
            v3.qualification_v3(result, dataset())


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("question", "changed", "Question text"),
        ("reference", "changed", "Reference"),
        ("question_type", "changed", "Question type"),
        ("category", "MR", "Category differs"),
        ("error", "failed", "Canonical question"),
    ],
)
def test_frozen_question_integrity(field: str, value: str, message: str) -> None:
    result = rows()
    result[0][field] = value
    with pytest.raises(CampaignStopped, match=message):
        v3.qualification_v3(result, dataset())


def test_candidate_acceptance_and_record() -> None:
    arms = candidate()
    original = copy.deepcopy(arms)
    result = v3.paired_result_v3(arms, PIN)
    assert result["rule"] == v3.RULE_V3
    assert result["mode"] == "candidate"
    assert (result["w"], result["l"], result["net"]) == (5, 0, 5)
    assert ratio(result["p"]) == Fraction(1, 32)
    assert result["criterion_one"] is True
    assert result["accepted"] is True
    assert result["interpretation"] == "measured dev gain"
    assert result["F"] == 0
    assert result["pilot_report_pin"] == PIN
    assert len(result["calibration"]) == 6
    assert set(result["calibration"]) == {"A0/A1", "A0/A2", "A1/A0", "A1/A2", "A2/A0", "A2/A1"}
    assert not result["calibration_fired"]
    assert result["labels"]["C0"]["synthetic-0"] == {
        "samples": ["correct"] * 3,
        "official_majority": "correct",
    }
    assert len(result["limitations"]) == 3
    json.dumps(result)
    assert arms == original


@pytest.mark.parametrize("w,losses", [(2, 0), (6, 1)])
def test_candidate_net_and_p_value_rejections(w: int, losses: int) -> None:
    result = v3.paired_result_v3(candidate(w, losses), PIN)
    assert result["accepted"] is False
    assert result["criterion_one"] is False
    assert result["interpretation"] == "reject or investigate; no candidate rerun"


def test_category_loss_of_three_blocks_otherwise_passing_candidate() -> None:
    arms = candidate(15, 3)
    for arm in arms.values():
        for row in arm[20:23]:
            row["category"] = "TR"
    result = v3.paired_result_v3(arms, PIN)
    assert result["criterion_one"] is True
    assert result["category_deltas"]["TR"] == -3
    assert result["accepted"] is False
    # The unchanged limit permits exactly two losses.
    for arm in arms.values():
        arm[22]["category"] = "IE-user"
    assert v3.paired_result_v3(arms, PIN)["accepted"] is True


def test_abstention_loss_blocks_otherwise_passing_candidate() -> None:
    arms = candidate(10)
    set_labels(arms["C0"][24], [0, 0, 0])
    result = v3.paired_result_v3(arms, PIN)
    assert result["criterion_one"] is True
    assert result["abstention_delta"] == -1
    assert result["accepted"] is False


@pytest.mark.parametrize("direction", ["A1/A0", "A0/A1"])
def test_calibration_direction_and_investigate(direction: str) -> None:
    arms = candidate()
    if direction == "A1/A0":
        for row in arms["A1"][5:10]:
            set_labels(row, [1, 1, 1])
    else:
        for row in arms["A1"][20:25]:
            set_labels(row, [0, 0, 0])
    result = v3.paired_result_v3(arms, PIN)
    reverse = "/".join(reversed(direction.split("/")))
    assert result["calibration"][direction]["criterion_one"] is True
    assert result["calibration"][reverse]["criterion_one"] is False
    assert result["calibration"][direction]["w"] == 5
    assert result["calibration"][direction]["l"] == 0
    assert result["F"] == 5
    assert result["accepted"] is False
    assert result["interpretation"] == "investigate"


@pytest.mark.parametrize("mode", ["pilot", "candidate"])
@pytest.mark.parametrize(
    "change,message",
    [
        ("arm", "^Campaign arm coverage differs$"),
        ("id", "^Paired identities differ$"),
        ("category", "^Paired categories differ$"),
    ],
)
def test_cross_arm_coverage_and_categories(mode: str, change: str, message: str) -> None:
    arms = candidate()
    if mode == "pilot":
        arms = {key: arms[key] for key in ("A0", "A1")}
    if change == "arm":
        arms["unexpected"] = arms.pop("A1")
    elif change == "id":
        arms["A1"][0]["question_id"] = "unknown"
    else:
        arms["A1"][0]["category"] = "MR"
    with pytest.raises(CampaignStopped, match=message):
        if mode == "pilot":
            v3.pilot_report(arms)
        else:
            v3.paired_result_v3(arms, PIN)


@pytest.mark.parametrize(
    "pin",
    [
        {},
        {"identity": "id"},
        {"identity": "", "sha256": "a" * 64},
        {"identity": " ", "sha256": "a" * 64},
        {"identity": 1, "sha256": "a" * 64},
        {"identity": "id", "sha256": "z" * 64},
        {"identity": "id", "sha256": "A" * 64},
        {"identity": "id", "sha256": "a" * 63},
        {"identity": "id", "sha256": None},
        {**PIN, "passed": True},
        None,
    ],
)
def test_invalid_pilot_pins(pin: Any) -> None:
    with pytest.raises(CampaignStopped, match="Invalid passed pilot report pin"):
        v3.paired_result_v3(candidate(), pin)


def test_pilot_owner_discriminating_example() -> None:
    arms = {name: rows(set()) for name in ("A0", "A1")}
    set_labels(arms["A1"][0], [1, 1, 1])
    for arm in arms.values():
        set_labels(arm[1], [0, 0, 1])
    original = copy.deepcopy(arms)
    result = v3.pilot_report(arms)
    assert result["mode"] == "pilot"
    assert result["rule"] == v3.RULE_V3
    assert result["matched_sample_flip_rate"] == {
        "numerator": 3,
        "denominator": 150,
        "display": "1/50",
    }
    assert result["majority_label_flip_rate"] == {
        "numerator": 1,
        "denominator": 50,
        "display": "1/50",
    }
    assert result["all_cross_pairs_flip_rate"] == {
        "numerator": 13,
        "denominator": 450,
        "display": "13/450",
    }
    assert result["passed"] is False
    assert "accepted" not in result
    assert result["byte_identical_questions"] == {"A0": 50, "A1": 50}
    assert result["labels"]["A1"]["synthetic-0"]["samples"] == ["correct"] * 3
    json.dumps(result)
    assert arms == original


def test_pilot_reduction_pass_and_exact_strings() -> None:
    arms = {name: rows(set()) for name in ("A0", "A1")}
    set_labels(arms["A1"][0], [0, 0, 1])
    arms["A0"][0]["samples"][0]["hypothesis"] += " "
    arms["A1"][1]["samples"][1]["hypothesis"] += "\n"
    result = v3.pilot_report(arms)
    assert result["passed"] is True
    assert ratio(result["matched_sample_flip_rate"]) == Fraction(1, 150)
    assert ratio(result["majority_label_flip_rate"]) == 0
    assert ratio(result["all_cross_pairs_flip_rate"]) == Fraction(3, 450)
    assert result["byte_identical_questions"] == {"A0": 49, "A1": 49}
    assert "accepted" not in result


def test_zero_flip_pilot_fails_and_arm_order_is_irrelevant() -> None:
    arms = {"A1": list(reversed(rows())), "A0": rows()}
    result = v3.pilot_report(arms)
    assert result["passed"] is False
    assert (
        ratio(result["matched_sample_flip_rate"]) == ratio(result["majority_label_flip_rate"]) == 0
    )


def test_v3_unreachable_and_legacy_consumers_fail_closed() -> None:
    assert legacy.RULES == ("2026-10-28-official-v1", "2026-10-31-official-v2")
    with pytest.raises(CampaignStopped, match="Unknown acceptance rule version"):
        legacy.paired_result(candidate(), rule=v3.RULE_V3)
    with pytest.raises(ValueError, match="binary verdict"):
        require_complete_official_coverage(rows(), [row["question_id"] for row in dataset()])
    with pytest.raises(KeyError, match="official_judgment"):
        legacy.paired_result(candidate())
    directory = Path(legacy.__file__).parent
    importers = set()
    for path in directory.glob("*.py"):
        if path.name == "campaign_v3.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                if any("campaign_v3" in alias.name for alias in node.names):
                    importers.add(path.name)
            elif isinstance(node, ast.ImportFrom):
                if "campaign_v3" in (node.module or "") or any(
                    alias.name == "campaign_v3" for alias in node.names
                ):
                    importers.add(path.name)
    # B2b2 adds the pinned pilot factory and exact-pin launch/retry dispatch.
    # Production CLI unreachability remains enforced by the legacy resolver test.
    assert importers == {
        "campaign_guard.py",
        "campaign_launch.py",
        "campaign_retry.py",
        "campaign_v3_pilot.py",
        "fact_harness.py",
    }


def test_v3_does_not_call_legacy_scoring_or_import_layout() -> None:
    tree = ast.parse(Path(v3.__file__).read_text())
    forbidden = {
        "_counts",
        "score_dual_judgments",
        "require_complete_official_coverage",
        "qualification",
        "paired_result",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else (node.func.id if isinstance(node.func, ast.Name) else "")
            )
            assert name not in forbidden
        elif isinstance(node, ast.ImportFrom):
            assert "campaign_layout" not in (node.module or "")
            assert not any(alias.name == "campaign_layout" for alias in node.names)
