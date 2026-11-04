"""Pure prompt-surface diagnostics, not a retrieval candidate.

Duplicate occupancy counts a line once when it matches ANY earlier rendered
line: exact equality after case-folding/whitespace collapse, and (separately)
token-set Jaccard >= 0.8 on case-folded alphanumeric tokens. Similarity includes
exact duplicates. Neither rule claims semantic equivalence.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Mapping, Sequence
from typing import Any

from orchestrator.memory import injection
from orchestrator.memory.embedding import EmbeddingVectorResult
from orchestrator.memory.multi_query import SubqueryProvider, validate_subqueries
from orchestrator.memory.store import MemoryStore
from orchestrator.memory.injection import (
    _section_counts as _section_counts,
    _eligible_l0 as _eligible_l0,
    _expected_retained_l0 as _expected_retained_l0,
    rendered_memory_rows as rendered_memory_rows,
)

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


@dataclass(frozen=True, slots=True)
class PinnedSubqueryProvider:
    """Frozen evaluation input, not an inference client or an acceptance result."""

    subqueries: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.subqueries) is not tuple:
            raise ValueError("Pinned subqueries must be a tuple")
        validate_subqueries(list(self.subqueries), "")

    async def get_subqueries(
        self, message: str, *, reference_time: datetime, deadline: float
    ) -> list[str]:
        return validate_subqueries(list(self.subqueries), message)


async def measure_multi_query_exposure(
    store: MemoryStore,
    *,
    user_id: uuid.UUID,
    question: str,
    provider: SubqueryProvider,
    query_embeddings: Mapping[str, EmbeddingVectorResult],
    reference_time: datetime,
    allowed_source_conversation_ids: list[uuid.UUID] | None,
    target_ids: Sequence[str],
    gold_conversation_ids: Sequence[str],
    source_conversations: Mapping[str, str | None],
    merge: str = "additive",
    max_tokens: int = injection.DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """Offline per-question gate input, through production gather and exposure.

    Refuse live decomposition providers (including subclasses overriding the
    frozen provider), and require every original/component vector up front.
    This opens no database connection; the caller supplies its scoped store.
    It grants no campaign, threshold, ingestion or route approval authority.
    """
    if type(provider) is not PinnedSubqueryProvider:
        raise ValueError("Exposure requires a frozen PinnedSubqueryProvider, never live inference")
    if merge not in {"additive", "round_robin"}:
        raise ValueError("Invalid exposure merge")
    query = injection._normalize_content(question)
    components = validate_subqueries(list(provider.subqueries), query)
    vectors = dict(query_embeddings)
    for text in [query, *components]:
        result = vectors.get(text)
        if not isinstance(result, EmbeddingVectorResult) or not result.embedding:
            raise ValueError("Exposure refuses missing cached query embeddings")
    baseline = await injection.gather_memory_context_inputs(
        store,
        user_id=user_id,
        pipeline="cloud",
        query_text=query,
        allowed_source_conversation_ids=allowed_source_conversation_ids,
        record_side_effects=False,
        query_reference_time=reference_time,
        subquery_provider=PinnedSubqueryProvider(()),
        evaluation=True,
        evaluation_merge="additive",
        evaluation_embeddings=vectors,
    )
    candidate = await injection.gather_memory_context_inputs(
        store,
        user_id=user_id,
        pipeline="cloud",
        query_text=query,
        allowed_source_conversation_ids=allowed_source_conversation_ids,
        record_side_effects=False,
        query_reference_time=reference_time,
        subquery_provider=provider,
        evaluation=True,
        evaluation_merge=merge,
        evaluation_embeddings=vectors,
    )

    def metrics(inputs: injection.MemoryContextInputs) -> dict[str, Any]:
        return measure(
            inputs,
            target_ids=target_ids,
            gold_conversation_ids=gold_conversation_ids,
            source_conversations=source_conversations,
            ranking=inputs.retrieved,
            max_tokens=max_tokens,
        )

    before, after = metrics(baseline), metrics(candidate)
    rows = rendered_memory_rows(candidate, injection.render_memory_context(candidate, max_tokens))
    original = set(before["rendered_ids"])
    added = [row for row in rows if str(row["id"]) not in original]
    return {
        "baseline": before,
        "candidate": after,
        "merge": merge,
        "subquery_count": len(components),
        "distractors": distractor_counts(question, added),
        "exposure_regression": before["target_exposed"] and not after["target_exposed"],
    }


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
    # Frozen rows removed by the renderer's own L0_TOKEN_BUDGET are absent from
    # both renders, so the diff above misses them. They were in the pre-budget
    # input and removed by a token budget: count them as budget drops, so a
    # trimmed frozen target is never "pre-budget but neither rendered nor dropped".
    eligible_l0 = _eligible_l0(inputs)
    retained_l0 = _expected_retained_l0(inputs, len(eligible_l0))
    dropped += [
        str(row["id"]) for row in eligible_l0[retained_l0:] if str(row["id"]) not in rendered
    ]
    retrieved_ids = [
        str(row["id"]) for row in inputs.retrieved[: injection.retrieved_width(inputs)]
    ]
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
