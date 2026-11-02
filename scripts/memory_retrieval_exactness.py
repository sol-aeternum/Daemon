"""Read-only, zero-provider retrieval exactness/candidate-pool audit (#601).

Only the primary runs this against the pinned dev snapshot. No restore, campaign
admission, index maintenance, provider dispatch, answer generation or judging.
Raw evidence contains text/SQL parameters and MUST remain outside Git.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import json
import re
import statistics
import subprocess
from typing import Any, cast
from unittest.mock import patch

import asyncpg

from orchestrator.config import get_settings
from orchestrator.eval import exposure
from orchestrator.eval import retrieval_exactness as rules
from orchestrator.eval.bench_db import (
    check_benchmark_dsn,
    fingerprint_benchmark_users,
    question_user_id,
)
from orchestrator.eval.fact_harness import question_as_of
from orchestrator.memory import injection, retrieval
from orchestrator.memory.embedding import EmbeddingVectorResult
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from scripts import memory_exposure_sweep as sweep
from tests.longmemeval.evaluate import build_parity_memory_context

VECTOR_FROM = datetime(2026, 11, 2, 9, 55, 58, tzinfo=UTC)
VECTOR_UNTIL = datetime(2026, 11, 2, 10, 34, 15, tzinfo=UTC)
FINGERPRINT_PREFIX = "5cb6ab14"
ENABLE_SETTINGS = ("enable_indexscan", "enable_indexonlyscan", "enable_bitmapscan")
EXPLAIN = "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
REPORT = sweep.ROOT / "tests/benchmark_results/memory_retrieval_exactness_2026-11-03.md"
EVIDENCE = REPORT.with_name(REPORT.stem + "_evidence.json")
PLAN_QUESTION_PREFIXES = ("0bb5a684", "0a34ad58")
MISMATCH_MEMORY_PREFIXES = ("0770505d", "07c6d26d")


@dataclass(frozen=True)
class CapturedQuery:
    kind: str
    sql: str
    args: tuple[Any, ...]

    def with_limit(self, limit: int | None) -> CapturedQuery:
        """Change only the captured LIMIT parameter, retaining every filter."""
        match = re.search(r"\bLIMIT\s+\$(\d+)\s*$", self.sql, re.IGNORECASE)
        if match is None:
            raise ValueError("Production query has no terminal parameterized LIMIT")
        args = list(self.args)
        args[int(match[1]) - 1] = limit
        return replace(self, args=tuple(args))

    async def fetch(self, reader: Any) -> Any:
        return await reader.fetch(self.sql, *self.args)

    async def explain(self, reader: Any) -> tuple[Any, dict[str, Any]]:
        plan = await reader.fetchval(EXPLAIN + self.sql, *self.args)
        return plan, rules.plan_summary(plan)


class RecordingReader(sweep.SnapshotReader):
    """The production store sees exactly this same serialized connection."""

    def __init__(self, connection: Any):
        super().__init__(connection)
        self.calls: list[CapturedQuery] = []

    async def fetch(self, sql: str, *args: Any) -> Any:
        kind = "vector" if " AS similarity" in sql else "bm25" if " AS bm25_score" in sql else None
        if kind is not None:
            self.calls.append(CapturedQuery(kind, sql, args))
        return await super().fetch(sql, *args)

    def queries(self, start: int) -> dict[str, CapturedQuery]:
        calls = self.calls[start:]
        if sorted(call.kind for call in calls) != ["bm25", "vector"]:
            raise ValueError("Require exactly one production vector and BM25 query")
        return {call.kind: call for call in calls}


class LocalSettings:
    """Only allowlisted SET LOCAL statements, inside a verified RO/RR scope."""

    def __init__(self, connection: Any, originals: dict[str, str], probes: int):
        self.connection = connection
        self.originals = originals
        self.probes = probes
        self.original_probes = probes
        self.active = True
        self.statements: list[str] = []

    async def set_local(self, name: str, value: int | str) -> None:
        if not self.active:
            raise ValueError("Settings require an active read-only transaction")
        if name == "ivfflat.probes":
            if type(value) is not int or value < 1:
                raise ValueError("Invalid probes")
        elif name not in ENABLE_SETTINGS or value not in ("on", "off"):
            raise ValueError("Unapproved setting")
        sql = f"SET LOCAL {name} = {value}"
        await self.connection.execute(sql)
        self.statements.append(sql)

    @asynccontextmanager
    async def mode(self, mode: str, *, probes: int | None = None) -> AsyncIterator[None]:
        if mode not in rules.MODES:
            raise ValueError("Unapproved exactness mode")
        try:
            await self.set_local(
                "ivfflat.probes",
                probes
                if probes is not None
                else rules.EXPECTED_PROBES
                if mode == "probes10"
                else self.probes,
            )
            for name in ENABLE_SETTINGS:
                await self.set_local(name, "off" if mode == "exact" else self.originals[name])
            yield
        finally:
            for name in ENABLE_SETTINGS:
                await self.set_local(name, self.originals[name])
            await self.set_local("ivfflat.probes", self.original_probes)


@asynccontextmanager
async def readonly_snapshot(connection: Any) -> AsyncIterator[LocalSettings]:
    async with connection.transaction(isolation="repeatable_read", readonly=True):
        if (
            await connection.fetchval("SHOW transaction_read_only") != "on"
            or await connection.fetchval("SHOW transaction_isolation") != "repeatable read"
        ):
            raise ValueError("Require read-only repeatable-read transaction")
        probes = int(await connection.fetchval("SHOW ivfflat.probes"))
        originals = {
            name: str(await connection.fetchval(f"SHOW {name}")) for name in ENABLE_SETTINGS
        }
        controls = LocalSettings(connection, originals, probes)
        try:
            yield controls
        finally:
            controls.active = False


@contextmanager
def overrides(pool: int, k: int) -> Iterator[None]:
    if pool not in rules.POOLS or k not in (*rules.WIDTHS, 20):
        raise ValueError("Unapproved in-process override")
    with patch.object(retrieval, "INITIAL_VECTOR_CANDIDATES", pool), sweep.memory_width(k):
        yield


@contextmanager
def capture_components() -> Iterator[list[dict[str, Any]]]:
    original = retrieval.retrieve_memories
    captured: list[dict[str, Any]] = []

    async def recording(*args: Any, **kwargs: Any) -> Any:
        output = kwargs.get("scored_candidates_out")
        if output is None:
            output = []
            kwargs["scored_candidates_out"] = output
        start = len(output)
        result = await original(*args, **kwargs)
        captured.extend(dict(row) for row in output[start:])
        return result

    with patch.object(retrieval, "retrieve_memories", recording):
        yield captured


async def gather(
    store: Any,
    reader: RecordingReader,
    inputs: sweep.SweepInputs,
    entry: Mapping[str, Any],
    vector: list[float],
    *,
    pool: int,
    k: int,
) -> tuple[str, Any, dict[str, CapturedQuery], list[dict[str, Any]]]:
    clock = retrieval.RECENCY_REFERENCE_TIME.set(question_as_of(entry))
    result = EmbeddingVectorResult(
        embedding=vector,
        provider="azure-openrouter",
        model=inputs.model,
        storage_model=inputs.storage_model,
    )
    start = len(reader.calls)
    try:
        with (
            overrides(pool, k),
            sweep.cached_query(sweep.parity_query_text(entry["question"]), result),
            capture_components() as scored,
        ):
            block, gathered = await build_parity_memory_context(
                store,
                user_id=question_user_id(entry["question_id"]),
                question_text=entry["question"],
                allowed_source_conversation_ids=None,
                log_retrieval=False,
                query_reference_time=question_as_of(entry),
            )
            if gathered.retrieval_mode in {"failed", "none", "lexical"}:
                raise ValueError("Production parity retrieval failed")
            queries = reader.queries(start)
            if not scored or any("final_score" not in row for row in scored):
                raise ValueError("Production scorer components unavailable")
            return block, gathered, queries, scored
    finally:
        retrieval.RECENCY_REFERENCE_TIME.reset(clock)


async def execution_facts(
    reader: Any, users: Sequence[Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """No settings overrides. Definitions/plans are raw; only hashes are exported."""
    settings = await reader.fetch(
        "SELECT name, setting, source, reset_val FROM pg_settings WHERE name = 'ivfflat.probes'"
    )
    role_settings = await reader.fetch(
        "SELECT setdatabase::int, setrole::int, option FROM pg_db_role_setting,"
        " LATERAL unnest(setconfig) AS option"
        " WHERE option LIKE 'ivfflat.probes=%'"
        " AND setdatabase IN (0, (SELECT oid FROM pg_database WHERE datname = current_database()))"
        " AND setrole IN (0, (SELECT oid FROM pg_roles WHERE rolname = current_user))"
    )
    version = await reader.fetchval("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    indexes = await reader.fetch(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'memories'"
    )
    selected = [
        dict(row)
        for row in indexes
        if row["indexname"] == "idx_memories_embedding_ivfflat"
        or ("USING btree" in row["indexdef"] and re.search(r"\(user_id[,)]", row["indexdef"]))
    ]
    if not any(row["indexname"] == "idx_memories_embedding_ivfflat" for row in selected):
        raise ValueError("Pinned IVFFlat index missing")
    models = await reader.fetch(
        "SELECT embedding_model, count(*) AS count FROM memories GROUP BY embedding_model"
    )
    counts = await reader.fetch(
        "SELECT user_id, count(*) AS count FROM memories WHERE user_id = ANY($1::uuid[]) GROUP BY user_id",
        list(users),
    )
    by_user = {str(row["user_id"]): int(row["count"]) for row in counts}
    values = [by_user.get(str(user), 0) for user in users]
    raw = {"pg_settings": [dict(row) for row in settings], "indexes": selected}
    clean = {
        "pg_settings": [
            {
                "setting": int(row["setting"]),
                "reset": int(row["reset_val"]),
                "source": str(row["source"]).replace(" ", "_"),
            }
            for row in settings
        ],
        "db_role_settings": [
            {
                "database_oid": row["setdatabase"],
                "role_oid": row["setrole"],
                "probes": int(row["option"].split("=", 1)[1]),
            }
            for row in role_settings
        ],
        "pgvector_version": version,
        "indexes": [
            {
                "name": row["indexname"],
                "definition_sha256": sweep.digest_bytes(row["indexdef"].encode()),
            }
            for row in selected
        ],
        "rows_by_embedding_model": [
            {"model": row["embedding_model"], "count": row["count"]} for row in models
        ],
        "dev_user_counts": {
            "count": len(values),
            "min": min(values),
            "median": statistics.median(values),
            "max": max(values),
            "per_user": by_user,
        },
    }
    return raw, clean


async def plan_audit(
    reader: Any,
    controls: LocalSettings,
    queries: Mapping[str, Mapping[str, CapturedQuery]],
    qids: Sequence[str],
) -> tuple[list[Any], list[Any], list[str]]:
    raw, clean, differences = [], [], []
    for qid in qids:
        for kind in ("vector", "bm25"):
            summaries = {}
            for mode in ("harness", "probes10"):
                async with controls.mode(mode):
                    plan, summary = await queries[qid][kind].explain(reader)
                summaries[mode] = summary
                raw.append({"question_id": qid, "kind": kind, "mode": mode, "plan": plan})
                clean.append({"question_id": qid, "kind": kind, "mode": mode, **summary})
            if summaries["harness"]["shape_sha256"] != summaries["probes10"]["shape_sha256"]:
                differences.append(f"{qid}:{kind}")
    return raw, clean, differences


def ranks(rows: Sequence[Mapping[str, Any]], targets: Sequence[str]) -> dict[str, int | None]:
    positions = {str(row["id"]): pos for pos, row in enumerate(rows, 1)}
    return {mid: positions.get(mid) for mid in targets}


def recall(
    reference: Sequence[Mapping[str, Any]], current: Sequence[Mapping[str, Any]], k: int
) -> float:
    gold = {str(row["id"]) for row in reference[:k]}
    return len(gold & {str(row["id"]) for row in current[:k]}) / len(gold) if gold else 1.0


async def ranking_audit(
    reader: Any,
    controls: LocalSettings,
    queries: Mapping[str, CapturedQuery],
    targets: Sequence[str],
    *,
    verify_probes100: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    async with controls.mode("exact"):
        vector_query = queries["vector"].with_limit(None)
        reference = await vector_query.fetch(reader)
        raw_plan, plan = await vector_query.explain(reader)
        if plan["indexes"]:
            raise ValueError("Exact reference still uses an index")
        lexical = await queries["bm25"].with_limit(None).fetch(reader)
    approximations = []
    verified = False
    for label, probes in (("harness", controls.probes), ("probes10", 10), ("probes100", 100)):
        async with controls.mode("harness", probes=probes):
            for limit in (10, 100):
                rows = await queries["vector"].with_limit(limit).fetch(reader)
                approximations.append(
                    {
                        "mode": label,
                        "probes": probes,
                        "limit": limit,
                        "ids": [str(row["id"]) for row in rows],
                        "recall10": recall(reference, rows, 10),
                        "recall50": recall(reference, rows, 50),
                    }
                )
                if verify_probes100 and probes == 100 and limit == 100:
                    # Production SQL has no ID tie-break. Compare the returned
                    # ID->cosine mapping, not incidental ordering among ties.
                    expected = {str(row["id"]): row["similarity"] for row in reference[:100]}
                    actual = {str(row["id"]): row["similarity"] for row in rows}
                    if expected != actual:
                        raise ValueError("Exact reference differs from probes100 at LIMIT100")
                    verified = True
    return {"exact_plan": raw_plan}, {
        "exact_ids": [str(row["id"]) for row in reference],
        "exact_cosine_ranks": ranks(reference, targets),
        "bm25_ranks": ranks(lexical, targets),
        "exact_plan": plan,
        "probes100_verified": verified,
        "approximations": approximations,
    }


def grid_measure(
    gathered: Any,
    block: str,
    inputs: sweep.SweepInputs,
    entry: Mapping[str, Any],
    source_map: Mapping[str, str | None],
    config: rules.Configuration,
    baseline_rendered: set[str],
) -> dict[str, Any]:
    qid = entry["question_id"]
    with overrides(config.pool, config.k):
        metrics = exposure.measure(
            gathered,
            target_ids=inputs.targets.get(qid, []),
            gold_conversation_ids=inputs.gold[qid],
            source_conversations=source_map,
            ranking=gathered.retrieved,
        )
        if injection.render_memory_context(gathered) != block:
            raise ValueError("Production gather/render self-check failed")
        rows = exposure.rendered_memory_rows(gathered, block)
        added = [
            {"id": row["id"], "content": text}
            for row, text in zip(rows, exposure.rendered_memory_texts(block), strict=True)
            if str(row["id"]) not in baseline_rendered
        ]
    metrics["block_sha256"] = sweep.digest_bytes(block.encode())
    metrics["distractors"] = (
        exposure.distractor_counts(entry["question"], added)
        if qid.endswith("_abs") or qid in sweep.ABSTENTION_OVERREACH
        else None
    )
    return metrics


async def mismatch_audit(
    store: Any,
    reader: RecordingReader,
    controls: LocalSettings,
    inputs: sweep.SweepInputs,
    vectors: Mapping[str, Any],
) -> tuple[list[Any], list[Any]]:
    raw, clean = [], []
    for prefix in MISMATCH_MEMORY_PREFIXES:
        matches = [
            (qid, mid)
            for qid, mids in inputs.targets.items()
            for mid in mids
            if mid.startswith(prefix)
        ]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one mismatch target: {prefix}")
        qid, mid = matches[0]
        entry = next(row for row in inputs.dataset if row["question_id"] == qid)
        for mode in ("harness", "exact"):
            async with controls.mode(mode):
                block, gathered, gather_queries, gather_scores = await gather(
                    store,
                    reader,
                    inputs,
                    entry,
                    vectors[qid][0],
                    pool=10,
                    k=20,
                )
                clock = retrieval.RECENCY_REFERENCE_TIME.set(question_as_of(entry))
                start = len(reader.calls)
                try:
                    with capture_components() as rank_scores:
                        ranked = await retrieval.retrieve_memories_for_text(
                            store=store,
                            query_text=sweep.parity_query_text(entry["question"]),
                            user_id=question_user_id(qid),
                            query_embedding=vectors[qid][0],
                            limit=50,
                            include_local=False,
                            storage_embedding_model=inputs.storage_model,
                            query_embedding_model=inputs.model,
                            touch_results=False,
                            retrieval_triggered_by="longmemeval",
                            query_reference_time=question_as_of(entry),
                        )
                finally:
                    retrieval.RECENCY_REFERENCE_TIME.reset(clock)
                rank_queries = reader.queries(start)
            with overrides(10, 20):
                visible = {str(row["id"]) for row in exposure.rendered_memory_rows(gathered, block)}
            components = {}
            for label, scores in (("gather20", gather_scores), ("rank50", rank_scores)):
                found = next((row for row in scores if str(row["id"]) == mid), None)
                components[label] = (
                    {name: found.get(name) for name in rules.SCORER_COMPONENTS} if found else None
                )
            clean.append(
                {
                    "question_id": qid,
                    "memory_id": mid,
                    "mode": mode,
                    "rank50": ranks(ranked, [mid])[mid],
                    "gather20_rank": ranks(gathered.retrieved, [mid])[mid],
                    "gather20_rendered": mid in visible,
                    "components": components,
                    "gather_vector_limit": gather_queries["vector"].args[5],
                    "rank_vector_limit": rank_queries["vector"].args[5],
                    "explanation": "unexplained",
                }
            )
            raw.append(
                {
                    "question_id": qid,
                    "memory_id": mid,
                    "mode": mode,
                    "gather20_block": block,
                    "gather20_scores": gather_scores,
                    "rank50_scores": rank_scores,
                    "gather_queries": [vars(query) for query in gather_queries.values()],
                    "rank_queries": [vars(query) for query in rank_queries.values()],
                }
            )
    return raw, clean


async def audit_snapshot(
    reader: RecordingReader,
    store: Any,
    inputs: sweep.SweepInputs,
    controls: LocalSettings,
    connection_probes: Mapping[str, int],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Baseline all 50 FIRST; no other grid result survives a failed self-check."""
    if retrieval.INITIAL_VECTOR_CANDIDATES != 10 or injection.MAX_MEMORY_ITEMS != 5:
        raise ValueError("Production constants must start at pool10/k5")
    sweep.verify_embedding_context(inputs)
    users = {
        entry["question_id"]: question_user_id(entry["question_id"]) for entry in inputs.dataset
    }
    if len(users) != exposure.QUESTION_COUNT or len(inputs.targets) != exposure.CASE_COUNT:
        raise ValueError("Require 50 dev questions and five target cases")
    if inputs.vector_from != VECTOR_FROM or inputs.vector_until != VECTOR_UNTIL:
        raise ValueError("Require the exact pilot A1 vector window")
    if set(inputs.pilot_hashes) != set(users):
        raise ValueError("Require all 50 pilot block hashes")
    before = await fingerprint_benchmark_users(reader, list(users.values()))
    if before["sha256"] != inputs.fingerprint:
        raise ValueError("Before fingerprint mismatch")
    raw_facts, facts = await execution_facts(reader, list(users.values()))
    source_rows = await reader.fetch(
        "SELECT id, user_id, source_conversation_id FROM memories WHERE user_id = ANY($1::uuid[])",
        list(users.values()),
    )
    owners = {str(row["id"]): str(row["user_id"]) for row in source_rows}
    source_map = {
        str(row["id"]): str(row["source_conversation_id"])
        if row["source_conversation_id"]
        else None
        for row in source_rows
    }
    if any(
        owners.get(mid) != str(users[qid]) for qid, mids in inputs.targets.items() for mid in mids
    ):
        raise ValueError("Missing or foreign target memory")
    logged = await reader.fetch(
        "SELECT id::text AS log_id, user_id, query_text, query_embedding_model,"
        " query_embedding::text AS query_embedding, created_at FROM retrieval_log"
        " WHERE user_id = ANY($1::uuid[]) ORDER BY user_id, created_at, id",
        list(users.values()),
    )
    vectors = {
        entry["question_id"]: sweep.select_logged_vector(
            logged,
            user_id=users[entry["question_id"]],
            text=sweep.parity_query_text(entry["question"]),
            model=inputs.model,
            dimensions=inputs.dimensions,
            vector_from=inputs.vector_from,
            vector_until=inputs.vector_until,
        )
        for entry in inputs.dataset
    }
    questions: dict[str, dict[str, Any]] = {}
    raw_questions: dict[str, dict[str, Any]] = {}
    captured: dict[str, dict[str, CapturedQuery]] = {}
    exact_scores: dict[str, dict[str, list[dict[str, Any]]]] = {qid: {} for qid in users}
    exact_retrieved: dict[str, set[str]] = {qid: set() for qid in users}
    controls.probes = connection_probes["harness"]
    with sweep.no_providers() as guard:
        async with controls.mode("harness"):
            for entry in inputs.dataset:
                qid = entry["question_id"]
                block, gathered, queries, _ = await gather(
                    store,
                    reader,
                    inputs,
                    entry,
                    vectors[qid][0],
                    pool=10,
                    k=5,
                )
                metrics = grid_measure(
                    gathered, block, inputs, entry, source_map, rules.BASELINE, set()
                )
                if metrics["distractors"] is not None:
                    metrics["distractors"] = {"added": 0, "plausible_support": 0}
                vector, log_ids, comparison = vectors[qid]
                questions[qid] = {
                    "question_id": qid,
                    "configs": {rules.BASELINE.key: metrics},
                    "source_conversations": {
                        mid: source_map[mid]
                        for mid, owner in owners.items()
                        if owner == str(users[qid])
                    },
                    "vector_log_ids": log_ids,
                    **comparison,
                    "vector_sha256": sweep.digest_bytes(
                        json.dumps(vector, separators=(",", ":")).encode()
                    ),
                    "pilot_match": metrics["block_sha256"] == inputs.pilot_hashes[qid],
                }
                captured[qid] = queries
                raw_questions[qid] = {
                    "configs": {rules.BASELINE.key: {"memory_context": block}},
                    "queries": [vars(query) for query in queries.values()],
                }
        if not all(question["pilot_match"] for question in questions.values()):
            matches = sum(question["pilot_match"] for question in questions.values())
            raise ValueError(
                f"Baseline pilot block mismatch: {matches}/50; refusing remaining audit"
            )
        plan_qids = []
        for prefix in PLAN_QUESTION_PREFIXES:
            matches = [qid for qid in users if qid.startswith(prefix)]
            if len(matches) != 1:
                raise ValueError(f"Expected one plan question: {prefix}")
            plan_qids.extend(matches)
        plan_qids.append(next(qid for qid in users if qid not in plan_qids))
        raw_plans, plans, differences = await plan_audit(reader, controls, captured, plan_qids)
        t1 = []
        for pos, entry in enumerate(inputs.dataset):
            qid = entry["question_id"]
            raw_rank, clean_rank = await ranking_audit(
                reader,
                controls,
                captured[qid],
                inputs.targets.get(qid, []),
                verify_probes100=pos < 5,
            )
            raw_questions[qid]["ranking"] = raw_rank
            t1.append({"question_id": qid, **clean_rank})
        configs = rules.configurations(connection_probes["harness"])
        for config in configs:
            if config == rules.BASELINE:
                continue
            async with controls.mode(config.mode):
                for entry in inputs.dataset:
                    qid = entry["question_id"]
                    block, gathered, _, scored = await gather(
                        store,
                        reader,
                        inputs,
                        entry,
                        vectors[qid][0],
                        pool=config.pool,
                        k=config.k,
                    )
                    questions[qid]["configs"][config.key] = grid_measure(
                        gathered,
                        block,
                        inputs,
                        entry,
                        source_map,
                        config,
                        set(questions[qid]["configs"][rules.BASELINE.key]["rendered_ids"]),
                    )
                    raw_questions[qid]["configs"][config.key] = {
                        "memory_context": block,
                        "scored_candidates": scored,
                    }
                    if config.mode == "exact":
                        exact_retrieved[qid].update(str(row["id"]) for row in gathered.retrieved)
                        if config.k == 10:
                            exact_scores[qid][str(config.pool)] = scored
        stability = [
            {
                "question_id": qid,
                **rules.pool_stability(
                    {
                        pool: [row for row in scores if str(row["id"]) in exact_retrieved[qid]]
                        for pool, scores in exact_scores[qid].items()
                    }
                ),
            }
            for qid in users
        ]
        raw_mismatches, mismatches = await mismatch_audit(store, reader, controls, inputs, vectors)
        after = await fingerprint_benchmark_users(reader, list(users.values()))
        if before != after or after["sha256"] != inputs.fingerprint:
            raise ValueError("After fingerprint mismatch")
        if (
            guard.attempted_calls
            or retrieval.INITIAL_VECTOR_CANDIDATES != 10
            or injection.MAX_MEMORY_ITEMS != 5
        ):
            raise ValueError("Provider/override restoration invariant failed")
    summaries = {
        config.key: rules.summarize_grid(list(questions.values()), config) for config in configs
    }
    largest = rules.Configuration(100, "exact", 10).key
    missing_ranks = [
        {
            "question_id": qid,
            "memory_id": mid,
            "exact_cosine_rank": next(row for row in t1 if row["question_id"] == qid)[
                "exact_cosine_ranks"
            ][mid],
            "bm25_rank": next(row for row in t1 if row["question_id"] == qid)["bm25_ranks"][mid],
        }
        for qid, mids in inputs.targets.items()
        for mid in mids
        if not questions[qid]["configs"][largest]["targets"][mid]["rendered"]
    ]
    decision = rules.decide_exactness(
        summaries,
        connection_probes=connection_probes,
        plan_shape_differences=differences,
        missing_target_ranks=missing_ranks,
    )
    decision["flags"] = sorted(decision["flags"])
    aggregate_recall = {
        f"{mode}_{limit}": {
            name: statistics.mean(
                approximation[name]
                for row in t1
                for approximation in row["approximations"]
                if approximation["mode"] == mode and approximation["limit"] == limit
            )
            for name in ("recall10", "recall50")
        }
        for mode in ("harness", "probes10", "probes100")
        for limit in (10, 100)
    }
    raw = {
        "execution_facts": raw_facts,
        "plans": raw_plans,
        "questions": raw_questions,
        "mismatches": raw_mismatches,
        "settings_statements": controls.statements,
    }
    clean = {
        "schema": "daemon-memory-retrieval-exactness/1",
        "input_shas": inputs.shas,
        "fingerprint_before": before["sha256"],
        "fingerprint_after": after["sha256"],
        "provider_calls": 0,
        "embedding_calls": 0,
        "chat_calls": 0,
        "provider_cost_usd": "0",
        "transaction": "read_only_repeatable_read",
        "clock": "question_as_of_and_RECENCY_REFERENCE_TIME",
        "vector_window": {
            "from": inputs.vector_from.isoformat(),
            "until": inputs.vector_until.isoformat(),
        },
        "pool_constant": "INITIAL_VECTOR_CANDIDATES",
        "separate_bm25_pool_constant": None,
        "pool_start": 10,
        "pool_end": retrieval.INITIAL_VECTOR_CANDIDATES,
        "width_start": 5,
        "width_end": injection.MAX_MEMORY_ITEMS,
        "connection_probes": dict(connection_probes),
        "execution_facts": facts,
        "captured_sql": {
            qid: [
                {
                    "kind": query.kind,
                    "sql_sha256": sweep.digest_bytes(query.sql.encode()),
                    "parameters_sha256": sweep.digest_bytes(
                        json.dumps(query.args, default=str).encode()
                    ),
                }
                for query in queries.values()
            ]
            for qid, queries in captured.items()
        },
        "plans": plans,
        "plan_shape_differences": differences,
        "pilot_matches": 50,
        "pilot_total": 50,
        "pilot_provenance": inputs.pilot_provenance,
        "grid_count": len(configs),
        "collapsed_probes10": connection_probes["harness"] == 10,
        "questions": list(questions.values()),
        "rankings": t1,
        "recall_means": aggregate_recall,
        "probes100_verifications": sum(row["probes100_verified"] for row in t1),
        "summaries": summaries,
        "stability": stability,
        "mismatches": mismatches,
        "missing_target_ranks": missing_ranks,
        "decision": decision,
        "settings_statement_count": len(controls.statements),
        "settings_statements_sha256": sweep.digest_bytes(json.dumps(controls.statements).encode()),
    }
    sweep.validate_sanitized(clean)
    return raw, clean


