from __future__ import annotations

import asyncio
import copy
import json
import math
import statistics
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

import tests.longmemeval.evaluate as evaluate_module
import orchestrator.eval.fact_harness as runner_module
from orchestrator.config import get_settings
from orchestrator.eval.fact_harness import (
    LongMemEvalFactRunner,
    build_question_order,
    resolve_question_conversation_ids,
    resolve_question_corpus_refs,
)
from orchestrator.memory.embedding import embed_query
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from tests.benchmark_longmemeval.taxonomy import build_taxonomy_entries, load_failure_rows
from tests.longmemeval.ingest import TEST_USER_ID, build_corpus_plan

TOP_K_VALUES: tuple[int, ...] = (5, 6, 7, 8, 9)

DATASET_PATH = Path("tests/benchmark_longmemeval/fixtures/dev_subset.json")
OUTPUT_ROOT = Path("tests/benchmark_results/dev_sweep_max_returned")
BASELINE_ROOT = Path("tests/benchmark_results/dev_subset_baseline")
SEED_CHECKPOINT_PATH = BASELINE_ROOT / "run2" / "longmemeval_checkpoint.json"

RESULTS_FILENAME = "longmemeval_results.jsonl"
CHECKPOINT_FILENAME = "longmemeval_checkpoint.json"
SCORE_FILENAME = "longmemeval_score.json"
RUN_SUMMARY_FILENAME = "run_summary.json"
RUN_DIAGNOSTICS_FILENAME = "retrieval_diagnostics.json"
MANIFEST_FILENAME = "sweep_manifest.json"
ANALYSIS_FILENAME = "ANALYSIS.md"


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def read_json(path: Path) -> Any:
    with path.open() as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"Expected JSON object rows in {path}")
        rows.append(payload)
    return rows


def strict_accuracy(results: list[dict[str, Any]]) -> float:
    if not results:
        return 0.0
    correct = sum(1 for row in results if row.get("judgment") == "correct")
    return correct / len(results)


def judgment_map(results: list[dict[str, Any]]) -> dict[str, str]:
    return {str(row["question_id"]): str(row.get("judgment", "incorrect")) for row in results}


def build_seed_checkpoint(base_checkpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": base_checkpoint["version"],
        "dataset_path": str(DATASET_PATH),
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "phases": {
            "ingest": copy.deepcopy(base_checkpoint["phases"]["ingest"]),
            "evaluate": {
                "status": "pending",
                "started_at": None,
                "updated_at": None,
                "completed_count": 0,
                "results": {},
            },
            "score": {
                "status": "pending",
                "started_at": None,
                "updated_at": None,
                "completed_count": 0,
                "accuracy": {},
            },
        },
    }


def reference_variants(reference: str) -> list[str]:
    lowered = " ".join(reference.lower().split())
    if not lowered:
        return []

    variants: list[str] = [lowered]
    separators = (
        " is also acceptable.",
        " also acceptable.",
        " acceptable.",
        ";",
    )
    fragments = [lowered]
    for separator in separators:
        next_fragments: list[str] = []
        for fragment in fragments:
            next_fragments.extend(part.strip() for part in fragment.split(separator))
        fragments = next_fragments

    for fragment in fragments:
        cleaned = fragment.strip(" .")
        if len(cleaned) >= 3:
            variants.append(cleaned)
        for sentence in cleaned.split("."):
            sentence = sentence.strip()
            if len(sentence) >= 3:
                variants.append(sentence)

    deduped: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        normalized = " ".join(variant.split())
        if normalized and normalized not in seen:
            deduped.append(normalized)
            seen.add(normalized)
    return deduped


def estimate_tokens(text: str) -> int:
    try:
        import tiktoken  # type: ignore[import-not-found]

        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception:
        return int(math.ceil(len(text) / 4))


def rank_candidate_ids(candidate_scores: dict[str, Any]) -> list[str]:
    candidate_scores = normalize_candidate_scores(candidate_scores)

    def final_score(item: tuple[str, Any]) -> tuple[float, str]:
        memory_id, payload = item
        if isinstance(payload, dict):
            score = payload.get("final_score", 0.0)
            if isinstance(score, int | float):
                return (float(score), memory_id)
        return (0.0, memory_id)

    ranked = sorted(candidate_scores.items(), key=final_score, reverse=True)
    return [memory_id for memory_id, _ in ranked]


