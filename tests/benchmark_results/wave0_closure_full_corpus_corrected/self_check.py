#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import cast


CATEGORY_FROM_QUESTION_TYPE = {
    "single-session-user": "IE-user",
    "single-session-assistant": "IE-assistant",
    "single-session-preference": "IE-preference",
    "multi-session": "MR",
    "temporal-reasoning": "TR",
    "knowledge-update": "KU",
}

ACCURACY_CATEGORIES = ["IE-user", "IE-assistant", "IE-preference", "MR", "KU", "TR", "ABS"]

JsonDict = dict[str, object]


def load_json_dict(path: Path) -> JsonDict:
    return cast(JsonDict, json.loads(path.read_text()))


def load_json_list(path: Path) -> list[JsonDict]:
    return cast(list[JsonDict], json.loads(path.read_text()))


def str_field(data: JsonDict, key: str, default: str = "") -> str:
    value = data.get(key, default)
    return default if value is None else str(value)


def nested_str_field(data: JsonDict, *keys: str) -> str:
    current: object = data
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return "" if current is None else str(current)


def float_field(data: JsonDict, key: str) -> float:
    value = data.get(key, 0.0)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    return 0.0


def load_results(path: Path) -> list[JsonDict]:
    rows: list[JsonDict] = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(cast(JsonDict, json.loads(line)))
    return rows


def derive_dataset_category(entry: JsonDict) -> str:
    category = str_field(entry, "category")
    if category:
        return category
    qtype = str_field(entry, "question_type", "single-session-user")
    return CATEGORY_FROM_QUESTION_TYPE.get(qtype, "IE-user")


def derive_accuracy(results: list[JsonDict]) -> dict[str, float]:
    counts = {cat: {"correct": 0, "total": 0} for cat in ACCURACY_CATEGORIES}
    for row in results:
        category = str_field(row, "category", "IE-user")
        if category not in counts:
            continue
        counts[category]["total"] += 1
        if str_field(row, "judgment", "incorrect") == "correct":
            counts[category]["correct"] += 1
    return {
        cat: (counts[cat]["correct"] / counts[cat]["total"] if counts[cat]["total"] else 0.0)
        for cat in ACCURACY_CATEGORIES
    }


