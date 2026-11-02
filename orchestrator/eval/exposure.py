"""Pure prompt-surface diagnostics, not a retrieval candidate.

Duplicate occupancy counts a line once when it matches ANY earlier rendered
line: exact equality after case-folding/whitespace collapse, and (separately)
token-set Jaccard >= 0.8 on case-folded alphanumeric tokens. Similarity includes
exact duplicates. Neither rule claims semantic equivalence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from orchestrator.memory import injection

BASELINE_K = 5
CANDIDATE_KS = (10, 15, 20)
SWEEP_KS = (BASELINE_K, *CANDIDATE_KS)
REQUIRED_CASES = 4
CASE_COUNT = 5
QUESTION_COUNT = 50
RANK_LIMIT = 50
DUPLICATE_JACCARD_THRESHOLD = 0.8
DUPLICATE_RULE = "casefold_alphanumeric_token_set_jaccard_ge_0.8"
EXACT_DUPLICATE_RULE = "casefold_whitespace_collapse_equality"
DISTRACTOR_RULE = "first_capitalized_content_token_else_first_content_token_overlap"
STOPWORDS = frozenset(
    "a an the i me my mine we us our you your he she it they their his her its "
    "what which when where why how who whom did do does was were is are be been "
    "being have has had can could would should will may might of to in on at for "
    "from with by and or but that this these those about as if than then previous "
    "chat conversation tell please mention mentioned user".split()
)


def content_tokens(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE))


def duplicate_occupancy(texts: Sequence[str]) -> dict[str, int]:
    """Count occupied redundant slots, not pairs; similarity includes exact matches."""
    normalized = [" ".join(text.casefold().split()) for text in texts]
    tokens = [content_tokens(text) for text in texts]
    exact = sum(value in normalized[:pos] for pos, value in enumerate(normalized))
    similar = 0
    for pos, value in enumerate(tokens):
        if any(
            bool(value | earlier)
            and len(value & earlier) / len(value | earlier) >= DUPLICATE_JACCARD_THRESHOLD
            for earlier in tokens[:pos]
        ):
            similar += 1
    return {"exact": exact, "similarity": similar}


def distractor_entity_tokens(question: str) -> set[str]:
    """First capitalized non-stopword token, else first non-stopword token.

    Tokens are Unicode alphanumerics; one token is deliberately only a lexical
    entity proxy. An added memory sharing it is an early warning, not a verdict.
    The entity text itself must never be exported to the sanitized output.
    """
    words = re.findall(r"[^\W_]+", question, flags=re.UNICODE)
    words = [word for word in words if word.casefold() not in STOPWORDS]
    selected = next((word for word in words if word[0].isupper()), words[0] if words else "")
    return {selected.casefold()} if selected else set()


def distractor_counts(
    question: str, added_memories: Sequence[Mapping[str, object]]
) -> dict[str, int]:
    entity = distractor_entity_tokens(question)
    return {
        "added": len(added_memories),
        "plausible_support": sum(
            bool(entity & content_tokens(str(memory.get("content") or "")))
            for memory in added_memories
        ),
    }


def rendered_memory_rows(
    inputs: injection.MemoryContextInputs, block: str, *, require_complete: bool = False
) -> list[dict[str, object]]:
    """Recover IDs from the actual renderer's prefix-only removal behaviour.

    Content is normalized to a single line by production. Count rendered lines
    within each production section, then associate them with the corresponding
    input prefix. This never searches for memory text substrings (which would
    misattribute duplicates). It also includes retained L0 rows.
    """
    section = ""
    retrieved_count = 0
    frozen_count = 0
    for line in block.splitlines():
        if line == "[FROZEN MEMORIES]":
            section = "frozen"
        elif line == "About this user:":
            section = "retrieved"
        elif line == "Recent context:":
            section = "summary"
        elif line.startswith("- "):
            if section == "frozen":
                frozen_count += 1
            elif section == "retrieved" and re.match(
                r"^- (?:Fact|Project|Preference|Important): ", line
            ):
                retrieved_count += 1
    eligible = [
        memory
        for memory in inputs.retrieved[: injection.MAX_MEMORY_ITEMS]
        if injection._truncate_to_chars(
            injection._normalize_content(memory.get("content")), injection.MAX_SINGLE_MEMORY_CHARS
        )
    ]
    eligible_l0 = [
        memory
        for memory in inputs.l0_memories
        if injection._truncate_to_chars(
            injection._normalize_content(memory.get("content")), injection.MAX_L0_CHARS
        )
    ]
    if (
        retrieved_count > len(eligible)
        or frozen_count > len(eligible_l0)
        or (
            require_complete
            and (retrieved_count != len(eligible) or frozen_count != len(eligible_l0))
        )
    ):
        raise ValueError("Renderer section contract changed")
    return [*eligible_l0[:frozen_count], *eligible[:retrieved_count]]


def rendered_memory_texts(block: str) -> list[str]:
    """Visible memory text only (no labels or summaries), including truncation."""
    texts = []
    section = ""
    for line in block.splitlines():
        if line == "[FROZEN MEMORIES]":
            section = "frozen"
        elif line == "About this user:":
            section = "retrieved"
        elif line == "Recent context:":
            section = "summary"
        elif section == "frozen" and line.startswith("- "):
            texts.append(line[2:])
        elif section == "retrieved" and re.match(
            r"^- (?:Fact|Project|Preference|Important): ", line
        ):
            texts.append(line.split(": ", 1)[1])
    return texts


def measure(
    inputs: injection.MemoryContextInputs,
    *,
    target_ids: Sequence[str],
    gold_conversation_ids: Sequence[str],
    source_conversations: Mapping[str, str | None],
    ranking: Sequence[Mapping[str, object]],
    max_tokens: int = injection.DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """Measure a single question at the current width with the production renderer.

    Gold-session coverage is a proxy: a source session represented by any memory
    is not proof that its answer-bearing fact was rendered. Empty gold is None.
    """
    block = injection.render_memory_context(inputs, max_tokens=max_tokens)
    rows = rendered_memory_rows(inputs, block)
    rendered = [str(row["id"]) for row in rows]
    # A second render disables only the budget, retaining all formatter limits.
    # Thus blank content and normal width slicing are not called budget drops.
    unbounded = injection.render_memory_context(inputs, max_tokens=10**12)
    pre_rows = rendered_memory_rows(inputs, unbounded, require_complete=True)
    pre_ids = [str(row["id"]) for row in pre_rows]
    dropped = [mid for mid in pre_ids if mid not in set(rendered)]
    retrieved_ids = [str(row["id"]) for row in inputs.retrieved[: injection.MAX_MEMORY_ITEMS]]
    pre_retrieved = set(retrieved_ids) | {str(row["id"]) for row in inputs.l0_memories}
    gold = set(gold_conversation_ids)
    covered = {source_conversations.get(mid) for mid in rendered} & gold
    targets = set(target_ids)
    ranks = {str(row["id"]): pos for pos, row in enumerate(ranking, start=1)}
    return {
        "target_exposed": bool(targets) and targets <= set(rendered),
        "target_any_exposed": bool(targets & set(rendered)),
        "target_pre_budget": bool(targets) and targets <= pre_retrieved,
        "targets": {
            mid: {
                "rendered": mid in rendered,
                "rank": ranks.get(mid),
                "budget_dropped": mid in dropped,
            }
            for mid in sorted(targets)
        },
        "gold_session_coverage_proxy": len(covered) / len(gold) if gold else None,
        "covered_gold_sessions": sorted(str(value) for value in covered),
        "rendered_ids": rendered,
        "retrieved_ids": retrieved_ids,
        "pre_budget_ids": sorted(pre_retrieved),
        "budget_dropped_ids": dropped,
        "budget_dropped": len(dropped),
        "target_budget_dropped": len(targets & set(dropped)),
        "effective_k": sum(str(row["id"]) in rendered for row in inputs.retrieved),
        "rendered_tokens": injection.estimate_tokens(block),
        "duplicates": duplicate_occupancy(rendered_memory_texts(block)),
    }


def coverage_losses(
    baseline: Mapping[str, float | None], current: Mapping[str, float | None]
) -> list[str]:
    if set(baseline) != set(current):
        raise ValueError("Coverage question sets differ")
    losses = []
    for qid, before in baseline.items():
        after = current[qid]
        if (before is None) != (after is None):
            raise ValueError(f"Gold coverage availability changed with width: {qid}")
        if before is not None and after is not None and after < before:
            losses.append(qid)
    return sorted(losses)


def decide(per_k_summary: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    """Owner's exclusive, ordered four-outcome chain; never authorizes a campaign."""
    if set(per_k_summary) != set(SWEEP_KS):
        raise ValueError("Expected baseline and all candidate widths")
    baseline = per_k_summary[BASELINE_K]["coverage"]
    if len(baseline) != QUESTION_COUNT:
        raise ValueError("Decision requires all 50 dev questions")
    losses: dict[int, list[str]] = {}
    for k in CANDIDATE_KS:
        row = per_k_summary[k]
        if not 0 <= row["cases_strict"] <= row["cases_pre_budget"] <= CASE_COUNT:
            raise ValueError("Require 0 <= R <= P <= 5")
        if row["target_budget_dropped"] < 0:
            raise ValueError("Negative target budget drops")
        losses[k] = coverage_losses(baseline, row["coverage"])
    for k in CANDIDATE_KS:
        row = per_k_summary[k]
        if (
            row["cases_strict"] >= REQUIRED_CASES
            and row["target_budget_dropped"] == 0
            and not losses[k]
        ):
            return {
                "outcome": "PROPOSE_WIDTH_CAMPAIGN",
                "k": k,
                "reasons": ["strict_threshold_met", "no_target_budget_drop", "coverage_preserved"],
            }
    for k in CANDIDATE_KS:
        row = per_k_summary[k]
        if row["cases_pre_budget"] >= REQUIRED_CASES and row["target_budget_dropped"] > 0:
            return {
                "outcome": "BUDGET_LIMITED",
                "k": k,
                "reasons": ["coupled_width_plus_budget", "owner_signoff_required"],
            }
    for k in CANDIDATE_KS:
        row = per_k_summary[k]
        if row["cases_strict"] >= REQUIRED_CASES and row["target_budget_dropped"] == 0:
            causes = {qid: row.get("coverage_causes", {}).get(qid) for qid in losses[k]}
            if any(
                cause not in {"prefix_instability", "non_target_budget_drop"}
                for cause in causes.values()
            ):
                raise ValueError("Missing coverage-loss attribution")
            return {
                "outcome": "COVERAGE_REGRESSION",
                "k": k,
                "reasons": ["coverage_decreased", "no_campaign"],
                "questions": causes,
            }
    return {"outcome": "INVESTIGATE_RETRIEVAL", "k": None, "reasons": ["strict_threshold_not_met"]}