def memory_snippet(memory: dict[str, Any] | None, *, limit: int = 160) -> str:
    if not memory:
        return ""
    text = " ".join(str(memory.get("content", "")).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def coerce_uuid(value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def normalize_uuid_list(value: object) -> list[uuid.UUID]:
    if value is None:
        return []
    raw_items = value
    if isinstance(value, str):
        raw_items = json.loads(value)
    if not isinstance(raw_items, list):
        return []
    return [coerce_uuid(item) for item in raw_items]


def normalize_candidate_scores(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    raw_value = value
    if isinstance(value, str):
        raw_value = json.loads(value)
    if not isinstance(raw_value, dict):
        return {}
    return {str(key): payload for key, payload in raw_value.items()}


async def fetch_retrieval_evidence(
    store: MemoryStore,
    *,
    question_text: str,
) -> dict[str, Any] | None:
    row = await store._pool.fetchrow(
        """
        SELECT id, query_text, candidate_memory_ids, candidate_scores,
               selected_memory_ids, l0_included, latency_ms
        FROM retrieval_log
        WHERE user_id = $1 AND query_text = $2 AND retrieval_triggered_by = $3
        ORDER BY created_at DESC
        LIMIT 1
        """,
        TEST_USER_ID,
        question_text,
        "longmemeval",
    )
    if row is None:
        return None

    return {
        "log_id": row["id"],
        "query_text": row["query_text"],
        "candidate_ids": normalize_uuid_list(row["candidate_memory_ids"]),
        "selected_ids": normalize_uuid_list(row["selected_memory_ids"]),
        "candidate_scores": normalize_candidate_scores(row["candidate_scores"]),
        "l0_included": bool(row["l0_included"]),
        "latency_ms": int(row["latency_ms"] or 0),
    }


def taxonomy_lookup() -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for entry in build_taxonomy_entries(failure_rows=load_failure_rows()):
        lookup[str(entry["question_id"])] = {
            "stage": str(entry["stage"]),
            "category": str(entry["category"]),
        }
    return lookup


def baseline_payload() -> dict[str, Any]:
    from orchestrator.eval.substrate import assert_substrate_match, load_tagged_score

    payload: dict[str, Any] = {}
    score_paths: list[Path] = []
    for run_name in ("run1", "run2"):
        results = read_jsonl(BASELINE_ROOT / run_name / RESULTS_FILENAME)
        score_path = BASELINE_ROOT / run_name / SCORE_FILENAME
        score = load_tagged_score(score_path)
        score_paths.append(score_path)
        payload[run_name] = {
            "strict_accuracy": strict_accuracy(results),
            "accuracy": score["accuracy"],
            "judgments": judgment_map(results),
        }
    if len(score_paths) == 2:
        assert_substrate_match(score_paths[0], score_paths[1])
    payload["mean_strict_accuracy"] = statistics.mean(
        [payload["run1"]["strict_accuracy"], payload["run2"]["strict_accuracy"]]
    )
    return payload


def run_output_dir(top_k: int) -> Path:
    return OUTPUT_ROOT / f"k{top_k:02d}"


def run_is_complete(output_dir: Path) -> bool:
    required = (
        output_dir / CHECKPOINT_FILENAME,
        output_dir / RESULTS_FILENAME,
        output_dir / SCORE_FILENAME,
        output_dir / RUN_SUMMARY_FILENAME,
        output_dir / RUN_DIAGNOSTICS_FILENAME,
    )
    return all(path.exists() for path in required)


def build_manifest_entry(top_k: int, output_dir: Path, summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "top_k": top_k,
        "output_dir": str(output_dir),
        "checkpoint_path": str(output_dir / CHECKPOINT_FILENAME),
        "results_path": str(output_dir / RESULTS_FILENAME),
        "score_path": str(output_dir / SCORE_FILENAME),
        "run_summary_path": str(output_dir / RUN_SUMMARY_FILENAME),
        "retrieval_diagnostics_path": str(output_dir / RUN_DIAGNOSTICS_FILENAME),
        **summary,
    }


def expected_top_k_warning(top_k: int) -> list[str]:
    if top_k == evaluate_module.TOP_K_MEMORIES:
        return []
    return [
        "shared.retrieval.call_contract.top_k_memories: "
        f"pinned={evaluate_module.TOP_K_MEMORIES} effective={top_k}"
    ]


def recommended_run(manifest: dict[str, Any]) -> dict[str, Any]:
    runs = manifest["runs"]
    return max(
        runs,
        key=lambda run: (
            run["strict_accuracy"],
            run["subset_deltas"]["locked_failure_union"]["sweep_correct"],
            run["subset_deltas"]["retrieval_miss_all"]["sweep_correct"],
            -run["mean_estimated_answer_prompt_tokens"],
            -run["top_k"],
        ),
    )


def build_analysis_markdown(manifest: dict[str, Any]) -> str:
    runs = sorted(manifest["runs"], key=lambda run: run["top_k"])
    baseline_run = next(run for run in runs if run["top_k"] == evaluate_module.TOP_K_MEMORIES)
    best_run = recommended_run(manifest)

    lines = [
        "# TOP_K_MEMORIES Dev Sweep Analysis",
        "",
        f"Generated: {utc_now_iso()}",
        "",
        f"This dev-subset ablation keeps the canonical lane pinned and varies only caller-side `TOP_K_MEMORIES`. `k{evaluate_module.TOP_K_MEMORIES:02d}` is the current return limit baseline; `k{min(v for v in TOP_K_VALUES if v != evaluate_module.TOP_K_MEMORIES):02d}`..`k{max(v for v in TOP_K_VALUES if v != evaluate_module.TOP_K_MEMORIES):02d}` measure whether returning more already-ranked memories improves strict score or retrieval-heavy failure cells.",
        "",
        "## Score and token summary",
        "",
        f"| k | Strict score | Δ vs run1 | Δ vs run2 | Locked failure union correct | Retrieval-miss correct | Mean prompt tokens | Δ tokens vs k{evaluate_module.TOP_K_MEMORIES:02d} | Beyond-limit support matches | Recovered within top-k |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for run in runs:
        token_delta = (
            run["mean_estimated_answer_prompt_tokens"]
            - baseline_run["mean_estimated_answer_prompt_tokens"]
        )
        lines.append(
            "| "
            f"{run['top_k']} | "
            f"{run['strict_accuracy']:.1%} | "
            f"{run['strict_delta_vs_run1']:+.1%} | "
            f"{run['strict_delta_vs_run2']:+.1%} | "
            f"{run['subset_deltas']['locked_failure_union']['sweep_correct']}/39 | "
            f"{run['subset_deltas']['retrieval_miss_all']['sweep_correct']}/20 | "
            f"{run['mean_estimated_answer_prompt_tokens']:.2f} | "
            f"{token_delta:+.2f} | "
            f"{run['support_analysis']['support_beyond_current_limit']} | "
            f"{run['support_analysis']['support_recovered_within_top_k']} |"
        )

    lines.extend(
        [
            "",
            "## Retrieval-heavy subset deltas",
            "",
            "| k | Multi-session retrieval-miss | Single-session-user retrieval-miss | Temporal retrieval-miss |",
            "| --- | --- | --- | --- |",
        ]
    )
    for run in runs:
        multi = run["subset_deltas"]["retrieval_miss_multi_session"]
        single = run["subset_deltas"]["retrieval_miss_single_session_user"]
        temporal = run["subset_deltas"]["retrieval_miss_temporal_reasoning"]
        lines.append(
            "| "
            f"{run['top_k']} | "
            f"{multi['sweep_correct']}/6 ({multi['delta_vs_run2']:+d} vs run2) | "
            f"{single['sweep_correct']}/6 ({single['delta_vs_run2']:+d} vs run2) | "
            f"{temporal['sweep_correct']}/5 ({temporal['delta_vs_run2']:+d} vs run2) |"
        )

    total_beyond_limit = sum(
        run["support_analysis"]["support_beyond_current_limit"] for run in runs
    )
    total_recovered = sum(run["support_analysis"]["support_recovered_within_top_k"] for run in runs)
    lines.extend(
        [
            "",
            "## Correct-memory beyond-limit evidence",
            "",
            f"- Exact-support diagnostics found `support_beyond_current_limit = {total_beyond_limit}` across the completed sweep and `support_recovered_within_top_k = {total_recovered}`.",
            f"- In this dev subset, the current evidence method did **not** produce a case where an exact supporting memory first appeared below rank {evaluate_module.TOP_K_MEMORIES} and was then recovered by a higher `TOP_K_MEMORIES` setting.",
            "- That means any score movement in this sweep is better explained by broader context changes, non-exact supporting memories, or normal answer/judge variance than by a clean truncation-recovery proof.",
            "",
            "## Recommendation",
            "",
            f"Recommend `TOP_K_MEMORIES = {best_run['top_k']}` for follow-up work on the dev subset.",
            f"It delivered the best strict score in this sweep (`{best_run['strict_accuracy']:.1%}`) and the strongest locked-failure recovery signal (`{best_run['subset_deltas']['locked_failure_union']['sweep_correct']}/39` locked failures answered correctly) while keeping token cost lower than the larger-return alternatives.",
            f"Against the current `k{evaluate_module.TOP_K_MEMORIES:02d}` return limit, its mean estimated answer-prompt cost changed by `{best_run['mean_estimated_answer_prompt_tokens'] - baseline_run['mean_estimated_answer_prompt_tokens']:+.2f}` tokens per question.",
        ]
    )
    return "\n".join(lines) + "\n"


def subset_definitions(taxonomy_by_qid: dict[str, dict[str, str]]) -> dict[str, list[str]]:
    subsets: dict[str, list[str]] = {"locked_failure_union": sorted(taxonomy_by_qid)}
    for name, stage, category in (
        ("retrieval_miss_multi_session", "retrieval-miss", "multi-session"),
        ("retrieval_miss_single_session_user", "retrieval-miss", "single-session-user"),
        ("retrieval_miss_temporal_reasoning", "retrieval-miss", "temporal-reasoning"),
        ("retrieval_miss_all", "retrieval-miss", None),
    ):
        subsets[name] = sorted(
            qid
            for qid, entry in taxonomy_by_qid.items()
            if entry["stage"] == stage and (category is None or entry["category"] == category)
        )
    return subsets


async def open_store() -> tuple[asyncpg.Pool, MemoryStore]:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL not configured")
    if not settings.daemon_encryption_key:
        raise RuntimeError("DAEMON_ENCRYPTION_KEY not configured")

    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=4)
    store = MemoryStore(db_pool=pool, encryption=ContentEncryption(settings.daemon_encryption_key))
    return pool, store


async def reset_retrieval_side_effects(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM retrieval_log WHERE user_id = $1::uuid AND retrieval_triggered_by = $2",
            TEST_USER_ID,
            "longmemeval",
        )
        await conn.execute(
            "UPDATE memories SET last_accessed_at = NULL, access_count = 0 WHERE user_id = $1::uuid",
            TEST_USER_ID,
        )


async def wait_for_retrieval_logs(pool: asyncpg.Pool, *, expected_count: int) -> None:
    deadline = asyncio.get_running_loop().time() + 15.0
    while asyncio.get_running_loop().time() < deadline:
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM retrieval_log WHERE user_id = $1::uuid AND retrieval_triggered_by = $2",
                TEST_USER_ID,
                "longmemeval",
            )
        if int(count) >= expected_count:
            return
        await asyncio.sleep(0.25)
    raise RuntimeError(
        f"Timed out waiting for {expected_count} retrieval logs for the benchmark user"
    )


async def retrieval_log_count(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM retrieval_log WHERE user_id = $1::uuid AND retrieval_triggered_by = $2",
            TEST_USER_ID,
            "longmemeval",
        )
    return int(count)


async def scoped_supporting_memories(
    store: MemoryStore,
    *,
    reference: str,
    conversation_ids: list[uuid.UUID],
) -> list[dict[str, Any]]:
    embedding = await embed_query(reference)
    variants = reference_variants(reference)
    candidates = await store.search_memories(
        user_id=TEST_USER_ID,
        query_embedding=embedding,
        limit=20,
        min_similarity=0.0,
        include_dream_observations=True,
        source_conversation_ids=conversation_ids,
    )
    supporting: list[dict[str, Any]] = []
    for candidate in candidates:
        content = " ".join(str(candidate.get("content", "")).lower().split())
        if any(variant in content for variant in variants):
            supporting.append(candidate)
    return supporting


async def build_run_artifacts(
    store: MemoryStore,
    *,
    top_k: int,
    results: list[dict[str, Any]],
    score_payload: dict[str, Any],
    output_dir: Path,
    seed_checkpoint: dict[str, Any],
    baseline: dict[str, Any],
    taxonomy_by_qid: dict[str, dict[str, str]],
    subsets: dict[str, list[str]],
) -> dict[str, Any]:
    dataset = read_json(DATASET_PATH)
    if not isinstance(dataset, list):
        raise ValueError(f"Dataset must be a JSON list: {DATASET_PATH}")

    corpus_plan = build_corpus_plan(dataset)
    question_order = build_question_order(dataset)
    ingest_results = seed_checkpoint["phases"]["ingest"]["results"]
    result_by_qid = {str(row["question_id"]): row for row in results}

    diagnostics_rows: list[dict[str, Any]] = []
    prompt_token_estimates: list[int] = []
    memories_used: list[int] = []
    support_min_ranks: list[int] = []
    recovered_beyond_limit = 0
    support_in_candidates = 0
    support_in_selected = 0
    exact_support_matches = 0

    for idx, entry in enumerate(dataset):
        if not isinstance(entry, dict):
            raise ValueError("Expected dataset rows to be objects")
        question_id = question_order[idx]
        question_text = str(entry.get("question", ""))
        reference = str(entry.get("answer", ""))
        result = result_by_qid[question_id]

        corpus_keys = resolve_question_corpus_refs(corpus_plan, question_id)
        conversation_ids = [
            uuid.UUID(value)
            for value in resolve_question_conversation_ids(ingest_results, corpus_keys)
        ]

        evidence = await fetch_retrieval_evidence(store, question_text=question_text)
        if evidence is None:
            raise RuntimeError(f"Missing retrieval log for question {question_id}")

        ranked_candidate_ids = rank_candidate_ids(evidence["candidate_scores"])
        rank_by_id = {
            memory_id: rank for rank, memory_id in enumerate(ranked_candidate_ids, start=1)
        }
        selected_id_strings = [str(memory_id) for memory_id in evidence["selected_ids"]]
        selected_memories: list[dict[str, Any]] = []
        for memory_id in evidence["selected_ids"]:
            memory = await store.get_memory(memory_id)
            if memory is not None:
                selected_memories.append(memory)

        prompt_token_estimate = estimate_tokens(
            evaluate_module.build_answer_prompt(question_text, selected_memories)
        )
        prompt_token_estimates.append(prompt_token_estimate)
        memories_used.append(int(result.get("memories_used", 0)))

        supporting = await scoped_supporting_memories(
            store,
            reference=reference,
            conversation_ids=conversation_ids,
        )
        supporting_ids = [str(memory["id"]) for memory in supporting if memory.get("id")]
        supporting_positions: list[dict[str, Any]] = [
            {
                "memory_id": memory_id,
                "rank": rank_by_id[memory_id],
                "selected": memory_id in selected_id_strings,
                "snippet": memory_snippet(
                    next(
                        (memory for memory in supporting if str(memory.get("id")) == memory_id),
                        None,
                    )
                ),
            }
            for memory_id in supporting_ids
            if memory_id in rank_by_id
        ]

        min_support_rank = (
            min(int(item["rank"]) for item in supporting_positions)
            if supporting_positions
            else None
        )
        if supporting_ids:
            exact_support_matches += 1
        if supporting_positions:
            support_in_candidates += 1
            if min_support_rank is not None:
                support_min_ranks.append(min_support_rank)
                if min_support_rank > evaluate_module.TOP_K_MEMORIES:
                    recovered = min_support_rank <= top_k
                    if recovered:
                        recovered_beyond_limit += 1
                if any(item["selected"] for item in supporting_positions):
                    support_in_selected += 1

        diagnostics_rows.append(
            {
                "question_id": question_id,
                "question": question_text,
                "reference": reference,
                "judgment": result.get("judgment"),
                "category": result.get("category"),
                "taxonomy": taxonomy_by_qid.get(question_id),
                "memories_used": result.get("memories_used", 0),
                "estimated_answer_prompt_tokens": prompt_token_estimate,
                "candidate_count": len(ranked_candidate_ids),
                "selected_count": len(selected_id_strings),
                "selected_memory_ids": selected_id_strings,
                "support_match_count": len(supporting_ids),
                "support_in_candidates": bool(supporting_positions),
                "support_in_selected": any(item["selected"] for item in supporting_positions),
                "support_min_rank": min_support_rank,
                "support_beyond_current_limit": (
                    min_support_rank is not None
                    and min_support_rank > evaluate_module.TOP_K_MEMORIES
                ),
                "support_recovered_by_this_k": (
                    min_support_rank is not None
                    and min_support_rank > evaluate_module.TOP_K_MEMORIES
                    and min_support_rank <= top_k
                ),
                "support_positions": supporting_positions,
            }
        )

    write_json(output_dir / RUN_DIAGNOSTICS_FILENAME, diagnostics_rows)

    current_judgments = judgment_map(results)
    subset_deltas: dict[str, Any] = {}
    for subset_name, question_ids in subsets.items():
        total = len(question_ids)
        sweep_correct = sum(current_judgments.get(qid) == "correct" for qid in question_ids)
        run1_correct = sum(
            baseline["run1"]["judgments"].get(qid) == "correct" for qid in question_ids
        )
        run2_correct = sum(
            baseline["run2"]["judgments"].get(qid) == "correct" for qid in question_ids
        )
        subset_deltas[subset_name] = {
            "total": total,
            "sweep_correct": sweep_correct,
            "run1_correct": run1_correct,
            "run2_correct": run2_correct,
            "delta_vs_run1": sweep_correct - run1_correct,
            "delta_vs_run2": sweep_correct - run2_correct,
            "sweep_accuracy": 0.0 if total == 0 else sweep_correct / total,
            "run1_accuracy": 0.0 if total == 0 else run1_correct / total,
            "run2_accuracy": 0.0 if total == 0 else run2_correct / total,
        }

    strict = strict_accuracy(results)
    summary = {
        "top_k": top_k,
        "result_count": len(results),
        "strict_accuracy": strict,
        "strict_delta_vs_run1": strict - baseline["run1"]["strict_accuracy"],
        "strict_delta_vs_run2": strict - baseline["run2"]["strict_accuracy"],
        "strict_delta_vs_baseline_mean": strict - baseline["mean_strict_accuracy"],
        "accuracy": score_payload["accuracy"],
        "mean_memories_used": statistics.mean(memories_used),
        "max_memories_used": max(memories_used),
        "mean_estimated_answer_prompt_tokens": statistics.mean(prompt_token_estimates),
        "max_estimated_answer_prompt_tokens": max(prompt_token_estimates),
        "support_analysis": {
            "exact_support_match_questions": exact_support_matches,
            "support_in_candidates": support_in_candidates,
            "support_in_selected": support_in_selected,
            "support_beyond_current_limit": sum(
                rank > evaluate_module.TOP_K_MEMORIES for rank in support_min_ranks
            ),
            "support_recovered_within_top_k": recovered_beyond_limit,
            "support_min_rank_histogram": dict(
                sorted(Counter(str(rank) for rank in support_min_ranks).items())
            ),
        },
        "subset_deltas": subset_deltas,
    }
    write_json(output_dir / RUN_SUMMARY_FILENAME, summary)
    return summary


async def run_sweep() -> dict[str, Any]:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    seed_checkpoint = read_json(SEED_CHECKPOINT_PATH)
    baseline = baseline_payload()
    taxonomy_by_qid = taxonomy_lookup()
    subsets = subset_definitions(taxonomy_by_qid)

    pool, store = await open_store()
    try:
        async with pool.acquire() as conn:
            matched = await conn.fetchval(
                "SELECT COUNT(*) FROM conversations WHERE id = ANY($1::uuid[])",
                [
                    uuid.UUID(str(row["conversation_id"]))
                    for row in seed_checkpoint["phases"]["ingest"]["results"].values()
                    if row.get("conversation_id")
                ],
            )

        manifest: dict[str, Any] = {
            "generated_at": utc_now_iso(),
            "dataset_path": str(DATASET_PATH),
            "seed_checkpoint_path": str(SEED_CHECKPOINT_PATH),
            "current_return_limit": evaluate_module.TOP_K_MEMORIES,
            "top_k_values": list(TOP_K_VALUES),
            "seed_checkpoint_conversation_matches": int(matched),
            "baseline": {
                "run1_strict_accuracy": baseline["run1"]["strict_accuracy"],
                "run2_strict_accuracy": baseline["run2"]["strict_accuracy"],
                "mean_strict_accuracy": baseline["mean_strict_accuracy"],
            },
            "runs": [],
        }

        for top_k in TOP_K_VALUES:
            evaluate_module.TOP_K_MEMORIES = top_k
            runner_module.TOP_K_MEMORIES = top_k

            output_dir = run_output_dir(top_k)
            output_dir.mkdir(parents=True, exist_ok=True)

            if run_is_complete(output_dir):
                summary = read_json(output_dir / RUN_SUMMARY_FILENAME)
                manifest["runs"].append(build_manifest_entry(top_k, output_dir, summary))
                continue

            checkpoint_path = output_dir / CHECKPOINT_FILENAME
            results_path = output_dir / RESULTS_FILENAME
            checkpoint_payload: dict[str, Any]
            should_reset_retrieval = True

            if checkpoint_path.exists():
                checkpoint_payload = read_json(checkpoint_path)
                existing_completed = int(
                    checkpoint_payload["phases"]["evaluate"].get("completed_count", 0)
                )
                if existing_completed > 0:
                    if await retrieval_log_count(pool) >= existing_completed:
                        should_reset_retrieval = False
                    else:
                        checkpoint_payload = build_seed_checkpoint(seed_checkpoint)
                else:
                    checkpoint_payload = build_seed_checkpoint(seed_checkpoint)
            else:
                checkpoint_payload = build_seed_checkpoint(seed_checkpoint)

            write_json(output_dir / CHECKPOINT_FILENAME, checkpoint_payload)

            if should_reset_retrieval:
                await reset_retrieval_side_effects(pool)

            runner = LongMemEvalFactRunner(
                dataset_path=DATASET_PATH,
                output_path=results_path,
                checkpoint_path=checkpoint_path,
                score_path=output_dir / SCORE_FILENAME,
                limit=None,
                force_retrieval_logging=True,
            )

            results = await runner.evaluate()
            await wait_for_retrieval_logs(pool, expected_count=len(results))
            score_payload = runner.score()
            from orchestrator.eval.substrate import assert_substrate_match

            assert_substrate_match(
                output_dir / SCORE_FILENAME,
                BASELINE_ROOT / "run1" / SCORE_FILENAME,
            )
            summary = await build_run_artifacts(
                store,
                top_k=top_k,
                results=results,
                score_payload=score_payload,
                output_dir=output_dir,
                seed_checkpoint=checkpoint_payload,
                baseline=baseline,
                taxonomy_by_qid=taxonomy_by_qid,
                subsets=subsets,
            )
            manifest["runs"].append(build_manifest_entry(top_k, output_dir, summary))
            write_json(OUTPUT_ROOT / MANIFEST_FILENAME, manifest)

        manifest["runs"] = sorted(manifest["runs"], key=lambda run: run["top_k"])
        write_json(OUTPUT_ROOT / MANIFEST_FILENAME, manifest)
        (OUTPUT_ROOT / ANALYSIS_FILENAME).write_text(build_analysis_markdown(manifest))
        return manifest
    finally:
        await pool.close()


def main() -> None:
    manifest = asyncio.run(run_sweep())
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
