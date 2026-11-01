"""Pure, unbound majority-of-three scoring; no plan, CLI or dispatch integration.

B2 must compose frozen-dataset provenance, run qualification/comparability,
pilot-file verification and criterion 4 (both modes). These functions neither
authorize execution nor turn a pilot into an acceptance verdict.
"""

from __future__ import annotations

from fractions import Fraction
from itertools import combinations, permutations
from math import comb
import re
from typing import Any, Mapping, Sequence

from orchestrator.eval import campaign_validation as validation
from orchestrator.eval.campaign_validation import MIN_SANITY_CATEGORY, require

RULE_V3 = "2026-11-01-paired-sign-v3"
_BINARY = ("correct", "incorrect")
_GENERIC = (*_BINARY, "partially_correct")
_CONTROLS = ("A0", "A1", "A2")
_LIMITATIONS = (
    "The sign test assumes independent questions; shared sessions and repeated dev-set "
    "selection can make nominal 0.05 optimistic.",
    "Majority-of-3 correctness is not single-answer production accuracy.",
    "Earlier results are not rescored; the v2 noted-dates rejection stands.",
)


def _majority(samples: Sequence[Mapping[str, Any]], field: str) -> str:
    return "correct" if sum(item[field] == "correct" for item in samples) >= 2 else "incorrect"


def _validate_row_contract(row: Mapping[str, Any]) -> None:
    require(isinstance(row, Mapping), "Invalid v3 result row")
    qid = row.get("question_id")
    require(isinstance(qid, str) and qid, "Invalid v3 question identity")
    require(
        isinstance(row.get("category"), str) and bool(row["category"]),
        "V3 category missing",
    )
    for field in ("official_judgment", "judgment", "hypothesis"):
        require(field not in row, f"V3 forbids top-level {field}")
    require(row.get("label_aggregation") == "majority_of_3", "V3 aggregation differs")
    raw_samples = row.get("samples")
    require(
        isinstance(raw_samples, list) and len(raw_samples) == 3,
        "V3 requires exactly 3 samples",
    )
    samples: list[dict[str, Any]] = row["samples"]
    for index, sample in enumerate(samples, 1):
        require(isinstance(sample, dict), "Invalid v3 sample record")
        require(
            type(sample.get("sample")) is int and sample["sample"] == index,
            "V3 sample indices must be 1, 2, 3 in order",
        )
        require(isinstance(sample.get("hypothesis"), str), "V3 sample hypothesis missing")
        require(sample.get("judgment") in _GENERIC, "Generic label missing")
        require(sample.get("official_judgment") in _BINARY, "Official label missing")
    require(
        row.get("official_majority") == _majority(samples, "official_judgment"),
        "V3 official majority differs",
    )
    require(
        row.get("generic_strict_majority") == _majority(samples, "judgment"),
        "V3 generic strict majority differs",
    )


def validate_v3_row(row: Mapping[str, Any]) -> None:
    """Validate a completed harness question, without claiming 50-ID coverage.

    B1's pure scoring contract is unchanged; runtime completion additionally
    requires explicit completed official calls and no harness/sample errors.
    """
    _validate_row_contract(row)
    require("error" not in row, "V3 question has an error")
    for sample in row["samples"]:
        require("error" not in sample, "V3 sample has an error")
        require(
            sample.get("official_judge_status") == "completed",
            "V3 official judge is not completed",
        )


def validate_v3_rows(
    rows: Sequence[Mapping[str, Any]], dataset: Sequence[Mapping[str, Any]]
) -> None:
    """Require 50 unique frozen IDs, three ordered valid samples and honest aggregates.

    The caller supplies the frozen dataset; B2 verifies its provenance/hash.
    Legacy top-level judge/answer fields are forbidden, even if null.
    """
    require(len(dataset) == 50, "V3 coverage requires 50 unique frozen dev IDs")
    ids = [row.get("question_id") for row in dataset]
    require(all(isinstance(qid, str) and qid for qid in ids), "Invalid frozen dev identity")
    require(len(set(ids)) == 50, "Duplicate dev identity")
    require(len(rows) == 50, "V3 coverage requires exactly one row per frozen dev ID")
    observed = []
    for row in rows:
        _validate_row_contract(row)
        observed.append(row["question_id"])
    require(len(set(observed)) == 50 and set(observed) == set(ids), "Paired identities differ")


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    """JSON-safe exact ratio, retaining the observed denominator for auditability."""
    value = Fraction(numerator, denominator)
    return {"numerator": numerator, "denominator": denominator, "display": str(value)}


