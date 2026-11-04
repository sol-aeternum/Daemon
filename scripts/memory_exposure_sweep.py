"""Read-only, zero-provider-call prompt exposure diagnostic for #601.

Run with PYTHONPATH=. inside the benchmark DB wrapper. All live paths are explicit
arguments; --dry-run verifies input pins and exits before DB access. This never
restores a snapshot, creates compute scope, answers, judges, or proposes v3 again.
The raw output contains memory text and belongs outside Git. Outputs are exclusive
creates, not resumable ledgers. A refused run must not be automatically replayed.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
from typing import Any, cast
from unittest.mock import patch
import uuid

import asyncpg
import httpx
import litellm

from orchestrator import compute_runtime
from orchestrator.config import get_settings
from orchestrator.eval import exposure
from orchestrator.eval.bench_db import (
    check_benchmark_dsn,
    fingerprint_benchmark_users,
    question_user_id,
)
from orchestrator.eval.fact_harness import (
    HISTORICAL_MESSAGE_TIMESTAMPS,
    build_corpus_key,
    ingest_result_key,
    question_as_of,
)
from orchestrator.memory import embedding, embedding_adapter, injection, retrieval
from orchestrator.memory.embedding import EmbeddingVectorResult
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from tests.longmemeval.evaluate import build_parity_memory_context

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "tests/benchmark_results/memory_phase3b_2026-11-01_evidence.json"
PHASE3B_INDEX_SHA256 = "3fe6e32efc329719728ade67fc677dfe7195688f8afdb3b147b3cc31ebac3043"
CASES = ROOT / "tests/benchmark_results/failure_triage_phase2_2026-10-28/cases.json"
ABSTENTION_OVERREACH = ("f685340e_abs", "gpt4_93159ced_abs", "gpt4_372c3eed_abs")
ABSTENTION_SOURCE = "tests/benchmark_results/failure_triage_phase2_2026-10-28.md:132"
MAX_SANITIZED_STRING = 128
FORBIDDEN_FIELDS = frozenset(
    {
        "content",
        "query_text",
        "question",
        "answer",
        "reference",
        "memory_context",
        "prompt",
        "hypothesis",
        "embedding",
        "query_embedding",
    }
)


class ProviderAccessDenied(BaseException):
    """Intentionally escape production's best-effort Exception degradation paths."""


@dataclass
class ProviderGuard:
    attempted_calls: int = 0

    def refuse(self, *args: Any, **kwargs: Any) -> Any:
        self.attempted_calls += 1
        raise ProviderAccessDenied("Provider or compute entrypoint reached")


@contextmanager
def no_providers() -> Iterator[ProviderGuard]:
    """Block adapters, chat construction/dispatch, HTTP and compute, restoring all."""
    guard = ProviderGuard()
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"OPENROUTER_API_KEY": "exposure-sweep-dummy"}))
        for module, names in (
            (embedding_adapter, ("embed", "_post", "metered_embedding_call", "current_scope")),
            (
                embedding,
                (
                    "_embed_texts",
                    "_embed_texts_with_openai",
                    "_embed_texts_with_openrouter",
                    "_embed_with_voyage_retry",
                ),
            ),
            (compute_runtime, ("account_compute", "metered_embedding_call", "current_scope")),
            (compute_runtime.ComputeScope, ("reserve_inference",)),
            (litellm, ("acompletion", "completion")),
            (httpx, ("AsyncClient", "Client")),
        ):
            for name in names:
                stack.enter_context(patch.object(module, name, guard.refuse))
        try:
            yield guard
        except BaseException as error:
            if guard.attempted_calls:
                error.add_note(f"Forbidden provider/compute attempts: {guard.attempted_calls}")
            raise
        else:
            if guard.attempted_calls:
                raise ProviderAccessDenied("Nonzero forbidden-call attempts")


@contextmanager
def memory_width(k: int) -> Iterator[None]:
    if k not in exposure.SWEEP_KS:
        raise ValueError("Unapproved width")
    with patch.object(injection, "MAX_MEMORY_ITEMS", k):
        yield


def parity_query_text(question: str) -> str:
    """injection.py:247 uses _normalize_content, NOT a formatted prompt."""
    return injection._normalize_content(question)


def _vector(value: object, dimensions: int) -> list[float]:
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, (list, tuple)) or len(parsed) != dimensions:
        raise ValueError("Logged vector has wrong dimensions")
    if any(type(number) not in (float, int) or not math.isfinite(number) for number in parsed):
        raise ValueError("Invalid logged vector")
    vector = [float(number) for number in parsed]
    if not any(vector):
        raise ValueError("Zero logged vector")
    return vector


