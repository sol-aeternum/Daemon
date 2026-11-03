"""Offline-tested exposure gate. Only the primary may execute generate/measure live.

No answers, judges, restore, index changes, persistent settings or ANALYZE.
Generation is blind: only question and as-of date reach generate_request().
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import statistics
import subprocess
import time
from typing import Any, cast
from unittest.mock import patch

import asyncpg

from orchestrator import compute_runtime
from orchestrator.config import get_settings
from orchestrator.entitlements.policy import load_inference_policy
from orchestrator.eval import decomposition as rules
from orchestrator.eval import decomposition_generation as generation
from orchestrator.eval import exposure
from orchestrator.eval.bench_db import (
    check_benchmark_dsn,
    fingerprint_benchmark_users,
    question_user_id,
)
from orchestrator.eval.fact_harness import question_as_of
from orchestrator.memory import embedding, embedding_adapter, injection, retrieval
from orchestrator.memory.embedding import EmbeddingVectorResult
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from scripts import memory_exposure_sweep as sweep
from scripts import memory_retrieval_exactness as exactness
from tests.longmemeval import evaluate

ROOT = sweep.ROOT
FROZEN = ROOT / "tests/benchmark_results" / rules.FROZEN_NAME
REPORT = FROZEN.with_name("memory_query_decomposition_2026-11-03.md")
EVIDENCE = REPORT.with_name(REPORT.stem + "_evidence.json")
EMBEDDING_ROUTE = "azure-openrouter-small-1024"
WRITE_ALLOWLIST = {
    "public.entitlement_reservations": "orchestrator/entitlements/store.py:642,710",
    "public.entitlement_usage_periods": "orchestrator/entitlements/store.py:88,168,383",
    "public.entitlement_accounts": "orchestrator/entitlements/store.py:198,217;orchestrator/entitlements/service.py:908",
    "public.retrieval_log": "orchestrator/memory/store.py:3624",
}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)  # nosec B603 B607 - fixed executable, local git arguments


def require_committed(path: Path) -> None:
    relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
    if git("show", f"HEAD:{relative}") != path.read_bytes():
        raise ValueError("Input differs from committed bytes")


def freeze_check() -> str:
    expected = rules.frozen_design()
    if FROZEN.read_bytes() != expected:
        raise ValueError("Frozen design differs from in-code design")
    require_committed(FROZEN)
    if git("status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("Require a clean committed git tree")
    return sweep.digest_bytes(expected)


def verify_chat_route() -> Any:
    policy = load_inference_policy()
    route = policy.routes.get(generation.ROUTE_ID)
    if route is None or not route.is_approved(policy.requirements):
        raise ValueError("Decomposition route unapproved")
    expected = {
        "only": ["azure/eu"],
        "order": ["azure/eu"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "data_collection": "deny",
        "zdr": True,
    }
    actual = route.transport_payload(policy.requirements)["extra_body"]["provider"]
    if (
        route.route_id != generation.ROUTE_ID
        or route.model != generation.MODEL
        or route.provider != "openrouter"
        or route.endpoint != embedding_adapter.ENDPOINT
        or actual != expected
        or route.price_ceiling is None
    ):
        raise ValueError("Decomposition route/pinning/ZDR mismatch")
    return route


def receipt_dict(response: Any) -> dict[str, Any]:
    value = response if isinstance(response, dict) else response.model_dump(mode="json")
    if not isinstance(value, dict):
        raise ValueError("Missing provider receipt")
    return value


def reported_cost(receipt: Mapping[str, Any]) -> Decimal:
    raw = receipt.get("usage", {}).get("cost")
    if type(raw) not in (str, float, int):
        raise ValueError("Provider-reported cost missing")
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        raise ValueError("Invalid provider-reported cost") from None
    if not value.is_finite() or value < 0:
        raise ValueError("Invalid provider-reported cost")
    return value


class Accounting:
    """Reported costs plus conservative unknown-send ceilings bound all attempts.

    Chat ceiling USD = ceil((B*prompt_rate + max_tokens*completion_rate)/1e6)/1e6,
    B = production _request_bound(request).bound. Rates are micro-USD per 1M.
    Embedding ceiling uses the production UTF-8-byte input_bound and input rate.
    A missing/error receipt retains its full ceiling, never an invented zero.
    """

    def __init__(self, records: Sequence[Mapping[str, Any]] = ()):
        self.records = [dict(row) for row in records]

    @property
    def total(self) -> Decimal:
        return sum((Decimal(row["charged_bound_usd"]) for row in self.records), Decimal(0))

    def before(self, ceiling: Decimal) -> None:
        if (
            not ceiling.is_finite()
            or ceiling < 0
            or self.total + ceiling > Decimal(rules.COST_CAP_USD)
        ):
            raise ValueError("USD 0.10 cumulative worst-case cap would be exceeded")

    def add(
        self,
        *,
        kind: str,
        qid: str,
        ceiling: Decimal,
        started: float,
        receipt: dict[str, Any] | None,
        status: str,
    ) -> None:
        cost_error = None
        try:
            actual = reported_cost(receipt) if receipt is not None else None
        except ValueError as error:
            actual, cost_error = None, error
        self.records.append(
            {
                "kind": kind,
                "question_id": qid,
                "status": status,
                "latency_ms": (time.perf_counter() - started) * 1000,
                "ceiling_usd": str(ceiling),
                "cost_usd": str(actual) if actual is not None else None,
                "charged_bound_usd": str(actual if actual is not None else ceiling),
                "receipt": receipt,
            }
        )
        if cost_error is not None:
            raise ValueError(f"Provider-reported cost unavailable: {qid}") from cost_error
        if actual is not None and actual > ceiling:
            raise ValueError(f"Provider cost exceeds request ceiling: {qid}")
        if self.total > Decimal(rules.COST_CAP_USD):
            raise ValueError("Provider cost exceeded cumulative cap")


def chat_ceiling(request: dict[str, Any], route: Any) -> Decimal:
    tokens = compute_runtime._request_bound(request).bound
    return Decimal(route.estimate_microusd(tokens, request["max_tokens"])) / 1_000_000


@contextmanager
def no_answers_or_judges() -> Iterator[None]:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise sweep.ProviderAccessDenied("Answer/judge transport construction or dispatch refused")

    # The benchmark's shared answer/judge transport and runner construction are
    # forbidden even during generation; the only LLM path is guarded_completion.
    from orchestrator.eval.fact_harness import LongMemEvalFactRunner

    with (
        patch.object(evaluate, "_call_llm_with_provider_config", refuse),
        patch.object(LongMemEvalFactRunner, "__init__", refuse),
    ):
        yield


async def generate_one(
    question: str, as_of: str, qid: str, accounting: Accounting, dispatch: Any = None
) -> dict[str, Any]:
    request = generation.generate_request(question, as_of)
    request_bytes = rules.canonical(request)
    route = verify_chat_route()
    ceiling = chat_ceiling(request, route)
    retries_429 = parse_retries = attempts = 0
    while True:
        if rules.canonical(request) != request_bytes:
            raise ValueError("Retry request mutated")
        verify_chat_route()
        accounting.before(ceiling)
        started = time.perf_counter()
        attempts += 1
        try:
            response = await (dispatch or compute_runtime.guarded_completion)(
                _route_id=generation.ROUTE_ID, _exact_model=True, **json.loads(request_bytes)
            )
        except compute_runtime.ComputeUnavailable as error:
            accounting.add(
                kind="chat",
                qid=qid,
                ceiling=ceiling,
                started=started,
                receipt=None,
                status=f"http_{error.status_code}" if error.status_code else "failed",
            )
            if error.status_code == 429 and retries_429 < rules.MAX_429_RETRIES:
                retries_429 += 1
                await asyncio.sleep(min(error.retry_after_seconds or 1, 60))
                continue
            raise ValueError(f"Generation dispatch stopped: {qid}") from error
        except Exception as error:
            accounting.add(
                kind="chat",
                qid=qid,
                ceiling=ceiling,
                started=started,
                receipt=None,
                status="failed",
            )
            raise ValueError(f"Generation dispatch stopped: {qid}") from error
        receipt = receipt_dict(response)
        accounting.add(
            kind="chat",
            qid=qid,
            ceiling=ceiling,
            started=started,
            receipt=receipt,
            status="received",
        )
        try:
            choices = receipt["choices"]
            if (
                len(choices) != 1
                or choices[0].get("finish_reason") != "stop"
                or choices[0]["message"].get("refusal")
                or choices[0]["message"].get("tool_calls")
            ):
                raise ValueError("Incomplete/refused generation")
            queries = generation.parse_queries(choices[0]["message"]["content"])
        except (KeyError, TypeError, ValueError) as error:
            accounting.records[-1]["status"] = "parse_failure"
            if parse_retries >= rules.MAX_PARSE_RETRIES:
                raise ValueError(f"Generation parse stopped: {qid}") from error
            parse_retries += 1
            continue
        return {
            "question_id": qid,
            "queries": queries,
            "request_sha256": sweep.digest_bytes(request_bytes),
            "attempts": attempts,
            "retries_429": retries_429,
            "parse_retries": parse_retries,
        }


async def table_stats(connection: Any) -> dict[str, list[int]]:
    await connection.execute("SELECT pg_stat_clear_snapshot()")
    rows = await connection.fetch(
        "SELECT schemaname, relname, n_tup_ins, n_tup_upd, n_tup_del FROM pg_stat_user_tables ORDER BY schemaname, relname"
    )
    return {
        f"{row['schemaname']}.{row['relname']}": [
            int(row[key]) for key in ("n_tup_ins", "n_tup_upd", "n_tup_del")
        ]
        for row in rows
    }


def check_table_writes(
    before: Mapping[str, Sequence[int]], after: Mapping[str, Sequence[int]]
) -> dict[str, list[int]]:
    if set(before) != set(after):
        raise ValueError("Table inventory changed during live phase")
    changed = {
        table: [right - left for left, right in zip(before[table], after[table], strict=True)]
        for table in before
        if before[table] != after[table]
    }
    if any(any(delta < 0 for delta in deltas) for deltas in changed.values()):
        raise ValueError("Table counters reset during live phase")
    forbidden = set(changed) - set(WRITE_ALLOWLIST)
    if forbidden:
        raise ValueError(f"Unapproved table writes: {','.join(sorted(forbidden))}")
    return changed


async def fingerprint(connection: Any, qids: Sequence[str], expected: str) -> str:
    actual = await fingerprint_benchmark_users(connection, [question_user_id(qid) for qid in qids])
    if not expected.startswith(exactness.FINGERPRINT_PREFIX) or actual["sha256"] != expected:
        raise ValueError("Benchmark fingerprint mismatch")
    return actual["sha256"]


def load_generation_inputs(args: argparse.Namespace) -> tuple[list[dict[str, Any]], str, str]:
    index = sweep.pinned_json(sweep.INDEX, sweep.PHASE3B_INDEX_SHA256)
    arm = index["arms"][args.phase3b_arm]
    dataset = sweep.pinned_json(args.dataset, arm["dataset_sha256"])
    if len(dataset) != 50 or len({row["question_id"] for row in dataset}) != 50:
        raise ValueError("Require 50 distinct dev questions")
    if sum(row["question_id"].endswith("_abs") for row in dataset) != 5:
        raise ValueError("Unexpected dev question identities")
    return dataset, arm["restore"]["fingerprint_sha256"], arm["dataset_sha256"]


def validate_oracle(
    path: Path, sha: str, targets: Mapping[str, Any] | None = None
) -> dict[str, list[str]]:
    require_committed(path)
    payload = sweep.pinned_json(path, sha)
    if payload.pop("label", None) != rules.ORACLE_LABEL or len(payload) != 5:
        raise ValueError("Require five explicitly contaminated oracle cases")
    if targets is not None and set(payload) != set(targets):
        raise ValueError("Oracle case identities differ from registered cases")
    return {qid: generation.validate_queries(queries) for qid, queries in payload.items()}


def validate_sanitized(value: dict[str, Any]) -> None:
    # The ONLY free-text exception is this root field, never a recursive key bypass.
    copied = dict(value)
    queries = copied.pop("sub_queries", {})
    if set(queries) != {"generated", "oracle"}:
        raise ValueError("Require explicitly allowlisted sub-query sources")
    for source in queries.values():
        if not isinstance(source, dict):
            raise ValueError("Invalid sub-query field")
        for qid, texts in source.items():
            sweep.validate_sanitized(qid)
            if generation.validate_queries(texts) != texts:
                raise ValueError("Sub-queries must be trimmed")
    sweep.validate_sanitized(copied)
    if len(rules.canonical(value)) > 512_000:
        raise ValueError("Sanitized evidence exceeds 500 KiB")


def write_outputs(
    raw_path: Path,
    clean_path: Path,
    report_path: Path | None,
    raw: dict[str, Any],
    clean: dict[str, Any],
) -> None:
    sweep.validate_output_paths(raw_path, clean_path, report_path)
    data = rules.canonical(raw)
    clean = {**clean, "raw_sha256": sweep.digest_bytes(data)}
    validate_sanitized(clean)
    report = render_report(clean) if report_path else None
    sweep.write_new_file(raw_path, data)
    sweep.write_new_file(clean_path, rules.canonical(clean))
    if report_path and report is not None:
        sweep.write_new_file(report_path, report.encode())


def percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    return ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * (
        index - lower
    )


def accounting_summary(accounting: Accounting) -> dict[str, Any]:
    records = accounting.records
    latencies = [row["latency_ms"] for row in records if row["kind"] == "chat"]
    costs = {
        qid: str(
            sum(
                (
                    Decimal(row["cost_usd"])
                    for row in records
                    if row["question_id"] == qid and row["cost_usd"] is not None
                ),
                Decimal(0),
            )
        )
        for qid in sorted({row["question_id"] for row in records})
    }
    return {
        "chat_attempts": sum(row["kind"] == "chat" for row in records),
        "embedding_calls": sum(row["kind"] == "embedding" for row in records),
        "provider_cost_usd": str(
            sum(
                (Decimal(row["cost_usd"]) for row in records if row["cost_usd"] is not None),
                Decimal(0),
            )
        ),
        "unknown_cost_attempts": sum(row["cost_usd"] is None for row in records),
        "charged_bound_usd": str(accounting.total),
        "generation_latency_ms_p50": statistics.median(latencies) if latencies else None,
        "generation_latency_ms_p95": percentile(latencies, 0.95),
        "cost_per_question_usd": costs,
        "embedding_calls_per_question": dict(
            Counter(row["question_id"] for row in records if row["kind"] == "embedding")
        ),
        "attempts": [
            {key: value for key, value in row.items() if key != "receipt"} for row in records
        ],
    }


def compact_questions(questions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep case target evidence, hashes and counts; full ID lists stay outside Git."""
    fields = (
        "block_sha256",
        "targets",
        "target_exposed",
        "target_pre_budget",
        "target_budget_dropped",
        "gold_session_coverage_proxy",
        "rendered_tokens",
        "effective_k",
        "budget_dropped",
        "duplicates",
        "distractors",
    )
    return [
        {
            "question_id": question["question_id"],
            "vector_log_ids": question["vector_log_ids"],
            "vector_sha256": question["vector_sha256"],
            "configs": {
                key: {field: row[field] for field in fields}
                for key, row in question["configs"].items()
            },
        }
        for question in questions
    ]