async def connection_probe(pool: Any) -> int:
    try:
        async with pool.acquire() as connection:
            async with readonly_snapshot(connection) as controls:
                return controls.probes
    finally:
        await pool.close()


async def run_live(
    inputs: sweep.SweepInputs, *, concurrency: int = 1
) -> tuple[dict[str, Any], dict[str, Any]]:
    if concurrency < 1:
        raise ValueError("Invalid harness concurrency")
    with sweep.no_providers():
        settings = get_settings()
        if settings.retrieval_logging_enabled or settings.retrieval_logging_debug:
            raise ValueError("Retrieval logging must be disabled")
        check_benchmark_dsn(settings.database_url)
        # Source-equivalent recipes ONLY. Never invoke Campaign/restored_pool or
        # FactHarness._phase (they mutate admission, manifests and attestations).
        harness = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=2,
            max_size=max(10, 4 * concurrency + 2),
        )
        harness_probes = await connection_probe(harness)
        campaign = await asyncpg.create_pool(
            settings.database_url,
            min_size=10,
            max_size=10,
            max_queries=1000000,
            max_inactive_connection_lifetime=0,
            timeout=10,
            command_timeout=30,
        )
        campaign_probes = await connection_probe(campaign)
        connection = await asyncpg.connect(
            settings.database_url,
            timeout=10,
            server_settings={"default_transaction_read_only": "on", "statement_timeout": "30000"},
        )
        try:
            async with readonly_snapshot(connection) as controls:
                probes = {
                    "harness": harness_probes,
                    "campaign": campaign_probes,
                    "sweep": controls.probes,
                }
                reader = RecordingReader(connection)
                store = MemoryStore(
                    cast(asyncpg.Pool, reader), ContentEncryption(settings.daemon_encryption_key)
                )
                raw, clean = await audit_snapshot(reader, store, inputs, controls, probes)
                clean["connection_recipes"] = {
                    "harness": {
                        "min_size": 2,
                        "max_size": max(10, 4 * concurrency + 2),
                        "init": None,
                        "concurrency": concurrency,
                    },
                    "campaign": {
                        "min_size": 10,
                        "max_size": 10,
                        "max_queries": 1000000,
                        "max_inactive_connection_lifetime": 0,
                        "timeout": 10,
                        "command_timeout": 30,
                        "init": None,
                        "recipe_only": True,
                    },
                    "sweep": {
                        "timeout": 10,
                        "default_transaction_read_only": "on",
                        "statement_timeout": 30000,
                    },
                }
                return raw, clean
        finally:
            await connection.close()