def utc_timestamp(value: str) -> datetime:
    """CLI timestamps must be explicit ISO-8601 UTC, not local or naive times."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(None):
        raise ValueError("Vector window timestamps must explicitly specify UTC")
    return parsed.astimezone(UTC)


def validate_vector_window(vector_from: datetime, vector_until: datetime) -> None:
    for value in (vector_from, vector_until):
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
            raise ValueError("Vector window timestamps must explicitly specify UTC")
    if vector_until < vector_from:
        raise ValueError("Vector window end precedes its start")


def select_logged_vector(
    rows: Sequence[Mapping[str, Any]],
    *,
    user_id: uuid.UUID,
    text: str,
    model: str,
    dimensions: int,
    vector_from: datetime,
    vector_until: datetime,
) -> tuple[list[float], list[str], dict[str, int | float]]:
    """Use exactly the pilot-window row; compare other rows without exporting them."""
    validate_vector_window(vector_from, vector_until)
    matches = [
        row
        for row in rows
        if str(row["user_id"]) == str(user_id)
        and row.get("query_role") in (None, "original")
        and row["query_text"] == text
        and row["query_embedding_model"] == model
    ]
    in_window = []
    outside = []
    for row in matches:
        created_at = row.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        if not isinstance(created_at, datetime) or created_at.tzinfo is None:
            raise ValueError(f"Invalid logged timestamp for user {user_id}")
        (in_window if vector_from <= created_at <= vector_until else outside).append(row)
    if len(in_window) != 1:
        raise ValueError(
            f"Expected exactly one in-window vector for user {user_id}; found {len(in_window)}"
        )
    chosen = in_window[0]
    vector = _vector(chosen["query_embedding"], dimensions)
    comparisons = [_vector(row["query_embedding"], dimensions) for row in outside]
    return (
        vector,
        [str(chosen["log_id"])],
        {
            "out_of_window_rows": len(outside),
            "identical_out_of_window": sum(other == vector for other in comparisons),
            "max_abs_diff": max(
                (
                    abs(left - right)
                    for other in comparisons
                    for left, right in zip(vector, other, strict=True)
                ),
                default=0.0,
            ),
        },
    )


@contextmanager
def cached_query(text: str, result: EmbeddingVectorResult) -> Iterator[None]:
    async def cached(value: str, *, local_only: bool = False) -> EmbeddingVectorResult:
        if value != text or local_only is not False:
            raise ProviderAccessDenied("Uncached embedding text refused")
        return result

    with patch.object(injection, "embed_query_with_metadata", cached):
        yield


class SnapshotReader:
    """Serialize gather's parallel reads on ONE repeatable-read connection.

    No execute/acquire/write surface. PostgreSQL additionally enforces read-only.
    """

    def __init__(self, connection: Any):
        self.connection = connection
        self.lock = asyncio.Lock()

    async def fetch(self, sql: str, *args: Any) -> Any:
        async with self.lock:
            return await self.connection.fetch(sql, *args)

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        async with self.lock:
            return await self.connection.fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        async with self.lock:
            return await self.connection.fetchval(sql, *args)


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _input_bytes(path: Path) -> bytes:
    if "holdout" in str(path).casefold() or "holdout" in str(path.resolve()).casefold():
        raise ValueError("Holdout access forbidden")
    return path.read_bytes()


def pinned_json(path: Path, expected: str) -> Any:
    data = _input_bytes(path)
    if digest_bytes(data) != expected:
        raise ValueError(f"Input pin mismatch: {path.name}")
    return json.loads(data)


@dataclass
class SweepInputs:
    dataset: list[dict[str, Any]]
    targets: dict[str, list[str]]
    gold: dict[str, list[str]]
    model: str
    storage_model: str
    dimensions: int
    fingerprint: str
    shas: dict[str, str]
    route_id: str
    vector_from: datetime
    vector_until: datetime
    pilot_hashes: dict[str, str] = field(default_factory=dict)
    pilot_provenance: dict[str, Any] = field(default_factory=dict)


def pilot_provenance(
    manifest: Mapping[str, Any] | None, model: str, route_id: str
) -> dict[str, Any]:
    """Explicit assumptions; verify only facts the pilot manifest actually exposes."""
    manifest = manifest or {}
    width = manifest.get(
        "max_memory_items", manifest.get("memory_formatter", {}).get("max_memory_items")
    )
    historical = manifest.get("historical_clock")
    if historical is None:
        timing = next(
            (
                item
                for item in manifest.get("parity_exceptions", [])
                if item.get("id") == "message_timestamps"
            ),
            None,
        )
        if timing is not None:
            historical = timing == HISTORICAL_MESSAGE_TIMESTAMPS
    recorded = manifest.get("embedding", {})
    recorded_model = recorded.get("model", recorded.get("query_embedding_model"))

    def status(actual: Any, expected: Any) -> str:
        return "unverified" if actual is None else "verified" if actual == expected else "mismatch"

    return {
        "assumed_width": exposure.BASELINE_K,
        "width_status": status(width, exposure.BASELINE_K),
        "assumed_historical_clock": True,
        "historical_clock_status": status(historical, True),
        "assumed_model": model,
        "model_status": status(recorded_model, model),
        "assumed_route_id": route_id,
        "route_status": status(recorded.get("route_id"), route_id),
    }


def pilot_hashes(path: Path) -> dict[str, str]:
    output: dict[str, str] = {}
    for line in _input_bytes(path).decode().splitlines():
        row = json.loads(line)
        block = row.get("memory_context")
        value = (
            digest_bytes(block.encode())
            if isinstance(block, str)
            else row.get("memory_context_sha256")
        )
        if value is None:
            continue
        qid = str(row["question_id"])
        if not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError("Invalid pilot memory hash")
        if qid in output and output[qid] != value:
            raise ValueError(f"Conflicting pilot blocks: {qid}")
        output[qid] = value
    return output


def prepare_inputs(args: argparse.Namespace) -> SweepInputs:
    """Local-only pin checks; no DB, settings, credentials or transport access."""
    validate_vector_window(args.vector_from, args.vector_until)
    index = pinned_json(INDEX, PHASE3B_INDEX_SHA256)
    arm = index["arms"][args.phase3b_arm]
    manifest = pinned_json(args.phase3b_manifest, arm["artifacts_sha256"]["run_manifest.json"])
    ingest_manifest = pinned_json(args.ingest_manifest, index["pins"]["ingest_manifest_sha256"])
    checkpoint = pinned_json(args.ingest_checkpoint, index["pins"]["ingest_checkpoint_sha256"])
    dataset = pinned_json(args.dataset, arm["dataset_sha256"])
    if not isinstance(dataset, list) or len(dataset) != exposure.QUESTION_COUNT:
        raise ValueError("Expected 50 dev questions")
    qids = [row["question_id"] for row in dataset]
    if len(set(qids)) != exposure.QUESTION_COUNT or sum(qid.endswith("_abs") for qid in qids) != 5:
        raise ValueError("Unexpected dev question identities")
    # The selected adapter's model/storage identity is recorded in the pinned
    # ingest manifest; unlike legacy Settings.embedding_query_model, it describes
    # actual dispatch. Live retrieval must retain that selected space with no
    # fallbacks; no provider approval/dispatch is performed by this diagnostic.
    spaces = ingest_manifest["phases"]["ingest"]["memory_embeddings"]
    if set(spaces) != {embedding_adapter.STORAGE_MODEL} or not manifest["embedding"]["route_id"]:
        raise ValueError("Unsupported or ambiguous pinned embedding identity")
    cases = json.loads(_input_bytes(CASES))["cases"]
    targets = {
        row["question_id"]: [memory["id"] for memory in row["source_memories"]]
        for row in cases
        if row["classification"] == "prompt-selection-gap"
    }
    if (
        len(targets) != exposure.CASE_COUNT
        or not all(targets.values())
        or not set(targets) <= set(qids)
    ):
        raise ValueError("Expected five nonempty target cases in dev")
    if not set(ABSTENTION_OVERREACH) <= set(qids):
        raise ValueError("Abstention cases missing from dev")
    results = checkpoint["phases"]["ingest"]["results"]
    gold: dict[str, list[str]] = {}
    for entry in dataset:
        qid = entry["question_id"]
        question_as_of(entry)  # fail invalid dates before DB access
        sessions = {
            str(sid): results[ingest_result_key(qid, build_corpus_key(messages))]["conversation_id"]
            for sid, messages in zip(
                entry["haystack_session_ids"], entry["haystack_sessions"], strict=True
            )
        }
        gold[qid] = [str(sessions[str(sid)]) for sid in entry.get("answer_session_ids", [])]
    pilot_manifest_path = (
        getattr(args, "pilot_manifest", None) or args.pilot_results.parent / "run_manifest.json"
    )
    pilot_manifest_bytes = (
        _input_bytes(pilot_manifest_path) if pilot_manifest_path.exists() else None
    )
    if getattr(args, "pilot_manifest", None) and pilot_manifest_bytes is None:
        raise ValueError("Explicit pilot manifest missing")
    return SweepInputs(
        dataset=dataset,
        targets=targets,
        gold=gold,
        model=embedding_adapter.MODEL,
        storage_model=embedding_adapter.STORAGE_MODEL,
        dimensions=embedding_adapter.DIMENSIONS,
        route_id=manifest["embedding"]["route_id"],
        vector_from=args.vector_from,
        vector_until=args.vector_until,
        fingerprint=arm["restore"]["fingerprint_sha256"],
        shas={
            "evidence_index": PHASE3B_INDEX_SHA256,
            "cases": digest_bytes(_input_bytes(CASES)),
            "dataset": arm["dataset_sha256"],
            "ingest_checkpoint": index["pins"]["ingest_checkpoint_sha256"],
            "ingest_manifest": index["pins"]["ingest_manifest_sha256"],
            "phase3b_manifest": arm["artifacts_sha256"]["run_manifest.json"],
            "pilot_results": digest_bytes(_input_bytes(args.pilot_results)),
            **(
                {"pilot_manifest": digest_bytes(pilot_manifest_bytes)}
                if pilot_manifest_bytes is not None
                else {}
            ),
        },
        pilot_hashes=pilot_hashes(args.pilot_results),
        pilot_provenance=pilot_provenance(
            json.loads(pilot_manifest_bytes) if pilot_manifest_bytes is not None else None,
            embedding_adapter.MODEL,
            manifest["embedding"]["route_id"],
        ),
    )


def verify_embedding_context(inputs: SweepInputs) -> None:
    """Fail closed before any retrieval; selected routes suppress fallback sends."""
    route_id = embedding.get_selected_embedding_route_id()
    if not inputs.route_id or route_id != inputs.route_id:
        raise ValueError("Selected embedding route differs from Phase 3B")
    if embedding.get_configured_embedding_fallback_storage_models() != ():
        raise ValueError("Embedding fallback storage models must be empty")
    if embedding_adapter.MODEL != inputs.model:
        raise ValueError("Effective query embedding model differs from pinned/logged model")
    if (
        embedding.get_primary_embedding_storage_model() != inputs.storage_model
        or inputs.storage_model != embedding_adapter.STORAGE_MODEL
    ):
        raise ValueError("Effective embedding storage space differs from pinned ingest space")


async def sweep_snapshot(
    reader: Any, store: Any, inputs: SweepInputs
) -> tuple[dict[str, Any], dict[str, Any]]:
    """All vectors and targets are verified before the first retrieval/output."""
    if injection.MAX_MEMORY_ITEMS != exposure.BASELINE_K:
        raise ValueError("MAX_MEMORY_ITEMS must start at 5")
    verify_embedding_context(inputs)
    validate_vector_window(inputs.vector_from, inputs.vector_until)
    users = {row["question_id"]: question_user_id(row["question_id"]) for row in inputs.dataset}
    before = await fingerprint_benchmark_users(reader, list(users.values()))
    if before["sha256"] != inputs.fingerprint:
        raise ValueError("Before fingerprint mismatch")
    source_rows = await reader.fetch(
        "SELECT id, user_id, source_conversation_id FROM memories WHERE user_id = ANY($1::uuid[])",
        list(users.values()),
    )
    source_map = {
        str(row["id"]): str(row["source_conversation_id"])
        if row["source_conversation_id"]
        else None
        for row in source_rows
    }
    owners = {str(row["id"]): str(row["user_id"]) for row in source_rows}
    missing = [
        mid
        for qid, ids in inputs.targets.items()
        for mid in ids
        if owners.get(mid) != str(users[qid])
    ]
    if missing:
        raise ValueError(f"Missing or foreign target memories: {','.join(sorted(missing))}")
    logged = await reader.fetch(
        "SELECT id::text AS log_id, user_id, query_text, query_embedding_model,"
        " query_embedding::text AS query_embedding, created_at FROM retrieval_log"
        " WHERE user_id = ANY($1::uuid[]) AND (query_role IS NULL OR query_role = 'original')"
        " ORDER BY user_id, created_at, id",
        list(users.values()),
    )
    vectors = {}
    for entry in inputs.dataset:
        qid = entry["question_id"]
        vectors[qid] = select_logged_vector(
            logged,
            user_id=users[qid],
            text=parity_query_text(entry["question"]),
            model=inputs.model,
            dimensions=inputs.dimensions,
            vector_from=inputs.vector_from,
            vector_until=inputs.vector_until,
        )
    questions = []
    raw_questions = []
    with no_providers() as guard:
        for entry in inputs.dataset:
            qid = entry["question_id"]
            vector, log_ids, comparison = vectors[qid]
            result = EmbeddingVectorResult(
                embedding=vector,
                provider="azure-openrouter",
                model=inputs.model,
                storage_model=inputs.storage_model,
            )
            clock = retrieval.RECENCY_REFERENCE_TIME.set(question_as_of(entry))
            try:
                with cached_query(parity_query_text(entry["question"]), result):
                    ranking = await retrieval.retrieve_memories_for_text(
                        store=store,
                        query_text=parity_query_text(entry["question"]),
                        user_id=users[qid],
                        query_embedding=vector,
                        limit=exposure.RANK_LIMIT,
                        include_local=False,
                        storage_embedding_model=inputs.storage_model,
                        query_embedding_model=inputs.model,
                        touch_results=False,
                        retrieval_triggered_by="longmemeval",
                        query_reference_time=question_as_of(entry),
                    )
                    widths = {}
                    raw_widths = {}
                    baseline_ids: list[str] = []
                    baseline_rendered: set[str] = set()
                    for k in exposure.SWEEP_KS:
                        with memory_width(k):
                            block, gathered = await build_parity_memory_context(
                                store,
                                user_id=users[qid],
                                question_text=entry["question"],
                                allowed_source_conversation_ids=None,
                                log_retrieval=False,
                                query_reference_time=question_as_of(entry),
                            )
                            if gathered.retrieval_mode in {"failed", "none", "lexical"}:
                                raise ValueError(f"Parity retrieval failed for {qid}")
                            metrics = exposure.measure(
                                gathered,
                                target_ids=inputs.targets.get(qid, []),
                                gold_conversation_ids=inputs.gold[qid],
                                source_conversations=source_map,
                                ranking=ranking,
                            )
                            rendered_rows = exposure.rendered_memory_rows(gathered, block)
                            visible_rows = [
                                {"id": row["id"], "content": text}
                                for row, text in zip(
                                    rendered_rows,
                                    exposure.rendered_memory_texts(block),
                                    strict=True,
                                )
                            ]
                            if k == exposure.BASELINE_K:
                                # Independent production wrapper vs explicit pair.
                                if block != injection.render_memory_context(gathered):
                                    raise ValueError("k=5 production renderer self-check failed")
                                baseline_ids = metrics["retrieved_ids"]
                                baseline_rendered = set(metrics["rendered_ids"])
                            metrics["prefix_stable"] = (
                                metrics["retrieved_ids"][: exposure.BASELINE_K] == baseline_ids
                            )
                            metrics["block_sha256"] = digest_bytes(block.encode())
                            metrics["distractors"] = (
                                exposure.distractor_counts(
                                    entry["question"],
                                    [
                                        row
                                        for row in visible_rows
                                        if str(row["id"]) not in baseline_rendered
                                    ],
                                )
                                if qid.endswith("_abs") or qid in ABSTENTION_OVERREACH
                                else None
                            )
                            widths[str(k)] = metrics
                            raw_widths[str(k)] = {
                                "memory_context": block,
                                "inputs": {
                                    "l0": gathered.l0_memories,
                                    "retrieved": gathered.retrieved,
                                    "summaries": gathered.summaries,
                                },
                            }
            finally:
                retrieval.RECENCY_REFERENCE_TIME.reset(clock)
            relevant_ids = {
                mid
                for row in widths.values()
                for mid in [*row["rendered_ids"], *row["pre_budget_ids"]]
            }
            expected = inputs.pilot_hashes.get(qid)
            questions.append(
                {
                    "question_id": qid,
                    "widths": widths,
                    "source_conversations": {
                        mid: source_map.get(mid) for mid in sorted(relevant_ids)
                    },
                    "vector_match_count": len(log_ids),
                    "vector_log_ids": log_ids,
                    **comparison,
                    "vector_sha256": digest_bytes(
                        json.dumps(vector, separators=(",", ":")).encode()
                    ),
                    "pilot_block_sha256": expected,
                    "pilot_match": widths[str(exposure.BASELINE_K)]["block_sha256"] == expected
                    if expected
                    else None,
                    "abstention_dev": qid.endswith("_abs"),
                    "abstention_overreach": qid in ABSTENTION_OVERREACH,
                }
            )
            raw_questions.append({"question_id": qid, "widths": raw_widths, "ranking": ranking})
        after = await fingerprint_benchmark_users(reader, list(users.values()))
        if after != before or after["sha256"] != inputs.fingerprint:
            raise ValueError("After fingerprint mismatch")
        if guard.attempted_calls != 0 or injection.MAX_MEMORY_ITEMS != exposure.BASELINE_K:
            raise ValueError("Provider count or width invariant failed")
    summaries = exposure.summarize(questions)
    decision = exposure.decide(summaries)
    sanitized = {
        "schema": "daemon-memory-exposure/1",
        "input_shas": inputs.shas,
        "fingerprint_before": before["sha256"],
        "fingerprint_after": after["sha256"],
        "provider_calls": 0,
        "embedding_calls": 0,
        "chat_calls": 0,
        "provider_cost_usd": "0",
        "max_memory_items_start": exposure.BASELINE_K,
        "max_memory_items_end": injection.MAX_MEMORY_ITEMS,
        "clock": "question_as_of_and_RECENCY_REFERENCE_TIME",
        "transaction": "read_only_repeatable_read",
        "embedding_model": inputs.model,
        "storage_model": inputs.storage_model,
        "dimensions": inputs.dimensions,
        "vector_window": {
            "from": inputs.vector_from.isoformat(),
            "until": inputs.vector_until.isoformat(),
        },
        "questions_with_differing_out_of_window_rows": sum(
            question["identical_out_of_window"] < question["out_of_window_rows"]
            for question in questions
        ),
        "duplicate_rule": exposure.DUPLICATE_RULE,
        "exact_duplicate_rule": exposure.EXACT_DUPLICATE_RULE,
        "distractor_rule": exposure.DISTRACTOR_RULE,
        "abstention_source": ABSTENTION_SOURCE,
        "questions": questions,
        "summaries": {str(k): row for k, row in summaries.items()},
        "decision": decision,
        "pilot_matches": sum(question["pilot_match"] is True for question in questions),
        "pilot_recoverable": sum(question["pilot_match"] is not None for question in questions),
        "pilot_total": len(questions),
        "pilot_self_check": inputs.pilot_provenance
        or pilot_provenance(None, inputs.model, inputs.route_id),
    }
    return {"questions": raw_questions}, sanitized


def validate_sanitized(value: Any) -> None:
    """Allow short token strings only; reject text-bearing fields at every depth."""
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Nonfinite sanitized number")
    elif isinstance(value, str):
        if (
            not re.fullmatch(r"[A-Za-z0-9_.:/+\-]{1,128}", value)
            or len(value) > MAX_SANITIZED_STRING
        ):
            raise ValueError("Free text or overlong sanitized string")
    elif isinstance(value, list):
        for item in value:
            validate_sanitized(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or key in FORBIDDEN_FIELDS:
                raise ValueError("Forbidden sanitized field")
            validate_sanitized(key)
            validate_sanitized(item)
    else:
        raise ValueError("Unexpected sanitized type")


def write_outputs(
    raw_path: Path, sanitized_path: Path, raw: dict[str, Any], sanitized: dict[str, Any]
) -> None:
    data = (json.dumps(raw, indent=2, sort_keys=True, default=str, allow_nan=False) + "\n").encode()
    sanitized = {**sanitized, "raw_sha256": digest_bytes(data)}
    validate_sanitized(sanitized)
    validate_output_paths(raw_path, sanitized_path)
    write_new_file(raw_path, data)
    write_new_file(
        sanitized_path,
        (json.dumps(sanitized, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(),
    )


def validate_output_paths(
    raw_path: Path, sanitized_path: Path, report_path: Path | None = None
) -> None:
    paths = [path for path in (raw_path, sanitized_path, report_path) if path is not None]
    resolved = [path.resolve() for path in paths]
    if resolved[0].is_relative_to(ROOT.resolve()):
        raise ValueError("Raw output must be outside the git repository root")
    if len(set(resolved)) != len(resolved):
        raise ValueError("Output paths must be distinct and new")
    for path, destination in zip(paths, resolved, strict=True):
        if "holdout" in str(destination).casefold() or ".git" in destination.parts:
            raise ValueError("Forbidden output destination")
        if path.exists() or path.is_symlink():
            raise ValueError("Output paths must be distinct and new")


def write_new_file(path: Path, data: bytes) -> None:
    """Exclusive, no-follow, owner-only creation: plaintext is never world-readable."""
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


def render_report(data: Mapping[str, Any], prior: Mapping[str, Any]) -> str:
    """Deterministic report from sanitized metrics and primary-supplied evidence."""
    validate_sanitized(dict(data))
    # Decisions are reproduced from metrics, not manually reclassified by prose.
    decision = exposure.decide({int(k): row for k, row in data["summaries"].items()})
    if decision != data["decision"]:
        raise ValueError("Saved decision differs from decide()")
    lines = [
        "# Memory prompt-surface exposure sweep — 2026-11-03",
        "",
        "Diagnostic only; no production candidate, answer generation or judging.",
        "Historical clock: question_as_of plus RECENCY_REFERENCE_TIME; remapped corpus.",
        "Database reads use one read-only repeatable-read snapshot; fingerprints do not detect concurrent commits outside that snapshot.",
        f"Git: `{data['git_sha']}`. MAX_MEMORY_ITEMS: {data['max_memory_items_start']} → {data['max_memory_items_end']}.",
        f"Fingerprint before: `{data['fingerprint_before']}`; after: `{data['fingerprint_after']}`.",
        f"Raw SHA256: `{data['raw_sha256']}`.",
        "",
        "## Input pins",
        "",
    ]
    lines.extend(f"- {key}: `{value}`" for key, value in sorted(data["input_shas"].items()))
    lines.extend(
        [
            "",
            "## Calls and cost",
            "",
            f"Provider calls: {data['provider_calls']}; embedding: {data['embedding_calls']}; chat: {data['chat_calls']}; cost: USD {data['provider_cost_usd']} (zero calls, not a token-price estimate).",
            "Query vectors reused from retrieval_log; exact normalized-question/model match and exactly one row in the inclusive pilot A1 window.",
            f"Pinned vector window: [{data['vector_window']['from']}, {data['vector_window']['until']}]. Questions with differing out-of-window rows: {data['questions_with_differing_out_of_window_rows']}/{len(data['questions'])}.",
            f"Model: `{data['embedding_model']}`; storage: `{data['storage_model']}`; dimensions: {data['dimensions']}.",
            "",
            "## Prior evidence",
            "",
            f"2026-10-31 zero-call diagnostic: {prior['zero_call_diagnostic']}",
            f"v3 pilot: {prior['v3_pilot']}",
            "All approved restores are consumed (13/13). The failed v3 pilot is not proposed again.",
            "",
            "## Exposure by width",
            "",
            "Gold-session coverage is a proxy, not proof of answer-fact exposure. Empty gold is None.",
            "| k | Strict /5 | Any /5 | Pre-budget /5 | Coverage mean (proxy) | Changed questions | Coverage losses | Budget drops (target) | Tokens | Effective items | Duplicates exact / similar |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for k in exposure.SWEEP_KS:
        row = data["summaries"][str(k)]
        lines.append(
            f"| {k} | {row['cases_strict']} | {row['cases_any']} | {row['cases_pre_budget']} | {row['coverage_mean_proxy']} | {len(row['coverage_changed'])} | {len(row['coverage_losses'])} | {row['budget_dropped']} ({row['target_budget_dropped']}) | {row['rendered_tokens']} | {row['effective_k']} | {row['duplicates_exact']} / {row['duplicates_similarity']} |"
        )
    lines.extend(
        [
            "",
            f"Duplicate rules: `{exposure.EXACT_DUPLICATE_RULE}` and `{exposure.DUPLICATE_RULE}`; occupancy counts slots matching an earlier line, not pairs. Similarity includes exact duplicates.",
            "",
            "## Per-target ranks",
            "",
            "| Question | Memory ID | Rank (limit 50; None if absent) | Rendered widths | Budget-dropped widths |",
            "|---|---|---|---|---|",
        ]
    )
    for question in data["questions"]:
        for mid, target in sorted(question["widths"]["5"]["targets"].items()):
            rendered = [
                str(k)
                for k in exposure.SWEEP_KS
                if question["widths"][str(k)]["targets"][mid]["rendered"]
            ]
            dropped = [
                str(k)
                for k in exposure.SWEEP_KS
                if question["widths"][str(k)]["targets"][mid]["budget_dropped"]
            ]
            lines.append(
                f"| {question['question_id']} | {mid} | {target['rank']} | {', '.join(rendered) or 'none'} | {', '.join(dropped) or 'none'} |"
            )
    lines.extend(
        [
            "",
            "## Prefix stability and pilot self-check",
            "",
            f"k=5 pilot block hash matches: {data['pilot_matches']}/{data['pilot_total']}; recoverable: {data['pilot_recoverable']}/{data['pilot_total']}. Mismatches are findings, not stops.",
            f"Pilot comparison assumptions: width={data['pilot_self_check']['assumed_width']} ({data['pilot_self_check']['width_status']}); historical clock=True ({data['pilot_self_check']['historical_clock_status']}); model=`{data['pilot_self_check']['assumed_model']}` ({data['pilot_self_check']['model_status']}); route=`{data['pilot_self_check']['assumed_route_id']}` ({data['pilot_self_check']['route_status']}). Unverified means the pilot manifest does not expose the fact; hash equality alone is not provenance verification.",
            "",
            "| k | Stable top-5 /50 | Unstable IDs |",
            "|---|---|---|",
        ]
    )
    for k in exposure.SWEEP_KS:
        unstable = [
            question["question_id"]
            for question in data["questions"]
            if not question["widths"][str(k)]["prefix_stable"]
        ]
        lines.append(
            f"| {k} | {len(data['questions']) - len(unstable)} | {', '.join(unstable) or 'none'} |"
        )
    lines.extend(
        [
            "",
            "## Abstention distractor early warning",
            "",
            f"Rule: `{exposure.DISTRACTOR_RULE}`. First capitalized non-stopword alphanumeric token, else first non-stopword token; an added rendered memory sharing it is plausible support, not a verdict.",
            f"Overreach source: `{ABSTENTION_SOURCE}`. Dev _abs and overreach membership may overlap.",
            "",
            "| Question | Dev _abs | Overreach | k | Added items | Plausible support |",
            "|---|---|---|---|---|---|",
        ]
    )
    for question in data["questions"]:
        if not (question["abstention_dev"] or question["abstention_overreach"]):
            continue
        for k in exposure.SWEEP_KS:
            counts = question["widths"][str(k)]["distractors"]
            lines.append(
                f"| {question['question_id']} | {question['abstention_dev']} | {question['abstention_overreach']} | {k} | {counts['added']} | {counts['plausible_support']} |"
            )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"`{decision['outcome']}`; chosen k: {decision['k']}.",
            f"Reasons: {', '.join(decision['reasons'])}.",
        ]
    )
    if decision["outcome"] == "PROPOSE_WIDTH_CAMPAIGN":
        lines.append(
            f"Propose one width candidate, k={decision['k']}, under v2 only. New restore and budget approvals are required; existing restores are 13/13 consumed. No campaign is authorized by this diagnostic."
        )
    elif decision["outcome"] == "BUDGET_LIMITED":
        lines.append(
            "Binding constraint: coupled width-plus-budget variable; owner sign-off required. No campaign."
        )
    elif decision["outcome"] == "COVERAGE_REGRESSION":
        lines.append("No campaign. Coverage-loss attribution:")
        lines.extend(f"- {qid}: {cause}" for qid, cause in sorted(decision["questions"].items()))
    else:
        lines.append(
            "Investigate retrieval; every candidate width has fewer than four strictly rendered cases. No campaign."
        )
    lines.extend(["", "## Coverage changes", ""])
    lines.append(
        "Loss attribution is prefix_instability when any baseline memory from a lost gold session is absent from the new pre-budget set; otherwise it is non_target_budget_drop. The former takes precedence if both mechanisms occur."
    )
    for k in exposure.CANDIDATE_KS:
        row = data["summaries"][str(k)]
        lines.append(
            f"- k={k}: changed={row['coverage_changed']}; losses={row['coverage_losses']}; causes={row['coverage_causes']}."
        )
    return "\n".join(lines) + "\n"


async def run_live(inputs: SweepInputs) -> tuple[dict[str, Any], dict[str, Any]]:
    # Provider guard precedes settings/store construction, not just retrieval.
    with no_providers():
        settings = get_settings()
        if settings.retrieval_logging_enabled or settings.retrieval_logging_debug:
            raise ValueError("Retrieval logging must be disabled for the read-only sweep")
        check_benchmark_dsn(settings.database_url)
        connection = await asyncpg.connect(
            settings.database_url,
            timeout=10,
            server_settings={"default_transaction_read_only": "on", "statement_timeout": "30000"},
        )
        try:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                reader = SnapshotReader(connection)
                if await reader.fetchval("SHOW transaction_read_only") != "on":
                    raise ValueError("Read-only transaction not enforced")
                store = MemoryStore(
                    cast(asyncpg.Pool, reader), ContentEncryption(settings.daemon_encryption_key)
                )
                return await sweep_snapshot(reader, store, inputs)
        finally:
            await connection.close()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    for name in (
        "dataset",
        "ingest-checkpoint",
        "ingest-manifest",
        "phase3b-manifest",
        "pilot-results",
        "raw-out",
        "sanitized-out",
    ):
        result.add_argument(f"--{name}", type=Path, required=True)
    result.add_argument("--phase3b-arm", choices=("A0", "A1"), default="A1")
    result.add_argument("--vector-from", type=utc_timestamp, required=True)
    result.add_argument("--vector-until", type=utc_timestamp, required=True)
    result.add_argument(
        "--pilot-manifest",
        type=Path,
        help="Defaults to run_manifest.json beside pilot results; absent facts remain unverified",
    )
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--report-out", type=Path)
    result.add_argument(
        "--prior-evidence", type=Path, help="JSON with zero_call_diagnostic and v3_pilot strings"
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if bool(args.report_out) != bool(args.prior_evidence):
        raise ValueError("Report and prior evidence arguments must be paired")
    validate_output_paths(args.raw_out, args.sanitized_out, args.report_out)
    inputs = prepare_inputs(args)
    if args.dry_run:
        print("Dry-run: input pins verified; no DB/provider access or output")
        return 0
    raw, sanitized = asyncio.run(run_live(inputs))
    sanitized["git_sha"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()  # nosec B603 B607 - fixed local command
    write_outputs(args.raw_out, args.sanitized_out, raw, sanitized)
    if args.report_out:
        saved = json.loads(args.sanitized_out.read_text())
        prior = json.loads(_input_bytes(args.prior_evidence))
        write_new_file(args.report_out, render_report(saved, prior).encode())
    print(
        f"Completed: zero provider calls; pilot matches {sanitized['pilot_matches']}/50; {sanitized['decision']['outcome']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