def main() -> int:
    base = Path(__file__).resolve().parent
    dataset_path = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(tempfile.gettempdir()) / "longmemeval_s_reconstructed_runner_native.json"
    )
    results_path = Path(sys.argv[2]) if len(sys.argv) > 2 else base / "longmemeval_results.jsonl"
    checkpoint_path = (
        Path(sys.argv[3]) if len(sys.argv) > 3 else base / "longmemeval_checkpoint.json"
    )
    score_path = Path(sys.argv[4]) if len(sys.argv) > 4 else base / "longmemeval_score.json"

    dataset = load_json_list(dataset_path)
    results = load_results(results_path)
    checkpoint = load_json_dict(checkpoint_path)
    score = load_json_dict(score_path)

    dataset_by_qid = {str_field(row, "question_id"): row for row in dataset}
    result_by_qid: dict[str, JsonDict] = {}
    duplicate_question_ids: Counter[str] = Counter()
    raw_category_counts: Counter[str] = Counter()
    answer_models: Counter[str] = Counter()
    judge_models: Counter[str] = Counter()
    answer_fingerprints: Counter[str] = Counter()
    judge_fingerprints: Counter[str] = Counter()
    provider_endpoint_slugs: Counter[str] = Counter()

    attempts = len(results)
    success_count = 0
    error_count = 0
    empty_hypothesis_count = 0
    non_empty_reference_count = 0
    reference_mismatch_sample: list[list[str]] = []
    category_mismatch_sample: list[list[str]] = []
    reference_mismatch_count = 0
    category_mismatch_count = 0

    for row in results:
        qid = str_field(row, "question_id")
        if qid in result_by_qid:
            duplicate_question_ids[qid] += 1
        result_by_qid[qid] = row
        raw_category = str_field(row, "category", "IE-user")
        raw_category_counts[raw_category] += 1

        if str_field(row, "reference"):
            non_empty_reference_count += 1
        if str_field(row, "hypothesis") == "":
            empty_hypothesis_count += 1
        if str_field(row, "error"):
            error_count += 1
        else:
            success_count += 1

        answer_model = str_field(row, "answer_model")
        judge_model = str_field(row, "judge_model")
        answer_fingerprint = str_field(row, "answer_fingerprint")
        judge_fingerprint = str_field(row, "judge_fingerprint")
        if answer_model:
            answer_models[answer_model] += 1
        if judge_model:
            judge_models[judge_model] += 1
        if answer_fingerprint:
            answer_fingerprints[answer_fingerprint] += 1
        if judge_fingerprint:
            judge_fingerprints[judge_fingerprint] += 1

        prompt_meta_obj = row.get("answer_prompt_metadata")
        prompt_meta: JsonDict = (
            cast(JsonDict, prompt_meta_obj) if isinstance(prompt_meta_obj, dict) else {}
        )
        provider_slug = str_field(prompt_meta, "provider_endpoint_slug")
        if provider_slug:
            provider_endpoint_slugs[provider_slug] += 1

        dataset_row = dataset_by_qid.get(qid)
        if dataset_row is None:
            continue

        dataset_reference = str_field(dataset_row, "answer") or str_field(dataset_row, "reference")
        raw_reference = str_field(row, "reference")
        if raw_reference != dataset_reference:
            reference_mismatch_count += 1
            if len(reference_mismatch_sample) < 5:
                reference_mismatch_sample.append([qid, raw_reference, dataset_reference])

        dataset_category = derive_dataset_category(dataset_row)
        if raw_category != dataset_category:
            category_mismatch_count += 1
            if len(category_mismatch_sample) < 5:
                category_mismatch_sample.append([qid, raw_category, dataset_category])

    dataset_category_counts = Counter(derive_dataset_category(row) for row in dataset)
    dataset_question_type_counts = Counter(str_field(row, "question_type") for row in dataset)
    derived_accuracy = derive_accuracy(results)
    score_accuracy_obj = score.get("accuracy")
    score_accuracy = (
        cast(JsonDict, score_accuracy_obj) if isinstance(score_accuracy_obj, dict) else {}
    )
    score_matches = all(
        abs(float_field(score_accuracy, cat) - derived_accuracy.get(cat, 0.0)) < 1e-12
        for cat in ACCURACY_CATEGORIES
    )

    summary = {
        "dataset_path": str(dataset_path),
        "results_path": str(results_path),
        "checkpoint_path": str(checkpoint_path),
        "score_path": str(score_path),
        "dataset_rows": len(dataset),
        "raw_rows": attempts,
        "unique_question_ids": len(result_by_qid),
        "duplicate_question_ids": {qid: count + 1 for qid, count in duplicate_question_ids.items()},
        "raw_ids_missing_in_dataset": sorted(set(result_by_qid) - set(dataset_by_qid)),
        "dataset_ids_missing_in_raw": sorted(set(dataset_by_qid) - set(result_by_qid)),
        "success_count": success_count,
        "error_count": error_count,
        "empty_hypothesis_count": empty_hypothesis_count,
        "non_empty_reference_count": non_empty_reference_count,
        "reference_mismatch_count": reference_mismatch_count,
        "reference_mismatch_sample": reference_mismatch_sample,
        "category_mismatch_count": category_mismatch_count,
        "category_mismatch_sample": category_mismatch_sample,
        "raw_category_counts": dict(raw_category_counts),
        "dataset_category_counts": dict(dataset_category_counts),
        "dataset_question_type_counts": dict(dataset_question_type_counts),
        "derived_accuracy": derived_accuracy,
        "score_accuracy": score_accuracy,
        "score_matches_derived_accuracy": score_matches,
        "score_result_count": score.get("result_count"),
        "checkpoint_score_status": nested_str_field(checkpoint, "phases", "score", "status"),
        "checkpoint_score_completed_count": cast(
            object,
            cast(JsonDict, cast(JsonDict, checkpoint.get("phases", {})).get("score", {})).get(
                "completed_count"
            ),
        ),
        "checkpoint_score_result_count": cast(
            object,
            cast(JsonDict, cast(JsonDict, checkpoint.get("phases", {})).get("score", {})).get(
                "result_count"
            ),
        ),
        "checkpoint_evaluate_status": nested_str_field(checkpoint, "phases", "evaluate", "status"),
        "checkpoint_evaluate_completed_count": cast(
            object,
            cast(JsonDict, cast(JsonDict, checkpoint.get("phases", {})).get("evaluate", {})).get(
                "completed_count"
            ),
        ),
        "answer_models": dict(answer_models),
        "judge_models": dict(judge_models),
        "answer_fingerprints": dict(answer_fingerprints),
        "judge_fingerprints": dict(judge_fingerprints),
        "provider_endpoint_slugs_from_prompt_metadata": dict(provider_endpoint_slugs),
        "no_silent_model_fallback": len(answer_models) == 1 and len(judge_models) == 1,
        "no_silent_provider_fallback": set(provider_endpoint_slugs) <= {"openai"}
        if provider_endpoint_slugs
        else True,
    }
    json.dump(summary, sys.stdout, indent=2, sort_keys=True)
    _ = sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