async def embed_subquery(text: str, qid: str, accounting: Accounting) -> list[float]:
    route = embedding_adapter.approved_route(EMBEDDING_ROUTE)
    ceiling = Decimal(route.estimate_microusd(embedding_adapter.input_bound(text))) / 1_000_000
    original = embedding_adapter._post
    received = False

    async def observed(
        client: Any, fresh: Any, texts: list[str], key: str, options: dict[str, Any]
    ) -> Any:
        nonlocal received
        if fresh != route or texts != [text] or received:
            raise sweep.ProviderAccessDenied("Unexpected embedding dispatch")
        accounting.before(ceiling)
        started = time.perf_counter()
        try:
            receipt = await original(client, fresh, texts, key, options)
        except BaseException:
            accounting.add(
                kind="embedding",
                qid=qid,
                ceiling=ceiling,
                started=started,
                receipt=None,
                status="failed",
            )
            raise
        received = True
        accounting.add(
            kind="embedding",
            qid=qid,
            ceiling=ceiling,
            started=started,
            receipt=receipt_dict(receipt),
            status="received",
        )
        return receipt

    accounting.before(ceiling)
    with patch.object(embedding_adapter, "_post", observed):
        result = await embedding.embed_query_with_metadata(text)
    if (
        not received
        or result.model != embedding_adapter.MODEL
        or result.storage_model != embedding_adapter.STORAGE_MODEL
        or len(result.embedding) != 1024
    ):
        raise ValueError(f"Embedding receipt/identity missing: {qid}")
    return result.embedding