def _counts_v3(labels: Sequence[str]) -> dict[str, Any]:
    correct = sum(label == "correct" for label in labels)
    return {
        "total": len(labels),
        "judged": len(labels),
        "missing": 0,
        "correct": correct,
        "strict_accuracy": _ratio(correct, len(labels)) if labels else None,
    }


def v3_summaries(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate shape and report 150 samples, 50 majorities and sample disagreement.

    No frozen-set provenance is claimed here; validate_v3_rows(rows, dataset)
    provides the external identity check. All rates are JSON-safe exact ratios.
    """
    validate_v3_rows(rows, rows)
    samples = [sample for row in rows for sample in row["samples"]]
    abstentions = [row for row in rows if "_abs" in row["question_id"]]
    disagreements = sum(
        (sample["judgment"] == "correct") != (sample["official_judgment"] == "correct")
        for sample in samples
    )
    return {
        "samples": _counts_v3([sample["official_judgment"] for sample in samples]),
        "majority": _counts_v3([row["official_majority"] for row in rows]),
        "generic_strict_majority": _counts_v3([row["generic_strict_majority"] for row in rows]),
        "official_by_category": {
            category: _counts_v3(
                [row["official_majority"] for row in rows if row["category"] == category]
            )
            for category in sorted({row["category"] for row in rows})
        },
        "abstention_subset": {
            "overlaps_original_categories": True,
            "official": _counts_v3([row["official_majority"] for row in abstentions]),
            "generic": _counts_v3([row["generic_strict_majority"] for row in abstentions]),
        },
        "paired": {
            "total": len(samples),
            "strict_disagreements": disagreements,
            "strict_disagreement_rate": _ratio(disagreements, len(samples)),
        },
    }


def qualification_v3(
    rows: Sequence[Mapping[str, Any]], dataset: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Check frozen question fields, majority sanity and per-sample disagreement.

    Only question-level qualification lives here; B2 composes manifest,
    checkpoint, corpus, route and execution evidence checks.
    """
    validate_v3_rows(rows, dataset)
    expected = {row["question_id"]: row for row in dataset}
    for row in rows:
        source = expected[row["question_id"]]
        require(row.get("question") == source["question"], "Question text differs from freeze")
        require(row.get("reference") == source["answer"], "Reference differs from freeze")
        require(row.get("question_type") == source["question_type"], "Question type differs")
        require(
            row["category"] == validation.CATEGORY_MAP.get(source["question_type"]),
            "Category differs",
        )
        require(not row.get("error"), "Canonical question has an error")
    summaries = v3_summaries(rows)
    require(
        Fraction(1, 20) <= Fraction(summaries["majority"]["correct"], 50) <= Fraction(19, 20),
        "Implausible primary accuracy",
    )
    require(
        all(
            item["correct"] > 0
            for item in summaries["official_by_category"].values()
            if item["total"] >= MIN_SANITY_CATEGORY
        ),
        "Zero official category; investigate",
    )
    require(
        Fraction(summaries["paired"]["strict_disagreements"], 150) <= Fraction(1, 5),
        "Judge disagreement exceeds 20 percent",
    )
    return summaries


def sign_test_p(w: int, losses: int) -> Fraction:
    """Exact one-sided P(X >= w), X ~ Binomial(w+l, 1/2); no discordance gives 1."""
    require(type(w) is int and type(losses) is int, "Sign test counts must be integers, not bool")
    require(w >= 0 and losses >= 0, "Sign test counts must be nonnegative")
    n = w + losses
    return Fraction(sum(comb(n, k) for k in range(w, n + 1)), 2**n)


def criterion_one(w: int, losses: int) -> bool:
    """Require both net gain >= 3 and exact one-sided p <= 1/20."""
    p = sign_test_p(w, losses)
    return w - losses >= 3 and p <= Fraction(1, 20)


def _arm_labels(
    arms: Mapping[str, Sequence[Mapping[str, Any]]], names: set[str]
) -> tuple[dict[str, dict[str, bool]], dict[str, str], dict[str, Any]]:
    require(set(arms) == names, "Campaign arm coverage differs")
    dataset = arms["A0"]
    labels = {}
    categories = {}
    evidence = {}
    for arm in sorted(names):
        rows = arms[arm]
        validate_v3_rows(rows, dataset)
        categories[arm] = {row["question_id"]: row["category"] for row in rows}
        labels[arm] = {row["question_id"]: row["official_majority"] == "correct" for row in rows}
        evidence[arm] = {
            row["question_id"]: {
                "samples": [sample["official_judgment"] for sample in row["samples"]],
                "official_majority": row["official_majority"],
            }
            for row in rows
        }
    require(
        all(value == categories["A0"] for value in categories.values()),
        "Paired categories differ",
    )
    return labels, categories["A0"], evidence


def _comparison(candidate: Mapping[str, bool], comparator: Mapping[str, bool]) -> dict[str, Any]:
    w = sum(candidate[qid] and not comparator[qid] for qid in comparator)
    losses = sum(comparator[qid] and not candidate[qid] for qid in comparator)
    p = sign_test_p(w, losses)
    return {
        "w": w,
        "l": losses,
        "net": w - losses,
        "p": _ratio(p.numerator, p.denominator),
        "criterion_one": criterion_one(w, losses),
    }


def paired_result_v3(
    arms: Mapping[str, Sequence[Mapping[str, Any]]], pilot_report_pin: Mapping[str, Any]
) -> dict[str, Any]:
    """Report C0/A0 sign-test criteria 1–3 and six directed control calibrations.

    B2 verifies qualification, comparability, the passed pilot file and criterion
    4 (both modes); accepted here is only the conditional paired scoring result.
    A0 supplies matched IDs/categories, not proof of frozen-dataset provenance.
    """
    require(isinstance(pilot_report_pin, Mapping), "Invalid passed pilot report pin")
    require(
        set(pilot_report_pin) == {"identity", "sha256"}
        and isinstance(pilot_report_pin.get("identity"), str)
        and bool(pilot_report_pin["identity"].strip())
        and isinstance(pilot_report_pin.get("sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", pilot_report_pin["sha256"]) is not None,
        "Invalid passed pilot report pin",
    )
    labels, categories, evidence = _arm_labels(arms, {*_CONTROLS, "C0"})
    result = _comparison(labels["C0"], labels["A0"])
    deltas = {
        category: sum(
            int(labels["C0"][qid]) - int(labels["A0"][qid])
            for qid in categories
            if categories[qid] == category
        )
        for category in sorted(set(categories.values()))
    }
    abstention = sum(
        int(labels["C0"][qid]) - int(labels["A0"][qid]) for qid in categories if "_abs" in qid
    )
    calibration = {
        f"{candidate}/{comparator}": _comparison(labels[candidate], labels[comparator])
        for candidate, comparator in permutations(_CONTROLS, 2)
    }
    fired = any(pair["criterion_one"] for pair in calibration.values())
    flips = {
        f"{a}/{b}": sum(labels[a][qid] != labels[b][qid] for qid in categories)
        for a, b in combinations(_CONTROLS, 2)
    }
    accepted = (
        result["criterion_one"] and min(deltas.values()) >= -2 and abstention >= 0 and not fired
    )
    return {
        "rule": RULE_V3,
        "mode": "candidate",
        "paired_comparator": "A0",
        "pilot_report_pin": dict(pilot_report_pin),
        **result,
        "category_deltas": deltas,
        "abstention_delta": abstention,
        "calibration": calibration,
        "calibration_fired": fired,
        "control_flips": flips,
        "F": max(flips.values()),
        "labels": evidence,
        "accepted": accepted,
        "interpretation": "investigate"
        if fired
        else ("measured dev gain" if accepted else "reject or investigate; no candidate rerun"),
        "limitations": list(_LIMITATIONS),
    }


def pilot_report(arms: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    """Report official flip rates; pass iff majority rate < matched-sample rate.

    Cross-pair flips and exact-string identical counts are report-only. No
    acceptance verdict is produced; B2 composes criterion 4 (both modes).
    """
    labels, categories, evidence = _arm_labels(arms, {"A0", "A1"})
    samples = {
        arm: {row["question_id"]: row["samples"] for row in arms[arm]} for arm in ("A0", "A1")
    }
    matched = sum(
        a["official_judgment"] != b["official_judgment"]
        for qid in categories
        for a, b in zip(samples["A0"][qid], samples["A1"][qid], strict=True)
    )
    majority = sum(labels["A0"][qid] != labels["A1"][qid] for qid in categories)
    cross = sum(
        a["official_judgment"] != b["official_judgment"]
        for qid in categories
        for a in samples["A0"][qid]
        for b in samples["A1"][qid]
    )
    identical = {
        arm: sum(len({item["hypothesis"] for item in row}) == 1 for row in samples[arm].values())
        for arm in ("A0", "A1")
    }
    return {
        "rule": RULE_V3,
        "mode": "pilot",
        "matched_sample_flip_rate": _ratio(matched, 150),
        "majority_label_flip_rate": _ratio(majority, 50),
        "all_cross_pairs_flip_rate": _ratio(cross, 450),
        "byte_identical_questions": identical,
        "passed": Fraction(majority, 50) < Fraction(matched, 150),
        "labels": evidence,
        "limitations": list(_LIMITATIONS),
    }
