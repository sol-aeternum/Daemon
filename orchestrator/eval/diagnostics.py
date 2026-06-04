"""Retrieval failure-mode diagnostics for LongMemEval.

Classifies wrong answers from scored LongMemEval results into:
  - extraction_miss: no supporting memory exists in the system
  - retrieval_miss: supporting memory exists but was not in the retrieved/top set
  - reader_failure: supporting memory was retrieved but answer was still wrong

Uses embedding-based memory search to determine whether the reference fact
exists in the database, then cross-references against retrieval_log candidate
and selected sets to determine the actual failure mode.

Outputs machine-readable summary + human-readable report under
`tests/benchmark_results/`.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, final

import asyncpg

from orchestrator.config import get_settings
from orchestrator.memory.embedding import embed_query
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_SIMILARITY_THRESHOLD = 0.5


@final
class FailureMode:
    EXTRACTION_MISS = "extraction_miss"
    RETRIEVAL_MISS = "retrieval_miss"
    READER_FAILURE = "reader_failure"
    UNKNOWN = "unknown"


FAILURE_MODES = [
    FailureMode.EXTRACTION_MISS,
    FailureMode.RETRIEVAL_MISS,
    FailureMode.READER_FAILURE,
    FailureMode.UNKNOWN,
]


@dataclass
class RetrievalEvidence:
    log_id: uuid.UUID
    query_text: str
    candidate_ids: list[uuid.UUID]
    selected_ids: list[uuid.UUID]
    candidate_scores: dict[str, Any]
    l0_included: bool
    latency_ms: int


@dataclass
class SupportingMemoryInfo:
    found: bool
    memory_ids: list[uuid.UUID]
    in_candidates: bool
    in_selected: bool


@dataclass
class DiagnosticResult:
    question_id: str
    question: str
    reference: str
    hypothesis: str
    category: str
    judgment: str
    failure_mode: str
    evidence: RetrievalEvidence | None
    supporting_memory: SupportingMemoryInfo | None
    memories_used: int
    note: str = ""


async def _build_store() -> MemoryStore:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL not configured")
    if not settings.daemon_encryption_key:
        raise RuntimeError("DAEMON_ENCRYPTION_KEY not configured")
    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=2,
        max_size=10,
    )
    encryption = ContentEncryption(settings.daemon_encryption_key)
    store = MemoryStore(db_pool=pool, encryption=encryption)
    return store


async def fetch_retrieval_log_for_query(
    store: MemoryStore,
    question_text: str,
    user_id: uuid.UUID,
) -> RetrievalEvidence | None:
    rows = await store._pool.fetch(
        """
        SELECT id, query_text, candidate_memory_ids, candidate_scores,
               selected_memory_ids, l0_included, latency_ms
        FROM retrieval_log
        WHERE user_id = $1 AND query_text = $2
        ORDER BY created_at DESC
        LIMIT 1
        """,
        user_id,
        question_text,
    )
    if not rows:
        return None

    row = rows[0]
    candidate_ids = [uuid.UUID(id_str) for id_str in (row["candidate_memory_ids"] or [])]
    selected_ids = [uuid.UUID(id_str) for id_str in (row["selected_memory_ids"] or [])]
    candidate_scores = row["candidate_scores"] or {}

    return RetrievalEvidence(
        log_id=row["id"],
        query_text=row["query_text"],
        candidate_ids=candidate_ids,
        selected_ids=selected_ids,
        candidate_scores=candidate_scores,
        l0_included=row["l0_included"],
        latency_ms=row["latency_ms"],
    )


async def fetch_retrieval_logs_by_fuzzy_match(
    store: MemoryStore,
    question_text: str,
    user_id: uuid.UUID,
    threshold: float = 0.85,
) -> list[RetrievalEvidence]:
    rows = await store._pool.fetch(
        """
        SELECT id, query_text, candidate_memory_ids, candidate_scores,
               selected_memory_ids, l0_included, latency_ms
        FROM retrieval_log
        WHERE user_id = $1
        ORDER BY created_at DESC
        LIMIT 50
        """,
        user_id,
    )

    results: list[tuple[float, RetrievalEvidence]] = []
    question_lower = question_text.lower()
    for row in rows:
        query_lower = (row["query_text"] or "").lower()
        ratio = SequenceMatcher(None, question_lower, query_lower).ratio()
        if ratio >= threshold:
            candidate_ids = [uuid.UUID(id_str) for id_str in (row["candidate_memory_ids"] or [])]
            selected_ids = [uuid.UUID(id_str) for id_str in (row["selected_memory_ids"] or [])]
            candidate_scores = row["candidate_scores"] or {}
            results.append(
                (
                    ratio,
                    RetrievalEvidence(
                        log_id=row["id"],
                        query_text=row["query_text"],
                        candidate_ids=candidate_ids,
                        selected_ids=selected_ids,
                        candidate_scores=candidate_scores,
                        l0_included=row["l0_included"],
                        latency_ms=row["latency_ms"],
                    ),
                )
            )

    results.sort(key=lambda x: x[0], reverse=True)
    return [ev for _, ev in results]


async def find_supporting_memories(
    store: MemoryStore,
    reference: str,
    user_id: uuid.UUID,
    min_similarity: float = DEFAULT_MEMORY_SIMILARITY_THRESHOLD,
) -> SupportingMemoryInfo:
    """Determine whether the reference fact exists in memory and was retrieved.

    Uses the reference answer to embed-query the memory store, then checks
    whether any of the matching memory IDs appear in the retrieval candidate
    or selected sets. This tells us whether the supporting memory exists
    at all and whether it was part of the retrieval set.
    """
    try:
        ref_embedding = await embed_query(reference)
    except Exception:
        logger.warning("[diagnostics] Failed to embed reference '%s...'", reference[:30])
        return SupportingMemoryInfo(
            found=False, memory_ids=[], in_candidates=False, in_selected=False
        )

    try:
        memories = await store.search_memories(
            user_id=user_id,
            query_embedding=ref_embedding,
            limit=10,
            min_similarity=min_similarity,
            include_dream_observations=True,
        )
    except Exception:
        logger.warning(
            "[diagnostics] Failed to search memories for reference '%s...'",
            reference[:30],
        )
        return SupportingMemoryInfo(
            found=False, memory_ids=[], in_candidates=False, in_selected=False
        )

    matching_ids: list[uuid.UUID] = []
    for mem in memories:
        mem_id = mem.get("id")
        if isinstance(mem_id, uuid.UUID):
            mem_content = mem.get("content", "")
            if reference.lower() in mem_content.lower():
                matching_ids.append(mem_id)

    return SupportingMemoryInfo(
        found=len(matching_ids) > 0,
        memory_ids=matching_ids,
        in_candidates=False,
        in_selected=False,
    )


def merge_supporting_memory_into_evidence(
    evidence: RetrievalEvidence | None,
    supporting: SupportingMemoryInfo,
) -> SupportingMemoryInfo:
    """Merge retrieval log evidence into the supporting memory info.

    Updates in_candidates and in_selected based on the retrieval log.
    If evidence is None, just returns supporting as-is.
    """
    if evidence is None:
        return supporting

    candidate_set = set(str(m) for m in evidence.candidate_ids)
    selected_set = set(str(m) for m in evidence.selected_ids)

    return SupportingMemoryInfo(
        found=supporting.found,
        memory_ids=supporting.memory_ids,
        in_candidates=any(str(m) in candidate_set for m in supporting.memory_ids),
        in_selected=any(str(m) in selected_set for m in supporting.memory_ids),
    )


def classify_failure(
    result: dict[str, Any],
    evidence: RetrievalEvidence | None,
    supporting: SupportingMemoryInfo,
) -> tuple[str, str]:
    """Classify a wrong answer using actual supporting memory evidence.

    Returns (failure_mode, note).
    """
    judgment = result.get("judgment", "incorrect")
    if judgment == "correct":
        return ("", "")

    memories_used = result.get("memories_used", 0)  # noqa: F841

    if evidence is None:
        if not supporting.found:
            return (
                FailureMode.EXTRACTION_MISS,
                "no retrieval log and no supporting memory found for reference; "
                "supporting fact was never extracted",
            )
        if not supporting.in_candidates:
            return (
                FailureMode.EXTRACTION_MISS,
                "no retrieval log; supporting memory exists but was not retrieved "
                "(not in candidate set), indicating extraction failure",
            )
        return (
            FailureMode.UNKNOWN,
            "no retrieval log; supporting memory was in candidates but not selected; "
            "cannot determine failure mode without log",
        )

    candidate_count = len(evidence.candidate_ids)
    selected_count = len(evidence.selected_ids)

    if candidate_count == 0:
        return (
            FailureMode.EXTRACTION_MISS,
            "retrieval returned zero candidates; no memory was extracted for this query",
        )

    if not supporting.found:
        return (
            FailureMode.EXTRACTION_MISS,
            "reference fact not found in memory store; supporting memory was never extracted",
        )

    if supporting.in_selected:
        return (
            FailureMode.READER_FAILURE,
            f"supporting memory was selected ({len(supporting.memory_ids)} match); "
            "LLM retrieved correct memory but produced wrong answer",
        )

    if supporting.in_candidates:
        return (
            FailureMode.RETRIEVAL_MISS,
            f"supporting memory was in candidates ({len(supporting.memory_ids)} match) "
            f"but not selected ({selected_count}/{candidate_count} selected); "
            "memory existed but was not ranked highly enough to be selected",
        )

    if not supporting.in_candidates:
        return (
            FailureMode.RETRIEVAL_MISS,
            f"supporting memory exists but was not in candidates; "
            f"extraction produced memory but it was not retrieved ({candidate_count} "
            f"candidates were retrieved, none matched reference)",
        )

    return (
        FailureMode.UNKNOWN,
        f"supporting memory found but classification unclear; "
        f"candidates={candidate_count} selected={selected_count} "
        f"supporting_in_candidates={supporting.in_candidates} "
        f"supporting_in_selected={supporting.in_selected}",
    )


def build_machine_readable_summary(
    results: list[DiagnosticResult],
    category_breakdown: dict[str, dict[str, int]],
) -> dict[str, Any]:
    total = len(results)
    mode_counts: dict[str, int] = {m: 0 for m in FAILURE_MODES}
    for r in results:
        if r.failure_mode:
            mode_counts[r.failure_mode] = mode_counts.get(r.failure_mode, 0) + 1

    return {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "total_questions": total,
        "failure_mode_counts": mode_counts,
        "category_breakdown": category_breakdown,
        "results": [
            {
                "question_id": r.question_id,
                "question": r.question,
                "reference": r.reference,
                "hypothesis": r.hypothesis,
                "category": r.category,
                "judgment": r.judgment,
                "failure_mode": r.failure_mode,
                "memories_used": r.memories_used,
                "note": r.note,
                "supporting_memory": (
                    {
                        "found": r.supporting_memory.found,
                        "memory_ids": [str(m) for m in r.supporting_memory.memory_ids],
                        "in_candidates": r.supporting_memory.in_candidates,
                        "in_selected": r.supporting_memory.in_selected,
                    }
                    if r.supporting_memory
                    else None
                ),
                "evidence": (
                    {
                        "log_id": str(r.evidence.log_id),
                        "candidate_count": len(r.evidence.candidate_ids),
                        "selected_count": len(r.evidence.selected_ids),
                        "l0_included": r.evidence.l0_included,
                        "latency_ms": r.evidence.latency_ms,
                    }
                    if r.evidence
                    else None
                ),
            }
            for r in results
        ],
    }


def build_human_readable_report(
    results: list[DiagnosticResult],
    category_breakdown: dict[str, dict[str, int]],
    summary: dict[str, Any],
) -> str:
    lines = [
        "# LongMemEval Retrieval Diagnostics Report",
        "",
        f"**Generated:** {summary['generated_at']}",
        f"**Total Questions:** {summary['total_questions']}",
        "",
        "## Failure Mode Summary",
        "",
    ]

    mode_counts = summary["failure_mode_counts"]
    for mode in FAILURE_MODES:
        count = mode_counts.get(mode, 0)
        pct = (count / summary["total_questions"] * 100) if summary["total_questions"] > 0 else 0
        lines.append(f"- **{mode}**: {count} ({pct:.1f}%)")

    lines.extend(["", "## Per-Category Breakdown", ""])

    for category, counts in category_breakdown.items():
        total_cat = sum(counts.values())
        if total_cat == 0:
            continue
        lines.append(f"### {category}")
        for mode, count in counts.items():
            if count > 0:
                pct = count / total_cat * 100
                lines.append(f"- {mode}: {count}/{total_cat} ({pct:.1f}%)")
        lines.append("")

    wrong_results = [r for r in results if r.judgment != "correct"]
    if wrong_results:
        lines.extend(["## Wrong Answer Details", ""])
        for r in wrong_results:
            lines.append(f"### {r.question_id} [{r.category}]")
            lines.append(f"- **Judgment:** {r.judgment}")
            lines.append(f"- **Failure Mode:** {r.failure_mode}")
            lines.append(f"- **Question:** {r.question[:100]}...")
            lines.append(f"- **Reference:** {r.reference[:100]}...")
            lines.append(f"- **Hypothesis:** {r.hypothesis[:100]}...")
            if r.supporting_memory:
                lines.append(
                    f"- **Supporting Memory:** found={r.supporting_memory.found}, "
                    f"in_candidates={r.supporting_memory.in_candidates}, "
                    f"in_selected={r.supporting_memory.in_selected}"
                )
            if r.note:
                lines.append(f"- **Note:** {r.note}")
            if r.evidence:
                lines.append(
                    f"- **Retrieval Log:** {len(r.evidence.selected_ids)}/"
                    f"{len(r.evidence.candidate_ids)} candidates selected, "
                    f"l0_included={r.evidence.l0_included}, "
                    f"latency={r.evidence.latency_ms}ms"
                )
            lines.append("")

    return "\n".join(lines)


def compute_category_breakdown(
    results: list[DiagnosticResult],
) -> dict[str, dict[str, int]]:
    breakdown: dict[str, dict[str, int]] = {}
    for r in results:
        if r.category not in breakdown:
            breakdown[r.category] = {m: 0 for m in FAILURE_MODES}
        if r.failure_mode:
            breakdown[r.category][r.failure_mode] = breakdown[r.category].get(r.failure_mode, 0) + 1
    return breakdown


async def run_diagnostics(
    results_path: Path,
    output_dir: Path,
    user_id: uuid.UUID,
    *,
    use_fuzzy_match: bool = False,
) -> dict[str, Any]:
    """Run retrieval diagnostics on scored LongMemEval results.

    Loads results from `results_path`, queries retrieval logs and memory store
    from the database, classifies wrong answers using embedding-based supporting
    memory evidence, and writes outputs to `output_dir`.

    Outputs:
        <output_dir>/diagnostics_summary.json  - machine-readable summary
        <output_dir>/diagnostics_report.md     - human-readable report

    Args:
        results_path: Path to scored LongMemEval results (JSONL).
        output_dir: Directory for output files.
        user_id: UUID of the user whose memories to inspect.
        use_fuzzy_match: If True, use fuzzy matching when exact query_text
            match fails (for questions with minor text differences).

    Returns:
        The machine-readable summary dict.
    """
    if not results_path.exists():
        raise FileNotFoundError(f"Results file not found: {results_path}")

    results: list[dict[str, Any]] = []
    with results_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.warning("Skipping malformed JSONL line: %s", exc)
                continue

    if not results:
        raise ValueError(f"No valid results found in {results_path}")

    store = await _build_store()
    try:
        diagnostics: list[DiagnosticResult] = []
        for result in results:
            question_id = result.get("question_id", "")
            question_text = result.get("question", "")
            reference = result.get("reference", "")
            judgment = result.get("judgment", "incorrect")

            evidence: RetrievalEvidence | None = None
            supporting = SupportingMemoryInfo(
                found=False, memory_ids=[], in_candidates=False, in_selected=False
            )

            if judgment != "correct":
                evidence = await fetch_retrieval_log_for_query(store, question_text, user_id)
                if evidence is None and use_fuzzy_match:
                    fuzzy_matches = await fetch_retrieval_logs_by_fuzzy_match(
                        store, question_text, user_id
                    )
                    if fuzzy_matches:
                        evidence = fuzzy_matches[0]
                        logger.info(
                            "[diagnostics] Fuzzy-matched question %s to query '%s...'",
                            question_id,
                            evidence.query_text[:50],
                        )

                if reference:
                    raw_supporting = await find_supporting_memories(store, reference, user_id)
                    supporting = merge_supporting_memory_into_evidence(evidence, raw_supporting)

            failure_mode, note = classify_failure(result, evidence, supporting)

            diagnostics.append(
                DiagnosticResult(
                    question_id=question_id,
                    question=question_text,
                    reference=reference,
                    hypothesis=result.get("hypothesis", ""),
                    category=result.get("category", "IE-user"),
                    judgment=judgment,
                    failure_mode=failure_mode,
                    evidence=evidence,
                    supporting_memory=supporting,
                    memories_used=result.get("memories_used", 0),
                    note=note,
                )
            )
    finally:
        await store._pool.close()

    category_breakdown = compute_category_breakdown(diagnostics)
    summary = build_machine_readable_summary(diagnostics, category_breakdown)
    report = build_human_readable_report(diagnostics, category_breakdown, summary)

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "diagnostics_summary.json"
    report_path = output_dir / "diagnostics_report.md"

    with summary_path.open("w") as f:
        json.dump(summary, f, indent=2)
    logger.info("[diagnostics] Summary written to %s", summary_path)

    with report_path.open("w") as f:
        f.write(report)
    logger.info("[diagnostics] Report written to %s", report_path)

    print(f"\n{'=' * 60}")
    print("RETRIEVAL DIAGNOSTICS SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total questions analyzed: {summary['total_questions']}")
    print("\nFailure mode breakdown:")
    for mode in FAILURE_MODES:
        count = summary["failure_mode_counts"].get(mode, 0)
        pct = (count / summary["total_questions"] * 100) if summary["total_questions"] > 0 else 0
        print(f"  {mode}: {count} ({pct:.1f}%)")

    print("\nOutputs:")
    print(f"  {summary_path}")
    print(f"  {report_path}")

    return summary


def main() -> None:
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(
        description="Run retrieval failure-mode diagnostics on LongMemEval results."
    )
    parser.add_argument(
        "--results",
        type=Path,
        required=True,
        help="Path to scored LongMemEval results (JSONL).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tests/benchmark_results"),
        help="Directory for output files (default: tests/benchmark_results).",
    )
    parser.add_argument(
        "--user-id",
        type=uuid.UUID,
        required=True,
        help="UUID of the user whose memories to inspect.",
    )
    parser.add_argument(
        "--fuzzy-match",
        action="store_true",
        help="Use fuzzy matching when exact query_text match fails.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
    )

    asyncio.run(
        run_diagnostics(
            args.results,
            args.output_dir,
            args.user_id,
            use_fuzzy_match=args.fuzzy_match,
        )
    )


if __name__ == "__main__":
    main()
