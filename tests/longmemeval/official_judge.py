"""Official LongMemEval prompts/parser, kept separate from the generic metric.

Source: xiaowu0162/LongMemEval, src/evaluation/evaluate_qa.py,
commit d6dc8b50a2d9ac0c99485ea28fa5755c62414c34 (get_anscheck_prompt).
Only prompt construction and the upstream label predicate are reproduced here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

OFFICIAL_SOURCE_COMMIT = "d6dc8b50a2d9ac0c99485ea28fa5755c62414c34"
OFFICIAL_SOURCE_SHA256 = "ecce9c4c79dc89d99534ac17b383a5cbb5b9f0c69ee98adaf0684742e3d95251"
OFFICIAL_MAX_TOKENS = 10

_STANDARD = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
_TEMPORAL = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. In addition, do not penalize off-by-one errors for the number of days. If the question asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when the answer is 18), the model's response is still correct. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
_UPDATE = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response contains some previous information along with an updated answer, the response should be considered as correct as long as the updated answer is the required answer.\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
_PREFERENCE = "I will give you a question, a rubric for desired personalized response, and a response from a model. Please answer yes if the response satisfies the desired response. Otherwise, answer no. The model does not need to reflect all the points in the rubric. The response is correct as long as it recalls and utilizes the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
_ABSTENTION = "I will give you an unanswerable question, an explanation, and a response from a model. Please answer yes if the model correctly identifies the question as unanswerable. The model could say that the information is incomplete, or some other information is given but the asked information is not.\n\nQuestion: {}\n\nExplanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the question as unanswerable? Answer yes or no only."

_TEMPLATES = {
    "single-session-user": _STANDARD,
    "single-session-assistant": _STANDARD,
    "multi-session": _STANDARD,
    "temporal-reasoning": _TEMPORAL,
    "knowledge-update": _UPDATE,
    "single-session-preference": _PREFERENCE,
}


def build_official_prompt(
    question_type: str, question: str, reference: str, hypothesis: str, *, question_id: str
) -> str:
    if "_abs" in question_id:
        template = _ABSTENTION
    else:
        try:
            template = _TEMPLATES[question_type]
        except KeyError:
            raise ValueError("Unsupported official question type") from None
    return template.format(question, reference, hypothesis)


def official_label(raw_output: str) -> bool:
    """Exact upstream predicate, even for unexpected text; preserve raw evidence."""
    return "yes" in raw_output.strip().lower()


def _counts(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    observed = [
        row for row in rows if row.get(field) in {"correct", "incorrect", "partially_correct"}
    ]
    correct = sum(row[field] == "correct" for row in observed)
    return {
        "total": len(rows),
        "judged": len(observed),
        "correct": correct,
        "strict_accuracy": correct / len(observed) if observed else None,
        "missing": len(rows) - len(observed),
    }


def score_dual_judgments(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Additive measurement summary; do not change historical generic scoring.

    ABS is an explicitly overlapping subset of the six original question types.
    An unobserved subset is null, never a fabricated zero-percent result.
    """
    categories = sorted({str(row.get("category", "IE-user")) for row in rows})
    paired = [
        row
        for row in rows
        if row.get("official_judgment") in {"correct", "incorrect"}
        and row.get("judgment") in {"correct", "incorrect", "partially_correct"}
    ]
    flips = [
        row.get("question_id")
        for row in paired
        if (row["judgment"] == "correct") != (row["official_judgment"] == "correct")
    ]
    abstentions = [row for row in rows if "_abs" in str(row.get("question_id", ""))]
    return {
        "official": _counts(rows, "official_judgment"),
        "official_by_category": {
            category: _counts(
                [row for row in rows if row.get("category", "IE-user") == category],
                "official_judgment",
            )
            for category in categories
        },
        "abstention_subset": {
            "overlaps_original_categories": True,
            "generic": _counts(abstentions, "judgment"),
            "official": _counts(abstentions, "official_judgment"),
        },
        "paired": {
            "total": len(paired),
            "strict_disagreements": len(flips),
            "strict_disagreement_rate": len(flips) / len(paired) if paired else None,
            "strict_disagreement_ids": flips,
            "three_way_table": dict(
                sorted(
                    Counter(
                        f"{row['judgment']}|{row['official_judgment']}" for row in paired
                    ).items()
                )
            ),
        },
        "official_unexpected_output_count": sum(
            "official_judge_raw_output" in row
            and str(row["official_judge_raw_output"]).strip().lower() not in {"yes", "no"}
            for row in rows
        ),
        "official_length_finish_count": sum(
            row.get("official_judge_finish_reason") == "length" for row in rows
        ),
    }


def require_complete_official_coverage(
    rows: list[dict[str, Any]], expected_question_ids: Sequence[str]
) -> dict[str, Any]:
    """Offline primary-series coverage check, not full run/candidate qualification.

    Callers supply IDs from the frozen dev fixture and compare this summary to
    both evaluate/score manifest summaries. Keep additive/partial scoring intact:
    successful generic evidence must survive a failed official call.
    """
    expected = set(expected_question_ids)
    if len(expected) != 50 or len(expected_question_ids) != 50:
        raise ValueError("Official primary coverage requires 50 unique frozen dev IDs")
    observed = [row.get("question_id") for row in rows]
    if len(observed) != 50 or set(observed) != expected:
        raise ValueError("Official primary coverage requires exactly one row per frozen dev ID")
    if any(row.get("official_judgment") not in {"correct", "incorrect"} for row in rows):
        raise ValueError("Official primary coverage requires a binary verdict for every dev ID")
    return score_dual_judgments(rows)["official"]