async def measure_snapshot(
    reader: Any,
    store: Any,
    inputs: sweep.SweepInputs,
    generated: Mapping[str, list[str]],
    oracle: Mapping[str, list[str]],
    sub_vectors: Mapping[tuple[str, str, int], list[float]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    sweep.verify_embedding_context(inputs)
    users = {row["question_id"]: question_user_id(row["question_id"]) for row in inputs.dataset}
    await fingerprint(reader, list(users), inputs.fingerprint)
    sources = await reader.fetch(
        "SELECT id, user_id, source_conversation_id FROM memories WHERE user_id = ANY($1::uuid[])",
        list(users.values()),
    )
    source_map = {
        str(row["id"]): str(row["source_conversation_id"])
        if row["source_conversation_id"]
        else None
        for row in sources
    }
    owners = {str(row["id"]): str(row["user_id"]) for row in sources}
    for qid, targets in inputs.targets.items():
        if any(owners.get(mid) != str(users[qid]) for mid in targets):
            raise ValueError(f"Missing or foreign targets: {qid}")
    logged = await reader.fetch(
        "SELECT id::text AS log_id, user_id, query_text, query_embedding_model, query_embedding::text AS query_embedding, created_at FROM retrieval_log WHERE user_id = ANY($1::uuid[]) ORDER BY user_id, created_at, id",
        list(users.values()),
    )
    questions, raw_questions, attribution = [], [], []
    with sweep.no_providers():
        for entry in inputs.dataset:
            qid = entry["question_id"]
            text = sweep.parity_query_text(entry["question"])
            vector, log_ids, comparison = sweep.select_logged_vector(
                logged,
                user_id=users[qid],
                text=text,
                model=inputs.model,
                dimensions=inputs.dimensions,
                vector_from=inputs.vector_from,
                vector_until=inputs.vector_until,
            )
            result = EmbeddingVectorResult(
                embedding=vector,
                provider="azure-openrouter",
                model=inputs.model,
                storage_model=inputs.storage_model,
            )
            clock = retrieval.RECENCY_REFERENCE_TIME.set(question_as_of(entry))
            try:
                with sweep.cached_query(text, result):
                    block, gathered = await evaluate.build_parity_memory_context(
                        store,
                        user_id=users[qid],
                        question_text=entry["question"],
                        allowed_source_conversation_ids=None,
                        log_retrieval=False,
                        query_reference_time=question_as_of(entry),
                    )
                    if (
                        not gathered.retrieved
                        or gathered.retrieval_mode in {"failed", "none", "lexical"}
                        or sweep.digest_bytes(block.encode()) != inputs.pilot_hashes.get(qid)
                    ):
                        raise ValueError(f"Baseline pilot self-check failed: {qid}")
                    per_source = {}

                    async def retrieve(
                        query: str, query_vector: list[float]
                    ) -> list[dict[str, object]]:
                        return await retrieval.retrieve_memories_for_text(
                            store=store,
                            query_text=query,
                            user_id=users[qid],
                            query_embedding=query_vector,
                            limit=rules.DEPTH,
                            include_local=False,
                            storage_embedding_model=inputs.storage_model,
                            query_embedding_model=inputs.model,
                            touch_results=False,
                            retrieval_triggered_by="longmemeval",
                            query_reference_time=question_as_of(entry),
                        )

                    original_ranking = await retrieve(text, vector)
                    single = rules.substitute(gathered, rules.fuse([original_ranking], "rrf"), 5)
                    if injection.render_memory_context(single) != block:
                        raise ValueError(f"Single-query fusion baseline mismatch: {qid}")
                    for source, queries in (
                        ("generated", generated[qid]),
                        ("oracle", oracle.get(qid, [])),
                    ):
                        rankings = [original_ranking]
                        seen = {text}
                        query_indices = [0]
                        for index, query in enumerate(queries):
                            normalized = sweep.parity_query_text(query)
                            if normalized not in seen:
                                seen.add(normalized)
                                rankings.append(
                                    await retrieve(normalized, sub_vectors[(source, qid, index)])
                                )
                                query_indices.append(index + 1)
                        per_source[source] = rankings
                        if qid in inputs.targets:
                            for mid in inputs.targets[qid]:
                                attribution.append(
                                    {
                                        "question_id": qid,
                                        "memory_id": mid,
                                        "source": source,
                                        "query_indices": query_indices,
                                        "ranks": [
                                            next(
                                                (
                                                    pos
                                                    for pos, row in enumerate(ranking, 1)
                                                    if str(row["id"]) == mid
                                                ),
                                                None,
                                            )
                                            for ranking in rankings
                                        ],
                                    }
                                )
                    configs, raw_configs = {}, {}
                    baseline_rendered: set[str] = set()
                    for key in (rules.BASELINE, *rules.CONFIGS):
                        if key == rules.BASELINE:
                            current, ranking, k = gathered, original_ranking, 5
                        else:
                            # Merge names contain underscores; parse from known registration.
                            source, merge, k = next(
                                (source, merge, k)
                                for source in rules.SOURCES
                                for merge in rules.MERGES
                                for k in rules.WIDTHS
                                if rules.configuration(source, merge, k) == key
                            )
                            ranking = rules.fuse(per_source[source], merge)
                            current = rules.substitute(gathered, ranking, k)
                        with sweep.memory_width(k):
                            rendered_block = injection.render_memory_context(current)
                            metric = exposure.measure(
                                current,
                                target_ids=inputs.targets.get(qid, []),
                                gold_conversation_ids=inputs.gold[qid],
                                source_conversations=source_map,
                                ranking=ranking,
                            )
                            if qid in inputs.targets:
                                rules.check_case(qid, key, metric)
                            rendered_rows = exposure.rendered_memory_rows(current, rendered_block)
                            if key == rules.BASELINE:
                                baseline_rendered = set(metric["rendered_ids"])
                            metric["block_sha256"] = sweep.digest_bytes(rendered_block.encode())
                            metric["distractors"] = None
                            if qid.endswith("_abs") or qid in sweep.ABSTENTION_OVERREACH:
                                visible = [
                                    {"id": row["id"], "content": content}
                                    for row, content in zip(
                                        rendered_rows,
                                        exposure.rendered_memory_texts(rendered_block),
                                        strict=True,
                                    )
                                ]
                                metric["distractors"] = {
                                    **exposure.distractor_counts(
                                        entry["question"],
                                        [
                                            row
                                            for row in visible
                                            if str(row["id"]) not in baseline_rendered
                                        ],
                                    ),
                                    "own_plausible_support": exposure.distractor_counts(
                                        entry["question"], visible
                                    )["plausible_support"],
                                }
                            configs[key] = metric
                            raw_configs[key] = {
                                "memory_context": rendered_block,
                                "retrieved": current.retrieved,
                            }
                    relevant = {
                        mid
                        for row in configs.values()
                        for mid in row["pre_budget_ids"] + row["rendered_ids"]
                    }
                    questions.append(
                        {
                            "question_id": qid,
                            "configs": configs,
                            "source_conversations": {mid: source_map.get(mid) for mid in relevant},
                            "vector_log_ids": log_ids,
                            "vector_sha256": sweep.digest_bytes(rules.canonical(vector)),
                            **comparison,
                        }
                    )
                    raw_questions.append(
                        {"question_id": qid, "configs": raw_configs, "rankings": per_source}
                    )
            finally:
                retrieval.RECENCY_REFERENCE_TIME.reset(clock)
        await fingerprint(reader, list(users), inputs.fingerprint)
    decision = rules.decide_decomposition(questions)
    return {"questions": raw_questions}, {
        "questions": questions,
        "summaries": {
            key: rules.summarize(questions, key) for key in (rules.BASELINE, *rules.CONFIGS)
        },
        "decision": decision,
        "target_attribution": attribution,
        "pilot_matches": len(questions),
        "single_query_matches": len(questions),
    }


async def run_live(args: argparse.Namespace) -> None:
    frozen_sha = freeze_check()
    require_committed(args.oracle)
    if sweep.digest_bytes(sweep._input_bytes(args.oracle)) != args.oracle_sha:
        raise ValueError("Oracle preregistration hash mismatch")
    if args.command == "generate":
        dataset, expected, dataset_sha = load_generation_inputs(args)
        inputs = None
        prior = None
        oracle = None
        generated = {}
        accounting = Accounting()
    else:
        inputs = sweep.prepare_inputs(args)
        if (
            inputs.vector_from != exactness.VECTOR_FROM
            or inputs.vector_until != exactness.VECTOR_UNTIL
        ):
            raise ValueError("Require the exact v3 A1 vector window")
        dataset, expected, dataset_sha = inputs.dataset, inputs.fingerprint, inputs.shas["dataset"]
        prior = sweep.pinned_json(args.generation, args.generation_sha)
        if (
            prior["frozen_sha256"] != frozen_sha
            or prior["oracle_sha256"] != args.oracle_sha
            or prior["dataset_sha256"] != dataset_sha
            or prior["phase"] != "generate"
            or prior["fingerprint_before"] != expected
            or prior["fingerprint_after"] != expected
        ):
            raise ValueError("Generation provenance differs from preregistration")
        generated = {
            row["question_id"]: generation.validate_queries(row["queries"])
            for row in prior["questions"]
        }
        if len(prior["questions"]) != 50 or set(generated) != {
            entry["question_id"] for entry in dataset
        }:
            raise ValueError("Require exactly one generation per dev question")
        oracle = validate_oracle(args.oracle, args.oracle_sha, inputs.targets)
        accounting = Accounting(prior["receipts"])
        sweep.verify_embedding_context(inputs)
    if args.dry_run:
        print("Dry-run: committed pins checked; no DB/provider access")
        return
    settings = get_settings()
    check_benchmark_dsn(settings.database_url)
    if settings.retrieval_logging_enabled or settings.retrieval_logging_debug:
        raise ValueError("Retrieval logging must be disabled")
    if (
        embedding.get_selected_embedding_route_id() != EMBEDDING_ROUTE
        or embedding.get_configured_embedding_fallback_storage_models() != ()
    ):
        raise ValueError("Embedding route/fallback mismatch")
    verify_chat_route()
    # One writer backend permits forcing its own statistics flush after settlement.
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=1)
    observer = await asyncpg.connect(
        settings.database_url, server_settings={"default_transaction_read_only": "on"}
    )
    qids = [entry["question_id"] for entry in dataset]
    raw: dict[str, Any] = {}
    stats_before = None
    rows: list[dict[str, Any]] = []
    try:
        before = await fingerprint(observer, qids, expected)
        stats_before = await table_stats(observer)
        with no_answers_or_judges():
            if args.command == "generate":
                raw = {"phase": "generate", "questions": rows, "receipts": accounting.records}
                for entry in dataset:
                    qid = entry["question_id"]
                    async with compute_runtime.account_compute(
                        pool,
                        question_user_id(qid),
                        operation="chat",
                        background=True,
                        profile="background",
                    ):
                        rows.append(
                            await generate_one(
                                entry["question"],
                                question_as_of(entry).isoformat(),
                                qid,
                                accounting,
                            )
                        )
                generated = {row["question_id"]: row["queries"] for row in rows}
                clean: dict[str, Any] = {
                    "questions": [
                        {key: value for key, value in row.items() if key != "queries"}
                        for row in rows
                    ]
                }
            else:
                assert inputs is not None and oracle is not None
                sub_vectors = {}
                if (
                    sum(len(queries) for queries in generated.values()) > 150
                    or sum(len(queries) for queries in oracle.values()) > 15
                ):
                    raise ValueError("Embedding cap exceeded")
                for source, cohort in (("generated", generated), ("oracle", oracle)):
                    for qid, queries in cohort.items():
                        original = next(
                            entry["question"] for entry in dataset if entry["question_id"] == qid
                        )
                        seen = {sweep.parity_query_text(original)}
                        for index, text in enumerate(queries):
                            normalized = sweep.parity_query_text(text)
                            if normalized in seen:
                                continue  # Original vectors are NEVER re-embedded.
                            seen.add(normalized)
                            async with compute_runtime.account_compute(
                                pool,
                                question_user_id(qid),
                                operation="chat",
                                background=True,
                                profile="background",
                            ):
                                sub_vectors[(source, qid, index)] = await embed_subquery(
                                    normalized, qid, accounting
                                )
                await fingerprint(observer, qids, expected)  # fresh view after the last provider
                async with observer.transaction(isolation="repeatable_read", readonly=True):
                    if await observer.fetchval("SHOW transaction_read_only") != "on":
                        raise ValueError("Read-only snapshot not enforced")
                    reader = sweep.SnapshotReader(observer)
                    store = MemoryStore(
                        cast(asyncpg.Pool, reader),
                        ContentEncryption(settings.daemon_encryption_key),
                    )
                    raw, clean = await measure_snapshot(
                        reader, store, inputs, generated, oracle, sub_vectors
                    )
                raw.update({"phase": "measure", "receipts": accounting.records})
                raw["measurement"] = clean
                clean = {**clean, "questions": compact_questions(clean["questions"])}
        after = await fingerprint(observer, qids, expected)
        async with pool.acquire() as writer:
            await writer.execute("SELECT pg_stat_force_next_flush()")
        stats_after = await table_stats(observer)
        changes = check_table_writes(stats_before, stats_after)
        common = {
            "frozen_sha256": frozen_sha,
            "oracle_sha256": args.oracle_sha,
            "dataset_sha256": dataset_sha,
            "fingerprint_before": before,
            "fingerprint_after": after,
            "git_sha": git("rev-parse", "HEAD").decode().strip(),
        }
        raw.update(common)
        raw["table_stats_before"], raw["table_stats_after"] = stats_before, stats_after
        clean.update(
            {
                **common,
                "schema": "daemon-query-decomposition/1",
                "phase": args.command,
                "design_hashes": {
                    key: value
                    for key, value in json.loads(rules.frozen_design()).items()
                    if key.endswith("sha256")
                },
                "accounting": accounting_summary(accounting),
                "table_changes": changes,
                "write_allowlist": sorted(WRITE_ALLOWLIST),
                "answer_transports": 0,
                "judge_transports": 0,
                "sub_queries": {"generated": generated, "oracle": oracle or {}},
                "logical_generation_calls": len(generated),
                "generation_429_retries": sum(
                    row["retries_429"]
                    for row in (prior["questions"] if prior is not None else rows)
                ),
                "generation_parse_retries": sum(
                    row["parse_retries"]
                    for row in (prior["questions"] if prior is not None else rows)
                ),
                "query_count_distribution": {
                    str(count): number
                    for count, number in sorted(Counter(map(len, generated.values())).items())
                },
                "added_query_share": sum(
                    len(rules.query_set(entry["question"], generated[entry["question_id"]])) > 1
                    for entry in dataset
                )
                / len(dataset),
            }
        )
        if inputs is not None:
            clean["input_shas"] = {
                **inputs.shas,
                "generation": args.generation_sha,
                "oracle": args.oracle_sha,
            }
            clean["vector_window"] = {
                "from": inputs.vector_from.isoformat(),
                "until": inputs.vector_until.isoformat(),
            }
        write_outputs(args.raw_out, args.sanitized_out, args.report_out, raw, clean)
        print(
            f"Completed {args.command}; raw SHA256 {sweep.digest_bytes(rules.canonical(raw))}; charged bound USD {accounting.total}"
        )
    except BaseException as error:
        # Preserve uncertain/partial attempts; never auto-resume or replay them.
        failure = {
            "phase": args.command,
            "status": "failed",
            "receipts": accounting.records,
            "partial": raw,
            "error_type": type(error).__name__,
        }
        if stats_before is not None:
            try:
                async with pool.acquire() as writer:
                    await writer.execute("SELECT pg_stat_force_next_flush()")
                stats_after = await table_stats(observer)
                failure.update(
                    {"table_stats_before": stats_before, "table_stats_after": stats_after}
                )
                failure["table_changes"] = check_table_writes(stats_before, stats_after)
                failure["fingerprint_after"] = await fingerprint(observer, qids, expected)
            except BaseException as audit_error:
                failure["audit_error_type"] = type(audit_error).__name__
                error.add_note(f"Failure-path audit failed: {type(audit_error).__name__}")
        if not args.raw_out.exists():
            sweep.write_new_file(args.raw_out, rules.canonical(failure))
        raise
    finally:
        await observer.close()
        await pool.close()


def render_report(data: Mapping[str, Any]) -> str:
    validate_sanitized(dict(data))
    lines = [
        "# Query-decomposition exposure gate — 2026-11-03",
        "",
        "Diagnostic only; no production candidate activated or campaign authorized.",
        "",
        "## Frozen design and hashes",
        f"Frozen design SHA256: `{data['frozen_sha256']}`",
        f"Raw SHA256: `{data['raw_sha256']}`",
        f"Oracle SHA256: `{data['oracle_sha256']}`",
        f"Git: `{data['git_sha']}`",
        f"Design hashes: `{json.dumps(data.get('design_hashes', {}), sort_keys=True)}`",
        "",
        "## Accounting",
        "Chat uses seed=42 and JSON schema; no temperature or reasoning override. Azure EU/ZDR; no fallback.",
        "USD cap: reported costs plus conservative unknown-attempt ceilings, checked before each send.",
        "Chat ceiling: ceil((production_input_bound * prompt_rate + max_tokens * output_rate) / 1e6) micro-USD; embedding uses UTF-8 input_bound and pinned input rate.",
        f"Fingerprint before/after: `{data['fingerprint_before']}` / `{data['fingerprint_after']}`",
        "All pg_stat_user_tables counters inspected; only explicitly source-derived ledger tables and retrieval_log allowed. Denial encounters are not allowlisted.",
        "One writer backend forces its own next statistics flush; concurrent unrelated writes can conservatively fail the audit. Statistics are not a transactional write-provenance proof.",
        "Read-only repeatable-read retrieval; historical question clock; original vectors reused, never embedded.",
        "No answer or judge transport constructed; construction/dispatch guards active.",
    ]
    lines.extend(
        [
            f"{name}: `{value}`"
            for name, value in data["accounting"].items()
            if name not in {"attempts", "cost_per_question_usd", "embedding_calls_per_question"}
        ]
    )
    lines.extend(
        [
            f"Table changes: `{json.dumps(data['table_changes'], sort_keys=True)}`",
            "",
            "## Configuration table",
            "| Configuration | R | P | D | Coverage losses | Tokens |",
            "|---|---|---|---|---|---|",
        ]
    )
    for key, row in data.get("summaries", {}).items():
        lines.append(
            f"| {key} | {row['cases_strict']} | {row['cases_pre_budget']} | {row['target_budget_dropped']} | {len(row['coverage_losses'])} | {row['rendered_tokens']} |"
        )
    lines.extend(
        [
            "",
            "## Per-target attribution",
            "Ranks are 1-based production depth-10 results; null means absent within that depth. Index 0 is original, others are sub-query indices.",
            "| Case | Memory | Source | Query indices | Ranks |",
            "|---|---|---|---|---|",
        ]
    )
    for row in data.get("target_attribution", []):
        lines.append(
            f"| {row['question_id']} | {row['memory_id']} | {row['source']} | {row['query_indices']} | {row['ranks']} |"
        )
    lines.extend(
        [
            "",
            "## Statistics",
            f"Sub-query count distribution: `{json.dumps(data['query_count_distribution'], sort_keys=True)}`",
            f"Share with any added query: {data['added_query_share']}",
            "Per-question costs, embedding counts, latency and attempt records are in sanitized evidence.",
            "",
            "## Production-cost estimate",
            "One extra decomposition LLM call plus at most three query embeddings per turn (original query embedding unchanged).",
        ]
    )
    accounting = data["accounting"]
    for kind in ("chat", "embedding"):
        rows = [
            row
            for row in accounting["attempts"]
            if row["kind"] == kind and row["cost_usd"] is not None
        ]
        mean = (
            sum((Decimal(row["cost_usd"]) for row in rows), Decimal(0)) / len(rows)
            if rows
            else None
        )
        lines.append(
            f"Measured mean USD per {kind} attempt: {mean}; completed receipt count: {len(rows)}."
        )
    lines.extend(
        [
            "",
            "## Decision as returned",
            "```json",
            json.dumps(data.get("decision"), sort_keys=True),
            "```",
            "",
            "## Primary notes",
            "Pending primary adjudication; no authorization implied.",
            "",
        ]
    )
    if len("\n".join(lines).splitlines()) > 400:
        raise ValueError("Report exceeds 400 lines")
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--out", type=Path, default=FROZEN)
    for command in ("generate", "measure"):
        sub = commands.add_parser(command)
        for name in ("dataset", "oracle", "raw-out"):
            sub.add_argument(f"--{name}", type=Path, required=True)
        sub.add_argument("--oracle-sha", required=True)
        sub.add_argument(
            "--sanitized-out",
            type=Path,
            default=EVIDENCE if command == "measure" else None,
            required=command == "generate",
        )
        sub.add_argument(
            "--report-out", type=Path, default=REPORT if command == "measure" else None
        )
        sub.add_argument("--phase3b-arm", choices=("A1",), default="A1")
        sub.add_argument("--dry-run", action="store_true")
        if command == "measure":
            for name in (
                "generation",
                "ingest-checkpoint",
                "ingest-manifest",
                "phase3b-manifest",
                "pilot-results",
            ):
                sub.add_argument(f"--{name}", type=Path, required=True)
            sub.add_argument("--generation-sha", required=True)
            sub.add_argument("--pilot-manifest", type=Path)
            sub.add_argument(
                "--vector-from", type=sweep.utc_timestamp, default=exactness.VECTOR_FROM
            )
            sub.add_argument(
                "--vector-until", type=sweep.utc_timestamp, default=exactness.VECTOR_UNTIL
            )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "freeze":
        sweep.write_new_file(args.out, rules.frozen_design())
        print(sweep.digest_bytes(rules.frozen_design()))
    else:
        sweep.validate_output_paths(args.raw_out, args.sanitized_out, args.report_out)
        asyncio.run(run_live(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