def summarize(questions: Sequence[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
    """Aggregate sanitized question metrics; attribute coverage losses from IDs."""
    output: dict[int, dict[str, Any]] = {}
    for k in SWEEP_KS:
        rows = [question["widths"][str(k)] for question in questions]
        baseline_rows = [question["widths"][str(BASELINE_K)] for question in questions]
        coverage = {
            question["question_id"]: row["gold_session_coverage_proxy"]
            for question, row in zip(questions, rows, strict=True)
        }
        baseline = {
            question["question_id"]: row["gold_session_coverage_proxy"]
            for question, row in zip(questions, baseline_rows, strict=True)
        }
        losses = coverage_losses(baseline, coverage)
        causes = {}
        for question, row, before in zip(questions, rows, baseline_rows, strict=True):
            qid = question["question_id"]
            if qid in losses:
                lost_sessions = set(before["covered_gold_sessions"]) - set(
                    row["covered_gold_sessions"]
                )
                lost_ids = [
                    mid
                    for mid in before["rendered_ids"]
                    if question["source_conversations"].get(mid) in lost_sessions
                ]
                causes[qid] = (
                    "prefix_instability"
                    if any(mid not in row["pre_budget_ids"] for mid in lost_ids)
                    else "non_target_budget_drop"
                )
        values = [value for value in coverage.values() if value is not None]
        output[k] = {
            "cases_strict": sum(row["target_exposed"] for row in rows),
            "cases_any": sum(row["target_any_exposed"] for row in rows),
            "cases_pre_budget": sum(row["target_pre_budget"] for row in rows),
            "target_budget_dropped": sum(row["target_budget_dropped"] for row in rows),
            "budget_dropped": sum(row["budget_dropped"] for row in rows),
            "rendered_tokens": sum(row["rendered_tokens"] for row in rows),
            "effective_k": sum(row["effective_k"] for row in rows),
            "duplicates_exact": sum(row["duplicates"]["exact"] for row in rows),
            "duplicates_similarity": sum(row["duplicates"]["similarity"] for row in rows),
            "coverage": coverage,
            "coverage_mean_proxy": sum(values) / len(values) if values else None,
            "coverage_changed": [qid for qid in coverage if coverage[qid] != baseline[qid]],
            "coverage_losses": losses,
            "coverage_causes": causes,
        }
    return output