def render_report(data: Mapping[str, Any]) -> str:
    sweep.validate_sanitized(dict(data))
    decision = rules.decide_exactness(
        data["summaries"],
        connection_probes=data["connection_probes"],
        plan_shape_differences=data["plan_shape_differences"],
        missing_target_ranks=data["missing_target_ranks"],
    )
    decision["flags"] = sorted(decision["flags"])
    if decision != data["decision"]:
        raise ValueError("Saved decision differs from decide_exactness()")
    lines = [
        "# Memory retrieval exactness and candidate-pool audit — 2026-11-03",
        "",
        "Diagnostic only; not a candidate or campaign authorization. Zero provider calls and cost.",
        "No restore, admission, DDL, ANALYZE or session SET. Settings use SET LOCAL within read-only repeatable-read transactions.",
        "The three connection recipes are sampled serially. The campaign recipe is reproduced without invoking campaign restore/admission.",
        "Fingerprint equality inside one snapshot does not detect concurrent commits outside that snapshot.",
        f"Git: `{data['git_sha']}`. Raw SHA256: `{data['raw_sha256']}`.",
        f"Fingerprint before: `{data['fingerprint_before']}`; after: `{data['fingerprint_after']}`.",
        "Historical question_as_of and RECENCY_REFERENCE_TIME; logged vectors selected by exact question/model match, exactly one in the pinned pilot A1 window.",
        f"Window: `{data['vector_window']['from']}` through `{data['vector_window']['until']}` (inclusive).",
        "Pool override: INITIAL_VECTOR_CANDIDATES, shared by vector and BM25. There is no separate BM25 constant. Source constants remain pool=10 and MAX_MEMORY_ITEMS=5.",
        f"Baseline pilot rendered-block hash matches: {data['pilot_matches']}/{data['pilot_total']}; failure aborts before the rest of the grid.",
        "",
        "## Input pins",
        "",
        "```json",
        json.dumps(data["input_shas"], indent=2, sort_keys=True),
        "```",
        "",
        "## T0 — execution facts",
        "",
    ]
    for key in (
        "connection_probes",
        "connection_recipes",
        "execution_facts",
        "plans",
        "plan_shape_differences",
    ):
        lines.extend(
            [
                f"### {key}",
                "",
                "```json",
                json.dumps(data[key], indent=2, sort_keys=True),
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## T1 — exact versus approximate ranking",
            "",
            "Exactness retains the captured production eligibility SQL and disables index, index-only and bitmap scans locally. Plans are checked for absence of IVFFlat.",
            "LIMIT NULL exhausts eligible rows without rewriting filters. Exact ranks retain the SQL's unspecified ordering among cosine ties. Probes100 checks compare ID/cosine mappings at LIMIT100; boundary ties may conservatively stop the audit.",
            f"Exact/probes100 verifications: {data['probes100_verifications']}. Recall denominator is min(k, exact eligible rows); an empty reference has recall 1. LIMIT10 recall@50 cannot exceed 10/50 when at least 50 rows qualify.",
            "",
            "### Aggregate recall",
            "",
            "```json",
            json.dumps(data["recall_means"], indent=2, sort_keys=True),
            "```",
            "",
            "| Question | Mode | LIMIT | Recall@10 | Recall@50 |",
            "|---|---|---|---|---|",
        ]
    )
    for row in data["rankings"]:
        for approximation in row["approximations"]:
            lines.append(
                f"| {row['question_id']} | {approximation['mode']} | {approximation['limit']} | {approximation['recall10']} | {approximation['recall50']} |"
            )
    lines.extend(
        [
            "",
            "### Named target ranks",
            "",
            "| Question | Memory | Exact cosine rank | BM25 rank |",
            "|---|---|---|---|",
        ]
    )
    for row in data["rankings"]:
        for mid, rank in row["exact_cosine_ranks"].items():
            lines.append(f"| {row['question_id']} | {mid} | {rank} | {row['bm25_ranks'][mid]} |")
    lines.extend(
        [
            "",
            "## T2 — production gather/render grid",
            "",
            f"Configurations: {data['grid_count']}; duplicate probes10 collapsed: {data['collapsed_probes10']}.",
            "Coverage and COVERAGE_REGRESSION causes reuse exposure.py unchanged. Gold-session coverage is a proxy, not answer-fact exposure. Abstention distractors reuse the sweep rule and are warnings, not decision vetoes.",
            "",
            "| Configuration | Strict /5 | Pre-budget /5 | Coverage losses | Target drops | Total drops | Tokens | Added distractors | Plausible support |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for key, row in data["summaries"].items():
        lines.append(
            f"| {key} | {row['cases_strict']} | {row['cases_pre_budget']} | {len(row['coverage_losses'])} | {row['target_budget_dropped']} | {row['budget_dropped']} | {row['rendered_tokens']} | {row['distractors']['added']} | {row['distractors']['plausible_support']} |"
        )
    lines.extend(
        [
            "",
            "### Coverage-loss attribution",
            "",
            "```json",
            json.dumps(
                {key: row["coverage_causes"] for key, row in data["summaries"].items()},
                indent=2,
                sort_keys=True,
            ),
            "```",
            "",
            "## T3 — exact-search pool stability",
            "",
            "The fixed union is the memories returned across exact-search pools/widths. Components come directly from scored_candidates_out, never a reimplementation. A union member absent from a pool has no observed score there: full-union order is explicitly unknown, not imputed. Pairwise common-set order changes and changed components are reported independently; correlated component changes are not causal proof.",
            "",
            "```json",
            json.dumps(data["stability"], indent=2, sort_keys=True),
            "```",
            "",
            "## Gather20 versus rank50 mismatches",
            "",
            "0770505d and 07c6d26d: unexplained unless primary source/data adjudication establishes a cause. Dedicated harness/exact passes below retain production scores and actual vector limits (gather20 versus rank50), exposing pool membership/scoring differences without claiming causation from correlation.",
            "",
            "```json",
            json.dumps(data["mismatches"], indent=2, sort_keys=True),
            "```",
            "",
            "## Decision — decide_exactness()",
            "",
            "Sort key: (n_changed, priority_vector padded with 3, fixed probes/pool/width step_vector). Priorities probes=0/pool=1/width=2. Steps harness=0/probes10=1/exact=2; pool10=0/25=1/50=2/100=3; width5=0/10=1. Duplicate probes10 has step 0 when harness probes=10 and is dropped.",
            "NONE_OF_THE_ABOVE is retained as a safety net but is unreachable on a complete valid grid: exact/pool100/k10 necessarily triggers query formulation, candidate, coverage regression or budget limited.",
            "",
            "```json",
            json.dumps(decision, indent=2, sort_keys=True),
            "```",
            "",
            "## Primary notes",
            "",
            "<!-- Primary: add interpretation and remaining limitations after the live audit. -->",
            "",
        ]
    )
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    result = sweep.parser()
    result.description = __doc__
    result.add_argument("--harness-concurrency", type=int, default=1)
    result.set_defaults(report_out=REPORT)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.prior_evidence is not None:
        raise ValueError("This audit does not use --prior-evidence")
    sweep.validate_output_paths(args.raw_out, args.sanitized_out, args.report_out)
    inputs = sweep.prepare_inputs(args)
    if not inputs.fingerprint.startswith(FINGERPRINT_PREFIX):
        raise ValueError("Pinned snapshot must be 5cb6ab14…")
    if inputs.vector_from != VECTOR_FROM or inputs.vector_until != VECTOR_UNTIL:
        raise ValueError("Require the exact pilot A1 vector window")
    if args.harness_concurrency < 1:
        raise ValueError("Invalid harness concurrency")
    if args.dry_run:
        print("Dry-run: input pins verified; no DB/provider access or output")
        return 0
    raw, clean = asyncio.run(run_live(inputs, concurrency=args.harness_concurrency))
    clean["git_sha"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=sweep.ROOT,
        text=True,
    ).strip()
    sweep.write_outputs(args.raw_out, args.sanitized_out, raw, clean)
    saved = json.loads(args.sanitized_out.read_text())
    sweep.write_new_file(args.report_out, render_report(saved).encode())
    print(f"Completed: zero provider calls; pilot 50/50; {','.join(clean['decision']['flags'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
