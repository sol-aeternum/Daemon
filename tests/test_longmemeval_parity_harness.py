"""LongMemEval fact harness: benchmark DB guard, durable progress, resume, manifest.

Offline tests: no database, no provider. The runner's database-side helpers are
stubbed; their SQL is exercised against the real benchmark stack in the
RUNBOOK_LAPTOP.md smoke procedure.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.eval import bench_db, durable_io, fact_harness
from orchestrator.eval.bench_db import BenchmarkDatabaseGuardError
from orchestrator.eval.fact_harness import CHECKPOINT_VERSION, LongMemEvalFactRunner
from orchestrator.eval.interrupts import HarnessInterrupted, StopController, run_interruptible
from orchestrator.eval.run_manifest import ManifestPinError
from tests.longmemeval.ingest import build_corpus_key

BENCH_DSN = "postgresql://bench:bench@127.0.0.1:55432/daemon_lme_bench"
USER_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")
FP_INGEST = {"sha256": "a" * 64, "tables": {}}
FP_PRE_SESSION = {"sha256": "p" * 64, "tables": {}}
RUN_ID = "0" * 32


# ------------------------------------------------------------------------- guard
@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://daemon:daemon@127.0.0.1:5432/daemon",
        "postgresql://admin:s3cr3t-token@db.internal:5432/production",
        "postgresql://u:p@host/daemon_lme",
        "mysql://u:p@host/daemon_lme_bench",
        "",
        None,
        "not a url",
    ],
)
def test_dsn_guard_refuses_non_benchmark_databases(dsn: str | None) -> None:
    with pytest.raises(BenchmarkDatabaseGuardError) as excinfo:
        bench_db.check_benchmark_dsn(dsn)
    assert "s3cr3t" not in str(excinfo.value)


def test_dsn_guard_accepts_benchmark_database() -> None:
    assert bench_db.check_benchmark_dsn(BENCH_DSN) == "daemon_lme_bench"


class GuardPool:
    def __init__(self, name: str, marker: str | None) -> None:
        self.row = {"name": name, "marker": marker}

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any]:
        assert "shobj_description" in query
        return self.row


@pytest.mark.asyncio
async def test_database_guard_requires_marker_and_matching_name() -> None:
    marker = bench_db.BENCHMARK_DB_MARKER
    assert await bench_db.assert_benchmark_database(
        GuardPool("daemon_lme_bench", marker), BENCH_DSN
    ) == ("daemon_lme_bench")
    with pytest.raises(BenchmarkDatabaseGuardError, match="no benchmark marker"):
        await bench_db.assert_benchmark_database(GuardPool("daemon_lme_bench", None), BENCH_DSN)
    with pytest.raises(BenchmarkDatabaseGuardError, match="no benchmark marker"):
        await bench_db.assert_benchmark_database(
            GuardPool("daemon_lme_bench", "daemon-production"), BENCH_DSN
        )
    with pytest.raises(BenchmarkDatabaseGuardError, match="not the benchmark database"):
        await bench_db.assert_benchmark_database(GuardPool("daemon", marker), BENCH_DSN)


def _settings(dsn: str = BENCH_DSN) -> SimpleNamespace:
    return SimpleNamespace(
        database_url=dsn,
        daemon_encryption_key="test-key",
        embedding_query_model="voyage-4-lite",
        embedding_dimensions=1024,
        daemon_inference_policy=None,
        daemon_model_routing=None,
        embedding_route_id="",
    )


@pytest.mark.asyncio
async def test_runner_refuses_production_dsn_before_connecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text("[]")
    runner = _runner(tmp_path, dataset_path)
    create_pool = AsyncMock()
    monkeypatch.setattr(
        fact_harness, "get_settings", lambda: _settings("postgresql://d:d@h/daemon")
    )
    monkeypatch.setattr(fact_harness.asyncpg, "create_pool", create_pool)
    with pytest.raises(BenchmarkDatabaseGuardError):
        await runner.ingest()
    with pytest.raises(BenchmarkDatabaseGuardError):
        await runner.evaluate()
    create_pool.assert_not_called()
    assert not runner.checkpoint_path.parent.exists()


@pytest.mark.asyncio
async def test_runner_refuses_unmarked_database_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text("[]")
    runner = _runner(tmp_path, dataset_path)
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"name": "daemon_lme_bench", "marker": None})
    pool.execute = AsyncMock()
    pool.close = AsyncMock()
    create_user = AsyncMock()
    monkeypatch.setattr(fact_harness, "get_settings", lambda: _settings())
    monkeypatch.setattr(fact_harness.asyncpg, "create_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(fact_harness, "ensure_benchmark_user", create_user)
    monkeypatch.setattr(fact_harness, "git_state", lambda root=None: {"sha": "abc", "dirty": False})
    with pytest.raises(BenchmarkDatabaseGuardError):
        await runner.ingest()
    create_user.assert_not_awaited()
    pool.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_cleanup_refuses_production_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.longmemeval import ingest

    create_pool = AsyncMock()
    monkeypatch.setattr(ingest, "get_settings", lambda: _settings("postgresql://d:d@h/daemon"))
    monkeypatch.setattr(ingest.asyncpg, "create_pool", create_pool)
    with pytest.raises(BenchmarkDatabaseGuardError):
        await ingest.run_ingestion(cleanup=True)
    create_pool.assert_not_called()


# -------------------------------------------------------------------- durable io
def test_atomic_write_keeps_previous_version_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "checkpoint.json"
    durable_io.atomic_write_json(path, {"v": 1})

    def broken_replace(src: Any, dst: Any) -> None:
        raise OSError("disk unplugged")

    monkeypatch.setattr(durable_io.os, "replace", broken_replace)
    with pytest.raises(OSError):
        durable_io.atomic_write_json(path, {"v": 2})
    assert json.loads(path.read_text()) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["checkpoint.json"]


def test_append_jsonl_fsyncs_every_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    synced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(durable_io.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd)))
    path = tmp_path / "results.jsonl"
    durable_io.append_jsonl_record(path, {"question_id": "q1"})
    durable_io.append_jsonl_record(path, {"question_id": "q2"})
    assert len(synced) == 2
    assert [r["question_id"] for r in durable_io.read_jsonl_records(path)] == ["q1", "q2"]


def test_read_jsonl_cuts_torn_tail_and_keeps_complete_records(tmp_path: Path) -> None:
    path = tmp_path / "results.jsonl"
    path.write_text('{"question_id": "q1"}\n{"question_id": "q2"}\n{"question_id": "q3", "hyp')
    records = durable_io.read_jsonl_records(path)
    assert [r["question_id"] for r in records] == ["q1", "q2"]
    assert path.read_text() == '{"question_id": "q1"}\n{"question_id": "q2"}\n'

    path.write_text('{"question_id": "q1"}\n{"question_id": "q2"}')
    assert len(durable_io.read_jsonl_records(path)) == 2
    assert path.read_text().endswith("}\n")


def test_read_jsonl_rejects_mid_file_corruption(tmp_path: Path) -> None:
    path = tmp_path / "results.jsonl"
    path.write_text('{"question_id": "q1"}\nnot-json\n{"question_id": "q3"}\n')
    with pytest.raises(ValueError, match="Corrupt JSONL record at line 2"):
        durable_io.read_jsonl_records(path)


# ------------------------------------------------------------------ runner setup
def _runner(tmp_path: Path, dataset_path: Path, **kwargs: Any) -> LongMemEvalFactRunner:
    out = kwargs.pop("output_dir", tmp_path / "run")
    return LongMemEvalFactRunner(
        dataset_path=dataset_path,
        output_path=out / "longmemeval_results.jsonl",
        checkpoint_path=out / "longmemeval_checkpoint.json",
        score_path=out / "longmemeval_score.json",
        **kwargs,
    )


SESSION_A = [{"role": "user", "content": "I adopted a cat named Miso"}]
SESSION_B = [{"role": "user", "content": "I fly out of Adelaide"}]
KEY_A = build_corpus_key(SESSION_A)
KEY_B = build_corpus_key(SESSION_B)
USER_Q1 = bench_db.question_user_id("q1")
USER_Q2 = bench_db.question_user_id("q2")
CONV_Q1_A = str(uuid.uuid4())
CONV_Q2_A = str(uuid.uuid4())
CONV_Q2_B = str(uuid.uuid4())


def _key(question_id: str, corpus_key: str) -> str:
    return fact_harness.ingest_result_key(question_id, corpus_key)


def _ingest_result(
    question_id: str, corpus_key: str, conversation_id: str, status: str = "complete"
) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "user_id": str(bench_db.question_user_id(question_id)),
        "corpus_key": corpus_key,
        "conversation_id": conversation_id,
        "status": status,
    }


def _full_ingest() -> dict[str, dict[str, Any]]:
    """q1's user holds session A; q2's user holds its own copies of A and B."""
    return {
        _key("q1", KEY_A): _ingest_result("q1", KEY_A, CONV_Q1_A),
        _key("q2", KEY_A): _ingest_result("q2", KEY_A, CONV_Q2_A),
        _key("q2", KEY_B): _ingest_result("q2", KEY_B, CONV_Q2_B),
    }


DATASET = [
    {
        "question_id": "q1",
        "question": "What is my cat called?",
        "answer": "Miso",
        "question_type": "single-session-user",
        "question_date": "2023/05/20 (Sat) 02:21",
        "haystack_session_ids": ["s-a"],
        "haystack_sessions": [SESSION_A],
    },
    {
        "question_id": "q2",
        "question": "Where do I fly from?",
        "answer": "Adelaide",
        "question_type": "multi-session",
        "question_date": "2023/05/21 (Sun) 09:00",
        "haystack_session_ids": ["s-a", "s-b"],
        "haystack_sessions": [SESSION_A, SESSION_B],
    },
]


def _write_dataset(tmp_path: Path) -> Path:
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(DATASET))
    return path


def _ingested_checkpoint(dataset_path: Path, *, fingerprint: dict[str, Any] | None = None) -> dict:
    return {
        "version": CHECKPOINT_VERSION,
        "run_id": RUN_ID,
        "dataset_path": str(dataset_path),
        "dataset_sha256": durable_io.file_sha256(dataset_path),
        "phases": {
            "ingest": {
                "status": "completed",
                "started_at": "2026-10-26T00:00:00+00:00",
                "results": _full_ingest(),
                "state_fingerprint": fingerprint or FP_INGEST,
            },
            "evaluate": {"results": {}},
            "score": {"accuracy": {}},
        },
    }


@asynccontextmanager
async def _null_scope() -> AsyncIterator[None]:
    yield None


class BenchRuntime:
    """Stubs for the runner's database-side helpers; records what they saw."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        conversations: dict[uuid.UUID, set[str]] | None = None,
        fingerprints: list[dict[str, Any]] | None = None,
        git_sha: str = "abc",
    ) -> None:
        self.conversations = {user: set(ids) for user, ids in (conversations or {}).items()}
        self.users: list[uuid.UUID] = []
        self.rollbacks: list[tuple[uuid.UUID, set[str]]] = []
        self.wipes: list[uuid.UUID] = []
        self.fingerprints = list(fingerprints or [FP_INGEST])
        # What the memory tables hash to after a rollback (exact by default).
        self.cross_session_fingerprint = FP_PRE_SESSION
        self.git = {"sha": git_sha, "dirty": False, "code_sha256": "c" * 64}
        self.scopes: list[dict[str, Any]] = []
        self.settings = _settings()
        pool = MagicMock()
        pool.close = AsyncMock()
        target = fact_harness
        monkeypatch.setattr(target, "get_settings", lambda: self.settings)
        monkeypatch.setattr(target.asyncpg, "create_pool", AsyncMock(return_value=pool))
        monkeypatch.setattr(target, "ContentEncryption", lambda key: object())
        monkeypatch.setattr(target, "MemoryStore", lambda db_pool, encryption: MagicMock())
        monkeypatch.setattr(target, "assert_benchmark_database", AsyncMock())
        monkeypatch.setattr(target, "ensure_benchmark_user", self._ensure_user)
        monkeypatch.setattr(target, "git_state", lambda root=None: dict(self.git))
        monkeypatch.setattr(target, "list_benchmark_conversation_ids", self._conversations)
        monkeypatch.setattr(target, "rollback_orphan_conversations", self._rollback)
        monkeypatch.setattr(target, "wipe_benchmark_user", self._wipe)
        monkeypatch.setattr(target, "fingerprint_benchmark_state", self._user_fingerprint)
        monkeypatch.setattr(target, "fingerprint_benchmark_users", self._fingerprint)
        monkeypatch.setattr(
            target,
            "summarize_ingest_routes",
            AsyncMock(return_value={"extraction_models": {"luna": 2}, "memory_embeddings": {}}),
        )
        monkeypatch.setattr(target, "summarize_compute_spend", AsyncMock(return_value={}))
        self.attestation_stop = AsyncMock()
        self.approved_routes = ["luna-azure-eu"]
        monkeypatch.setattr(
            target,
            "prepare_service_runtime",
            AsyncMock(return_value={"routes": 1, "attested": 1, "revoked": 0, "check_failed": 0}),
        )
        monkeypatch.setattr(target.attestation, "stop", self.attestation_stop)
        monkeypatch.setattr(target, "approved_profile_routes", lambda profile: self.approved_routes)

        def fake_account_compute(pool: Any, user_id: Any, **kwargs: Any) -> Any:
            self.scopes.append(kwargs)
            return _null_scope()

        monkeypatch.setattr(target, "account_compute", fake_account_compute)
        monkeypatch.setattr(target, "print_results", lambda results, accuracy: None)

    async def _ensure_user(self, pool: Any, user_id: uuid.UUID) -> None:
        self.users.append(user_id)

    async def _conversations(self, pool: Any, user_id: uuid.UUID) -> set[str]:
        return set(self.conversations.get(user_id, set()))

    async def _rollback(self, pool: Any, user_id: uuid.UUID, ids: set[str]) -> dict[str, int]:
        self.rollbacks.append((user_id, set(ids)))
        self.conversations.get(user_id, set()).difference_update(ids)
        return {"conversations": len(ids), "memories": 3, "memory_extraction_log": 1}

    async def _wipe(self, pool: Any, user_id: uuid.UUID, statements: Any) -> dict[str, int]:
        self.wipes.append(user_id)
        removed = len(self.conversations.pop(user_id, set()))
        return {"conversations": removed, "memories": 7}

    async def _user_fingerprint(
        self, pool: Any, user_id: uuid.UUID, *, tables: Any = None
    ) -> dict[str, Any]:
        """One user's memory state before a session / after its rollback."""
        return self.cross_session_fingerprint

    async def _fingerprint(self, pool: Any, user_ids: Any, *, tables: Any = None) -> dict[str, Any]:
        return self.fingerprints.pop(0) if len(self.fingerprints) > 1 else self.fingerprints[0]


def _result(question_id: str, judgment: str = "correct", **extra: Any) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "question": "?",
        "reference": "r",
        "hypothesis": "h",
        "category": "IE-user",
        "judgment": judgment,
        "retrieval_mode": "lexical",
        "answer_usage": {"prompt_tokens": 10, "completion_tokens": 2},
        "answer_fingerprint": "fp_a",
        "judge_fingerprint": "fp_j",
        "run_id": RUN_ID,
        **extra,
    }


# ----------------------------------------------------------------- evaluate resume
@pytest.mark.asyncio
async def test_evaluate_adopts_flushed_result_and_never_reruns_or_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path)
    runner.checkpoint_path.parent.mkdir(parents=True)
    runner.checkpoint_path.write_text(json.dumps(_ingested_checkpoint(dataset_path)))
    # Killed after q1's result line was fsynced but before the checkpoint save,
    # with a duplicate line and a torn trailing append.
    runner.output_path.write_text(
        json.dumps(_result("q1")) + "\n" + json.dumps(_result("q1")) + "\n" + '{"question_id": "q2'
    )
    BenchRuntime(monkeypatch)
    seen: list[str] = []

    async def fake_eval(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs["question_id"])
        return _result(kwargs["question_id"])

    monkeypatch.setattr(fact_harness, "evaluate_single_parity", fake_eval)
    results = await runner.evaluate()

    assert seen == ["q2"]
    assert [r["question_id"] for r in results] == ["q1", "q2"]
    lines = [json.loads(line) for line in runner.output_path.read_text().splitlines()]
    assert [r["question_id"] for r in lines] == ["q1", "q2"]
    manifest = json.loads((runner.output_path.parent / "run_manifest.json").read_text())
    reconciliation = manifest["results_reconciliations"][0]
    assert reconciliation["adopted_from_file"] == 1
    assert reconciliation["duplicates_dropped"] == 1
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    assert checkpoint["phases"]["evaluate"]["completed_count"] == 2


@pytest.mark.asyncio
async def test_evaluate_retries_errored_questions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path)
    checkpoint = _ingested_checkpoint(dataset_path)
    checkpoint["phases"]["evaluate"]["results"] = {
        "q1": _result("q1", "incorrect", error="BenchmarkProviderError", attempt=1),
        "q2": _result("q2"),
    }
    runner.checkpoint_path.parent.mkdir(parents=True)
    runner.checkpoint_path.write_text(json.dumps(checkpoint))
    BenchRuntime(monkeypatch)
    calls: list[str] = []

    async def fake_eval(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["question_id"])
        return _result(kwargs["question_id"])

    monkeypatch.setattr(fact_harness, "evaluate_single_parity", fake_eval)
    results = await runner.evaluate()
    assert calls == ["q1"]
    assert results[0]["attempt"] == 2
    assert all(fact_harness.is_completed_result(r) for r in results)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "rate_limit"])
@pytest.mark.parametrize("reentry", ["checkpoint", "flushed_only"])
async def test_official_failure_preserves_base_observation_and_never_replays_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, reentry: str
) -> None:
    from orchestrator.memory import injection
    from tests.longmemeval import evaluate

    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path)
    runner.checkpoint_path.parent.mkdir(parents=True)
    runner.checkpoint_path.write_text(json.dumps(_ingested_checkpoint(dataset_path)))
    BenchRuntime(monkeypatch)
    retrievals: list[str] = []
    calls: list[str] = []

    async def gather(store: Any, **kwargs: Any) -> injection.MemoryContextInputs:
        retrievals.append(kwargs["query_text"])
        return injection.MemoryContextInputs(
            retrieved=[{"content": "User adopted a cat named Miso", "category": "fact"}],
            retrieval_mode="lexical",
        )

    class RateLimitError(Exception):
        status_code = 429

    async def llm(**kwargs: Any) -> dict[str, Any]:
        key = kwargs["bm_call_key"]
        calls.append(key)
        if key == "official_judge":
            cause = (
                RateLimitError("DO-NOT-STORE-PROVIDER-TEXT")
                if failure == "rate_limit"
                else TimeoutError("DO-NOT-STORE-PROVIDER-TEXT")
            )
            raise evaluate.BenchmarkProviderError("DO-NOT-STORE-WRAPPER-TEXT") from cause
        response = {
            "choices": [
                {
                    "message": {"content": "Miso" if key == "answer" else "CORRECT\nMatches."},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 3, "total_tokens": 103},
            "system_fingerprint": f"fp_{key}",
        }
        evaluate._capture_benchmark_metadata(response, key=key)
        return response

    monkeypatch.setattr(evaluate, "gather_memory_context_inputs", gather)
    monkeypatch.setattr(evaluate, "_call_llm_with_provider_config", llm)
    results = await runner.evaluate()
    assert len(results) == len(retrievals) == 2
    assert calls == ["answer", "judge", "official_judge"] * 2
    for row in results:
        assert row["hypothesis"] == "Miso" and row["judgment"] == "correct"
        assert "Miso" in row["memory_context"]
        assert row["memory_context_sha256"] == evaluate._sha256_text(row["memory_context"])
        assert row["as_of"] in ("2023-05-20T02:21:00+00:00", "2023-05-21T09:00:00+00:00")
        assert row["system_prompt_sha256"] == evaluate._sha256_text(
            evaluate.build_parity_answer_messages(
                row["question"],
                row["memory_context"],
                as_of=datetime.fromisoformat(row["as_of"]),
            )[0]["content"]
        )
        assert (
            row["answer_usage"]
            == row["judge_usage"]
            == {"prompt_tokens": 100, "completion_tokens": 3, "total_tokens": 103}
        )
        assert row["answer_fingerprint"] == "fp_answer"
        assert row["judge_fingerprint"] == "fp_judge"
        assert row["judge_raw_output"] == "CORRECT\nMatches."
        assert "error" not in row and "official_judgment" not in row
        assert row["official_judge_status"] == "failed"
        assert row["official_judge_usage"] is None
        assert row["official_judge_failure_diagnostics"] == {
            "provider_exception_type": "RateLimitError"
            if failure == "rate_limit"
            else "TimeoutError",
            "http_status_code": 429 if failure == "rate_limit" else None,
        }
        assert fact_harness.is_completed_result(row)
    assert "DO-NOT-STORE" not in runner.output_path.read_text()
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    assert checkpoint["phases"]["evaluate"]["completed_count"] == 2
    assert checkpoint["phases"]["evaluate"]["error_count"] == 0
    assert list(checkpoint["phases"]["evaluate"]["results"].values()) == results
    manifest = json.loads((runner.output_path.parent / "run_manifest.json").read_text())
    phase = manifest["phases"]["evaluate"]
    assert (
        phase["answer_usage"]
        == phase["judge_usage"]
        == {"prompt_tokens": 200, "completion_tokens": 6, "total_tokens": 206}
    )
    assert phase["official_judge_failed"] == 2
    assert phase["official_judgments"] == {
        "total": 2,
        "judged": 0,
        "correct": 0,
        "strict_accuracy": None,
        "missing": 2,
    }
    score = runner.score()
    assert score["strict_accuracy"] == 1 and score["error_count"] == 0
    assert score["dual_judgments"]["official"]["missing"] == 2
    assert score["dual_judgments"]["official"]["judged"] == 0
    assert score["dual_judgments"]["official"]["strict_accuracy"] is None

    if reentry == "flushed_only":
        checkpoint["phases"]["evaluate"]["results"] = {}
        checkpoint["phases"]["evaluate"]["completed_count"] = 0
        runner.checkpoint_path.write_text(json.dumps(checkpoint))
    resumed = _runner(tmp_path, dataset_path)
    assert await resumed.evaluate() == results
    assert calls == ["answer", "judge", "official_judge"] * 2
    assert len(retrievals) == 2
    assert len(runner.output_path.read_text().splitlines()) == 2


@pytest.mark.asyncio
async def test_stop_request_finishes_in_flight_question_then_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    stop = StopController()
    runner = _runner(tmp_path, dataset_path, stop=stop)
    runner.checkpoint_path.parent.mkdir(parents=True)
    runner.checkpoint_path.write_text(json.dumps(_ingested_checkpoint(dataset_path)))
    BenchRuntime(monkeypatch)
    calls: list[str] = []

    async def fake_eval(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["question_id"])
        stop.request(signal.SIGTERM)  # arrives while q1 is in flight
        return _result(kwargs["question_id"])

    monkeypatch.setattr(fact_harness, "evaluate_single_parity", fake_eval)
    with pytest.raises(HarnessInterrupted):
        await runner.evaluate()
    assert calls == ["q1"]
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    assert checkpoint["phases"]["evaluate"]["status"] == "interrupted"
    assert list(checkpoint["phases"]["evaluate"]["results"]) == ["q1"]
    manifest_path = runner.output_path.parent / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["invocations"][-1]["outcome"] == "interrupted:SIGTERM"
    assert manifest["invocations"][-1]["resumed"] is False

    resumed = _runner(tmp_path, dataset_path)
    results = await resumed.evaluate()
    assert calls == ["q1", "q2"]
    assert [r["question_id"] for r in results] == ["q1", "q2"]
    manifest = json.loads(manifest_path.read_text())
    assert manifest["invocations"][-1]["resumed"] is True
    assert manifest["invocations"][-1]["outcome"] == "completed"


@pytest.mark.asyncio
async def test_run_interruptible_turns_signals_into_a_stop_request() -> None:
    async def body(controller: StopController) -> str:
        os.kill(os.getpid(), signal.SIGTERM)
        for _ in range(50):
            if controller.requested:
                break
            await asyncio.sleep(0.01)
        controller.raise_if_requested()
        return "not reached"

    result, controller = await run_interruptible(body)
    assert result is None
    assert controller.signal_name == "SIGTERM"


# ------------------------------------------------------------------ ingest resume
def _ingest_checkpoint(
    dataset_path: Path,
    results: dict[str, Any],
    *,
    pending: dict[str, str] | None = None,
) -> dict[str, Any]:
    """``pending`` maps a question id to the corpus key it had in flight."""
    checkpoint = _ingested_checkpoint(dataset_path)
    checkpoint["phases"]["ingest"] = {
        "status": "interrupted",
        "started_at": "2026-10-26T00:00:00+00:00",
        "results": results,
        "pending_sessions": {
            question_id: {
                "corpus_key": corpus_key,
                "started_at": "2026-10-26T00:05:00+00:00",
                "state_fingerprint": FP_PRE_SESSION,
            }
            for question_id, corpus_key in (pending or {}).items()
        },
    }
    return checkpoint


@pytest.mark.asyncio
async def test_ingest_rolls_back_interrupted_session_before_resuming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path)
    runner.checkpoint_path.parent.mkdir(parents=True)
    runner.checkpoint_path.write_text(
        json.dumps(
            _ingest_checkpoint(
                dataset_path,
                {
                    _key("q1", KEY_A): _ingest_result("q1", KEY_A, CONV_Q1_A),
                    _key("q2", KEY_A): _ingest_result("q2", KEY_A, CONV_Q2_A),
                },
                pending={"q2": KEY_B},
            )
        )
    )
    orphan = str(uuid.uuid4())
    runtime = BenchRuntime(
        monkeypatch, conversations={USER_Q1: {CONV_Q1_A}, USER_Q2: {CONV_Q2_A, orphan}}
    )
    ingested: list[tuple[str, uuid.UUID]] = []

    async def fake_ingest(**kwargs: Any) -> dict[str, Any]:
        ingested.append((kwargs["session_id"], kwargs["user_id"]))
        await kwargs["on_conversation_created"](uuid.UUID(CONV_Q2_B))
        async with kwargs["scope_factory"]():
            pass
        return {
            "session_id": kwargs["session_id"],
            "conversation_id": CONV_Q2_B,
            "status": "complete",
        }

    monkeypatch.setattr(fact_harness, "ingest_session_parity", fake_ingest)
    await runner.ingest()

    assert runtime.rollbacks == [(USER_Q2, {orphan})]
    assert ingested == [("s-b", USER_Q2)]
    assert runtime.scopes == [
        {"operation": "agent", "auto_route": True, "background": True, "profile": "background"}
    ]
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    ingest_phase = checkpoint["phases"]["ingest"]
    assert ingest_phase["status"] == "completed"
    assert ingest_phase["state_fingerprint"] == FP_INGEST
    assert ingest_phase["results"] == {
        **_full_ingest(),
        _key("q2", KEY_B): {
            **ingest_phase["results"][_key("q2", KEY_B)],
            **_ingest_result("q2", KEY_B, CONV_Q2_B),
        },
    }
    manifest = json.loads((runner.output_path.parent / "run_manifest.json").read_text())
    assert manifest["ingest_rollbacks"][0]["question_id"] == "q2"
    assert manifest["ingest_rollbacks"][0]["orphan_conversations"] == 1
    assert manifest["ingest_rollbacks"][0]["restored_pre_session_state"] is True
    assert ingest_phase["pending_sessions"] == {}
    assert manifest["baseline_qualified"] is True
    assert manifest["routes"]["extraction"]["observed_models"] == {"luna": 2}


@pytest.mark.asyncio
async def test_inexact_rollback_rebuilds_only_that_question_from_its_first_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path)
    runner.checkpoint_path.parent.mkdir(parents=True)
    runner.checkpoint_path.write_text(
        json.dumps(
            _ingest_checkpoint(
                dataset_path,
                {
                    _key("q1", KEY_A): _ingest_result("q1", KEY_A, CONV_Q1_A),
                    _key("q2", KEY_A): _ingest_result("q2", KEY_A, CONV_Q2_A),
                },
                pending={"q2": KEY_B},
            )
        )
    )
    runtime = BenchRuntime(
        monkeypatch,
        conversations={USER_Q1: {CONV_Q1_A}, USER_Q2: {CONV_Q2_A, str(uuid.uuid4())}},
    )
    # The interrupted session superseded session A's memory: deleting its own
    # rows leaves that change behind.
    runtime.cross_session_fingerprint = {"sha256": "d" * 64, "tables": {}}
    ingested: list[tuple[str, uuid.UUID]] = []

    async def fake_ingest(**kwargs: Any) -> dict[str, Any]:
        ingested.append((kwargs["session_id"], kwargs["user_id"]))
        return {
            "session_id": kwargs["session_id"],
            "conversation_id": str(uuid.uuid4()),
            "status": "complete",
        }

    monkeypatch.setattr(fact_harness, "ingest_session_parity", fake_ingest)
    await runner.ingest()

    # Only q2's user is wiped, and q2 is re-ingested from its first session.
    assert runtime.wipes == [USER_Q2]
    assert ingested == [("s-a", USER_Q2), ("s-b", USER_Q2)]
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    ingest_phase = checkpoint["phases"]["ingest"]
    assert ingest_phase["status"] == "completed"
    assert ingest_phase["pending_sessions"] == {}
    assert ingest_phase["results"][_key("q1", KEY_A)] == _ingest_result("q1", KEY_A, CONV_Q1_A)
    assert ingest_phase["results"][_key("q2", KEY_A)]["conversation_id"] != CONV_Q2_A
    manifest = json.loads((runner.output_path.parent / "run_manifest.json").read_text())
    assert manifest["ingest_rollbacks"][0]["restored_pre_session_state"] is False
    rebuild = manifest["question_rebuilds"][0]
    assert rebuild["question_id"] == "q2"
    assert rebuild["trigger_corpus_key"] == KEY_B
    assert rebuild["sessions_discarded"] == 1
    assert rebuild["after_failure"] is False
    assert manifest["baseline_qualified"] is True


@pytest.mark.asyncio
async def test_rebuild_refuses_when_the_wipe_leaves_rows_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path)
    runner.checkpoint_path.parent.mkdir(parents=True)
    runner.checkpoint_path.write_text(
        json.dumps(
            _ingest_checkpoint(
                dataset_path,
                {
                    _key("q1", KEY_A): _ingest_result("q1", KEY_A, CONV_Q1_A),
                    _key("q2", KEY_A): _ingest_result("q2", KEY_A, CONV_Q2_A),
                },
                pending={"q2": KEY_B},
            )
        )
    )
    runtime = BenchRuntime(
        monkeypatch,
        conversations={USER_Q1: {CONV_Q1_A}, USER_Q2: {CONV_Q2_A, str(uuid.uuid4())}},
    )
    runtime.cross_session_fingerprint = {
        "sha256": "d" * 64,
        "tables": {"memories": {"rows": 2, "md5": "x"}},
    }
    fake_ingest = AsyncMock()
    monkeypatch.setattr(fact_harness, "ingest_session_parity", fake_ingest)
    with pytest.raises(RuntimeError, match="left rows behind"):
        await runner.ingest()
    fake_ingest.assert_not_awaited()
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    # The checkpoint still holds the question, so a later resume tries again.
    assert checkpoint["phases"]["ingest"]["pending_sessions"]["q2"]["corpus_key"] == KEY_B
    assert _key("q2", KEY_A) in checkpoint["phases"]["ingest"]["results"]


@pytest.mark.asyncio
async def test_death_after_the_wipe_commits_rebuilds_again_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path)
    runner.checkpoint_path.parent.mkdir(parents=True)
    runner.checkpoint_path.write_text(
        json.dumps(
            _ingest_checkpoint(
                dataset_path,
                {
                    _key("q1", KEY_A): _ingest_result("q1", KEY_A, CONV_Q1_A),
                    _key("q2", KEY_A): _ingest_result("q2", KEY_A, CONV_Q2_A),
                },
                pending={"q2": KEY_B},
            )
        )
    )
    runtime = BenchRuntime(
        monkeypatch,
        conversations={USER_Q1: {CONV_Q1_A}, USER_Q2: {CONV_Q2_A, str(uuid.uuid4())}},
    )
    # An empty user never matches the state before session B.
    runtime.cross_session_fingerprint = {"sha256": "d" * 64, "tables": {}}

    class Died(BaseException):
        pass

    # A SIGKILL: nothing reaches disk after the wipe, not even the shutdown save.
    real_save_manifest = fact_harness.save_manifest
    real_save_checkpoint = fact_harness.save_runner_checkpoint
    dead = False

    def die_after_the_wipe(path: Any, manifest: dict[str, Any]) -> None:
        nonlocal dead
        if dead:
            return
        if runtime.wipes and "question_rebuilds" in manifest:
            dead = True
            raise Died
        real_save_manifest(path, manifest)

    def save_checkpoint_unless_dead(path: Any, checkpoint: dict[str, Any]) -> None:
        if not dead:
            real_save_checkpoint(path, checkpoint)

    monkeypatch.setattr(fact_harness, "save_manifest", die_after_the_wipe)
    monkeypatch.setattr(fact_harness, "save_runner_checkpoint", save_checkpoint_unless_dead)
    fake_ingest = AsyncMock()
    monkeypatch.setattr(fact_harness, "ingest_session_parity", fake_ingest)
    with pytest.raises(Died):
        await runner.ingest()
    fake_ingest.assert_not_awaited()
    # The wipe committed, but the checkpoint still points at the wiped conversations.
    assert runtime.wipes == [USER_Q2]
    assert USER_Q2 not in runtime.conversations
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    assert checkpoint["phases"]["ingest"]["pending_sessions"]["q2"]["corpus_key"] == KEY_B
    assert _key("q2", KEY_A) in checkpoint["phases"]["ingest"]["results"]

    monkeypatch.setattr(fact_harness, "save_manifest", real_save_manifest)
    monkeypatch.setattr(fact_harness, "save_runner_checkpoint", real_save_checkpoint)
    ingested: list[tuple[str, uuid.UUID]] = []

    async def fake_resume_ingest(**kwargs: Any) -> dict[str, Any]:
        ingested.append((kwargs["session_id"], kwargs["user_id"]))
        return {
            "session_id": kwargs["session_id"],
            "conversation_id": str(uuid.uuid4()),
            "status": "complete",
        }

    monkeypatch.setattr(fact_harness, "ingest_session_parity", fake_resume_ingest)
    await _runner(tmp_path, dataset_path).ingest()

    # Resume finds no orphan, cannot match the pre-session state, and rebuilds again.
    assert runtime.wipes == [USER_Q2, USER_Q2]
    assert ingested == [("s-a", USER_Q2), ("s-b", USER_Q2)]
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    ingest_phase = checkpoint["phases"]["ingest"]
    assert ingest_phase["status"] == "completed"
    assert ingest_phase["pending_sessions"] == {}
    assert ingest_phase["results"][_key("q1", KEY_A)] == _ingest_result("q1", KEY_A, CONV_Q1_A)
    assert ingest_phase["results"][_key("q2", KEY_A)]["conversation_id"] != CONV_Q2_A
    manifest = json.loads((runner.output_path.parent / "run_manifest.json").read_text())
    assert manifest["question_rebuilds"][-1]["question_id"] == "q2"
    assert manifest["question_rebuilds"][-1]["sessions_discarded"] == 1
    assert manifest["baseline_qualified"] is True


@pytest.mark.asyncio
async def test_ingest_refuses_database_not_built_by_this_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path)
    runtime = BenchRuntime(monkeypatch, conversations={USER_Q2: {str(uuid.uuid4())}})
    fake_ingest = AsyncMock()
    monkeypatch.setattr(fact_harness, "ingest_session_parity", fake_ingest)
    with pytest.raises(RuntimeError, match="not built by this checkpoint"):
        await runner.ingest()
    assert runtime.rollbacks == []
    fake_ingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_failed_sessions_rolls_back_and_reingests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path, retry_failed_sessions=True)
    runner.checkpoint_path.parent.mkdir(parents=True)
    runner.checkpoint_path.write_text(
        json.dumps(
            _ingest_checkpoint(
                dataset_path,
                {
                    **_full_ingest(),
                    _key("q2", KEY_B): _ingest_result(
                        "q2", KEY_B, CONV_Q2_B, status="extraction_failed"
                    ),
                },
            )
        )
    )
    runtime = BenchRuntime(
        monkeypatch, conversations={USER_Q1: {CONV_Q1_A}, USER_Q2: {CONV_Q2_A, CONV_Q2_B}}
    )
    new_conv = str(uuid.uuid4())

    async def fake_ingest(**kwargs: Any) -> dict[str, Any]:
        return {
            "session_id": kwargs["session_id"],
            "conversation_id": new_conv,
            "status": "complete",
        }

    monkeypatch.setattr(fact_harness, "ingest_session_parity", fake_ingest)
    await runner.ingest()
    assert runtime.rollbacks == [(USER_Q2, {CONV_Q2_B})]
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    retried = checkpoint["phases"]["ingest"]["results"][_key("q2", KEY_B)]
    assert retried["conversation_id"] == new_conv
    manifest = json.loads((runner.output_path.parent / "run_manifest.json").read_text())
    assert manifest["baseline_qualified"] is False
    assert manifest["disqualifications"] == ["retried_failed_sessions"]


@pytest.mark.asyncio
async def test_ingest_refuses_without_an_approved_extraction_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path)
    runtime = BenchRuntime(monkeypatch)
    runtime.approved_routes = []
    fake_ingest = AsyncMock()
    monkeypatch.setattr(fact_harness, "ingest_session_parity", fake_ingest)
    with pytest.raises(RuntimeError, match="No approved inference route"):
        await runner.ingest()
    fake_ingest.assert_not_awaited()
    runtime.attestation_stop.assert_awaited()
    manifest = json.loads((runner.output_path.parent / "run_manifest.json").read_text())
    assert manifest["route_attestations"][0]["attested"] == 1
    assert manifest["invocations"][-1]["outcome"] == "failed:RuntimeError"


# ------------------------------------------------------- per-question users
def _dated_entry(question_id: str, sessions: list[tuple[str, str | None]]) -> dict[str, Any]:
    """A question whose haystack holds ``(text, haystack_date)`` sessions in dataset order."""
    return {
        "question_id": question_id,
        "question": "?",
        "answer": "a",
        "question_type": "multi-session",
        "question_date": "2023/05/20 (Sat) 02:21",
        "haystack_session_ids": [f"{question_id}-{text}" for text, _ in sessions],
        "haystack_sessions": [[{"role": "user", "content": text}] for text, _ in sessions],
        "haystack_dates": [date for _, date in sessions],
    }


def test_question_streams_ingest_in_haystack_date_order() -> None:
    (stream,) = fact_harness.build_question_streams(
        [
            _dated_entry(
                "q1",
                [
                    ("late", "2023/05/30 (Tue) 09:00"),
                    ("early", "2023/05/20 (Sat) 02:21"),
                    ("same-day-first", "2023/05/25 (Thu) 10:00"),
                    ("same-day-second", "2023/05/25 (Thu) 10:00"),
                ],
            )
        ]
    )
    assert [session.session_id for session in stream.sessions] == [
        "q1-early",
        "q1-same-day-first",
        "q1-same-day-second",
        "q1-late",
    ]
    assert stream.reordered is True
    assert stream.user_id == bench_db.question_user_id("q1")


def test_question_streams_keep_dataset_order_when_a_date_does_not_parse() -> None:
    (stream,) = fact_harness.build_question_streams(
        [_dated_entry("q1", [("b", "2023/05/30 (Tue) 09:00"), ("a", "sometime")])]
    )
    assert [session.session_id for session in stream.sessions] == ["q1-b", "q1-a"]
    assert stream.reordered is False


def test_question_streams_ingest_a_repeated_session_once_at_its_earliest_date() -> None:
    (stream,) = fact_harness.build_question_streams(
        [
            _dated_entry(
                "q1",
                [
                    ("x", "2023/05/30 (Tue) 09:00"),
                    ("y", "2023/05/21 (Sun) 09:00"),
                    ("x", "2023/05/20 (Sat) 09:00"),
                ],
            )
        ]
    )
    assert [session.haystack_date for session in stream.sessions] == [
        "2023/05/20 (Sat) 09:00",
        "2023/05/21 (Sun) 09:00",
    ]


def test_question_users_are_deterministic_and_distinct() -> None:
    assert bench_db.question_user_id("q1") == bench_db.question_user_id("q1")
    assert bench_db.question_user_id("q1") != bench_db.question_user_id("q2")
    assert bench_db.question_user_id("q1").version == 5


def _many_questions(tmp_path: Path, count: int, sessions: int) -> Path:
    dataset = [
        _dated_entry(
            f"q{n}",
            [(f"fact-{n}-{i}", f"2023/05/{10 + i:02d} (Wed) 09:00") for i in range(sessions)],
        )
        for n in range(count)
    ]
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(dataset))
    return path


@pytest.mark.asyncio
async def test_concurrent_ingest_runs_one_session_per_user_and_n_overall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _many_questions(tmp_path, count=5, sessions=3)
    runner = _runner(tmp_path, dataset_path, concurrency=3)
    runtime = BenchRuntime(monkeypatch)
    in_flight: dict[uuid.UUID, int] = {}
    peak = {"overall": 0, "per_user": 0}
    order: dict[uuid.UUID, list[str]] = {}

    async def fake_ingest(**kwargs: Any) -> dict[str, Any]:
        user = kwargs["user_id"]
        in_flight[user] = in_flight.get(user, 0) + 1
        peak["per_user"] = max(peak["per_user"], in_flight[user])
        peak["overall"] = max(peak["overall"], sum(in_flight.values()))
        await asyncio.sleep(0.01)
        in_flight[user] -= 1
        order.setdefault(user, []).append(kwargs["session_id"])
        return {
            "session_id": kwargs["session_id"],
            "conversation_id": str(uuid.uuid4()),
            "status": "complete",
        }

    monkeypatch.setattr(fact_harness, "ingest_session_parity", fake_ingest)
    await runner.ingest()

    assert peak == {"overall": 3, "per_user": 1}
    assert sorted(runtime.users) == sorted(bench_db.question_user_id(f"q{n}") for n in range(5))
    for n in range(5):
        assert order[bench_db.question_user_id(f"q{n}")] == [f"q{n}-fact-{n}-{i}" for i in range(3)]
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    assert checkpoint["phases"]["ingest"]["completed_count"] == 15
    assert checkpoint["phases"]["ingest"]["pending_sessions"] == {}
    manifest = json.loads((runner.output_path.parent / "run_manifest.json").read_text())
    assert manifest["invocations"][-1]["concurrency"] == 3
    assert manifest["phases"]["ingest"]["questions"] == 5


@pytest.mark.asyncio
async def test_stop_during_concurrent_ingest_finishes_in_flight_sessions_then_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _many_questions(tmp_path, count=4, sessions=2)
    stop = StopController()
    runner = _runner(tmp_path, dataset_path, concurrency=2, stop=stop)
    BenchRuntime(monkeypatch)
    started: list[str] = []
    finished: list[str] = []
    release = asyncio.Event()

    async def fake_ingest(**kwargs: Any) -> dict[str, Any]:
        started.append(kwargs["session_id"])
        if len(started) == 2:
            stop.request(signal.SIGTERM)  # arrives while two sessions are in flight
            release.set()
        await release.wait()
        finished.append(kwargs["session_id"])
        return {
            "session_id": kwargs["session_id"],
            "conversation_id": str(uuid.uuid4()),
            "status": "complete",
        }

    monkeypatch.setattr(fact_harness, "ingest_session_parity", fake_ingest)
    with pytest.raises(HarnessInterrupted):
        await runner.ingest()

    assert sorted(finished) == sorted(started) == ["q0-fact-0-0", "q1-fact-1-0"]
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    ingest_phase = checkpoint["phases"]["ingest"]
    assert ingest_phase["status"] == "interrupted"
    assert len(ingest_phase["results"]) == 2
    assert ingest_phase["pending_sessions"] == {}

    started.clear()
    release.set()
    await _runner(tmp_path, dataset_path, concurrency=2).ingest()
    assert len(started) == 6
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    assert checkpoint["phases"]["ingest"]["completed_count"] == 8


@pytest.mark.asyncio
async def test_a_failing_stream_lets_siblings_finish_their_session_and_stays_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _many_questions(tmp_path, count=2, sessions=2)
    runner = _runner(tmp_path, dataset_path, concurrency=2)
    runtime = BenchRuntime(monkeypatch)
    q0 = bench_db.question_user_id("q0")
    failed = asyncio.Event()
    fingerprints = {"q0": 0}
    calls: list[str] = []

    async def flaky_fingerprint(pool: Any, user_id: uuid.UUID, *, tables: Any = None) -> Any:
        if user_id == q0:
            fingerprints["q0"] += 1
            if fingerprints["q0"] == 2:  # before q0's second session
                failed.set()
                raise ConnectionError("database went away")
        return runtime.cross_session_fingerprint

    async def fake_ingest(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["session_id"])
        if kwargs["user_id"] == q0:
            await asyncio.sleep(0.01)  # lets q1's session start
        else:
            await failed.wait()  # still in flight when q0's stream fails
        return {
            "session_id": kwargs["session_id"],
            "conversation_id": str(uuid.uuid4()),
            "status": "complete",
        }

    monkeypatch.setattr(fact_harness, "fingerprint_benchmark_state", flaky_fingerprint)
    monkeypatch.setattr(fact_harness, "ingest_session_parity", fake_ingest)
    with pytest.raises(ConnectionError):
        await runner.ingest()

    assert calls == ["q0-fact-0-0", "q1-fact-1-0"]
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    ingest_phase = checkpoint["phases"]["ingest"]
    assert ingest_phase["status"] == "failed"
    assert {result["session_id"] for result in ingest_phase["results"].values()} == {
        "q0-fact-0-0",
        "q1-fact-1-0",
    }
    assert ingest_phase["pending_sessions"] == {}

    monkeypatch.setattr(fact_harness, "fingerprint_benchmark_state", runtime._user_fingerprint)
    failed.set()
    await _runner(tmp_path, dataset_path, concurrency=2).ingest()
    assert sorted(calls[2:]) == ["q0-fact-0-1", "q1-fact-1-1"]


def _single_question(tmp_path: Path, sessions: int) -> Path:
    dataset = [
        {
            "question_id": "q1",
            "question": "?",
            "answer": "a",
            "question_type": "multi-session",
            "question_date": "2023/05/20 (Sat) 02:21",
            "haystack_session_ids": [f"s-{i}" for i in range(sessions)],
            "haystack_sessions": [
                [{"role": "user", "content": f"fact number {i}"}] for i in range(sessions)
            ],
        }
    ]
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(dataset))
    return path


def _ingest_into(runtime: BenchRuntime, fail: dict[str, str] | None = None) -> Any:
    """A fake session ingest that creates a conversation in the runtime's DB.

    ``fail`` maps a session id to how it fails once: "returned" (extraction_failed
    result) or "raised" (an exception after the conversation exists).
    """
    pending_failures = dict(fail or {})

    async def fake_ingest(**kwargs: Any) -> dict[str, Any]:
        conversation = str(uuid.uuid4())
        runtime.conversations.setdefault(kwargs["user_id"], set()).add(conversation)
        await kwargs["on_conversation_created"](uuid.UUID(conversation))
        mode = pending_failures.pop(kwargs["session_id"], None)
        if mode == "raised":
            raise ConnectionError("provider down")
        return {
            "session_id": kwargs["session_id"],
            "conversation_id": conversation,
            "status": "extraction_failed" if mode == "returned" else "complete",
            "chunks_failed": 1 if mode == "returned" else 0,
            "chunk_errors": ["Timeout"] if mode == "returned" else [],
        }

    return fake_ingest


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["returned", "raised"])
async def test_a_failed_session_stops_the_run_and_resume_retries_it_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    dataset_path = _single_question(tmp_path, sessions=3)
    runner = _runner(tmp_path, dataset_path)
    runtime = BenchRuntime(monkeypatch)
    calls: list[str] = []
    fake = _ingest_into(runtime, fail={"s-1": mode})

    async def recording(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["session_id"])
        return await fake(**kwargs)

    monkeypatch.setattr(fact_harness, "ingest_session_parity", recording)
    with pytest.raises(fact_harness.IngestSessionFailed):
        await runner.ingest()

    assert calls == ["s-0", "s-1"]  # nothing after the failed session
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    ingest_phase = checkpoint["phases"]["ingest"]
    assert ingest_phase["status"] == "failed"
    assert [result["session_id"] for result in ingest_phase["results"].values()] == ["s-0"]
    failure = ingest_phase["pending_sessions"]["q1"]["failure"]
    assert failure["session_id"] == "s-1"
    assert failure["status"] == ("extraction_failed" if mode == "returned" else "error")
    manifest_path = runner.output_path.parent / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["ingest_failures"][0]["question_id"] == "q1"

    # A plain resume rolls the failed session back exactly and re-ingests it.
    await _runner(tmp_path, dataset_path).ingest()
    assert calls == ["s-0", "s-1", "s-1", "s-2"]
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    ingest_phase = checkpoint["phases"]["ingest"]
    assert ingest_phase["status"] == "completed"
    assert ingest_phase["pending_sessions"] == {}
    assert {result["status"] for result in ingest_phase["results"].values()} == {"complete"}
    manifest = json.loads(manifest_path.read_text())
    rollback = manifest["ingest_rollbacks"][0]
    assert rollback["after_failure"] is True
    assert rollback["restored_pre_session_state"] is True
    assert rollback["orphan_conversations"] == 1
    assert manifest["baseline_qualified"] is True


@pytest.mark.asyncio
async def test_a_failed_session_with_an_inexact_rollback_rebuilds_the_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _single_question(tmp_path, sessions=2)
    runner = _runner(tmp_path, dataset_path)
    runtime = BenchRuntime(monkeypatch)
    calls: list[str] = []
    fake = _ingest_into(runtime, fail={"s-1": "returned"})

    async def recording(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["session_id"])
        return await fake(**kwargs)

    monkeypatch.setattr(fact_harness, "ingest_session_parity", recording)
    with pytest.raises(fact_harness.IngestSessionFailed):
        await runner.ingest()

    # The failed session's partial facts superseded an earlier memory.
    runtime.cross_session_fingerprint = {"sha256": "d" * 64, "tables": {}}
    await _runner(tmp_path, dataset_path).ingest()

    assert calls == ["s-0", "s-1", "s-0", "s-1"]
    assert runtime.wipes == [bench_db.question_user_id("q1")]
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    assert checkpoint["phases"]["ingest"]["status"] == "completed"
    manifest = json.loads((runner.output_path.parent / "run_manifest.json").read_text())
    assert manifest["question_rebuilds"][0]["after_failure"] is True
    assert manifest["baseline_qualified"] is True
    assert manifest["disqualifications"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["returned", "raised"])
async def test_a_failing_question_is_never_masked_by_a_healthy_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    dataset_path = _many_questions(tmp_path, count=2, sessions=4)
    runner = _runner(tmp_path, dataset_path, concurrency=2)
    runtime = BenchRuntime(monkeypatch)
    fake = _ingest_into(runtime, fail={"q0-fact-0-1": mode})
    q0 = bench_db.question_user_id("q0")
    failing_started = asyncio.Event()
    calls: list[str] = []

    async def interleaved(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["session_id"])
        if kwargs["session_id"] == "q0-fact-0-1":
            failing_started.set()
        elif kwargs["user_id"] != q0 and kwargs["session_id"] != "q1-fact-1-0":
            # The healthy sibling is mid-session when q0 fails.
            await failing_started.wait()
            await asyncio.sleep(0.01)
        await asyncio.sleep(0)
        return await fake(**kwargs)

    monkeypatch.setattr(fact_harness, "ingest_session_parity", interleaved)
    with pytest.raises(fact_harness.IngestSessionFailed):
        await runner.ingest()

    checkpoint = json.loads(runner.checkpoint_path.read_text())
    ingest_phase = checkpoint["phases"]["ingest"]
    recorded = {result["session_id"] for result in ingest_phase["results"].values()}
    started_after_failure = calls[calls.index("q0-fact-0-1") + 1 :]
    # Only the sibling's session already in flight may start after the failure;
    # it finishes and is saved, and nothing else starts.
    assert len(started_after_failure) <= 1
    assert set(started_after_failure) <= recorded
    assert "q0-fact-0-1" not in recorded
    assert "q0-fact-0-2" not in calls
    assert ingest_phase["status"] == "failed"
    assert set(ingest_phase["pending_sessions"]) == {"q0"}

    await _runner(tmp_path, dataset_path, concurrency=2).ingest()
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    assert checkpoint["phases"]["ingest"]["completed_count"] == 8
    manifest = json.loads((runner.output_path.parent / "run_manifest.json").read_text())
    assert manifest["baseline_qualified"] is True


# --------------------------------------------------------------- evaluate-only rerun
def _source_run(tmp_path: Path, dataset_path: Path, *, manifest: bool = True) -> Path:
    source_dir = tmp_path / "ingest_run"
    source_dir.mkdir()
    checkpoint_path = source_dir / "longmemeval_checkpoint.json"
    checkpoint_path.write_text(json.dumps(_ingested_checkpoint(dataset_path)))
    if manifest:
        (source_dir / "run_manifest.json").write_text(
            json.dumps({"run_id": RUN_ID, "baseline_qualified": True, "disqualifications": []})
        )
    return checkpoint_path


@pytest.mark.asyncio
async def test_from_ingest_evaluates_without_reingesting_and_records_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    source = _source_run(tmp_path, dataset_path)
    snapshot = tmp_path / "ingest.dump"
    snapshot.write_bytes(b"PGDMP fake dump")
    runner = _runner(
        tmp_path,
        dataset_path,
        output_dir=tmp_path / "eval_a",
        from_ingest=source,
        ingest_snapshot=snapshot,
    )
    BenchRuntime(monkeypatch)
    ingest_mock = AsyncMock()
    monkeypatch.setattr(fact_harness, "ingest_session_parity", ingest_mock)

    asked: dict[str, uuid.UUID] = {}

    async def fake_eval(**kwargs: Any) -> dict[str, Any]:
        # Each question's user holds only its own haystack, so retrieval is
        # never narrowed to a conversation allow-list.
        assert kwargs["allowed_source_conversation_ids"] is None
        asked[kwargs["question_id"]] = kwargs["user_id"]
        return _result(kwargs["question_id"])

    monkeypatch.setattr(fact_harness, "evaluate_single_parity", fake_eval)
    await runner.evaluate()
    runner.score()

    ingest_mock.assert_not_awaited()
    assert asked == {"q1": USER_Q1, "q2": USER_Q2}
    manifest = json.loads((tmp_path / "eval_a/run_manifest.json").read_text())
    assert manifest["ingest_source"]["checkpoint_sha256"] == durable_io.file_sha256(source)
    assert manifest["ingest_snapshot"]["sha256"] == durable_io.file_sha256(snapshot)
    assert manifest["phases"]["evaluate"]["state_matches_ingest_at_start"] is True
    assert manifest["phases"]["evaluate"]["state_unchanged_by_evaluate"] is True
    assert manifest["phases"]["score"]["strict_accuracy"] == 1.0
    assert manifest["ingest_source"]["baseline_qualified"] is True
    assert manifest["baseline_qualified"] is True
    with pytest.raises(ValueError, match="evaluate-only"):
        await runner.ingest()


async def _evaluate_from(
    tmp_path: Path, dataset_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    runner = _runner(tmp_path, dataset_path, output_dir=tmp_path / "eval", from_ingest=source)

    async def fake_eval(**kwargs: Any) -> dict[str, Any]:
        return _result(kwargs["question_id"])

    monkeypatch.setattr(fact_harness, "evaluate_single_parity", fake_eval)
    await runner.evaluate()
    runner.score()
    return json.loads((tmp_path / "eval/run_manifest.json").read_text())


@pytest.mark.asyncio
async def test_evaluate_from_a_retried_ingest_stays_disqualified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    ingest = _runner(tmp_path, dataset_path, retry_failed_sessions=True)
    ingest.checkpoint_path.parent.mkdir(parents=True)
    ingest.checkpoint_path.write_text(
        json.dumps(
            _ingest_checkpoint(
                dataset_path,
                {
                    **_full_ingest(),
                    _key("q2", KEY_B): _ingest_result(
                        "q2", KEY_B, CONV_Q2_B, status="extraction_failed"
                    ),
                },
            )
        )
    )
    BenchRuntime(monkeypatch, conversations={USER_Q1: {CONV_Q1_A}, USER_Q2: {CONV_Q2_A, CONV_Q2_B}})

    async def fake_ingest(**kwargs: Any) -> dict[str, Any]:
        return {
            "session_id": kwargs["session_id"],
            "conversation_id": str(uuid.uuid4()),
            "status": "complete",
        }

    monkeypatch.setattr(fact_harness, "ingest_session_parity", fake_ingest)
    await ingest.ingest()

    manifest = await _evaluate_from(tmp_path, dataset_path, ingest.checkpoint_path, monkeypatch)

    assert manifest["ingest_source"]["baseline_qualified"] is False
    assert manifest["ingest_source"]["disqualifications"] == ["retried_failed_sessions"]
    assert manifest["baseline_qualified"] is False
    assert manifest["disqualifications"] == ["source_ingest_not_qualified"]
    assert manifest["phases"]["score"]["baseline_qualified"] is False


@pytest.mark.asyncio
async def test_evaluate_from_an_unverifiable_ingest_is_not_qualified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    source = _source_run(tmp_path, dataset_path, manifest=False)
    BenchRuntime(monkeypatch)

    manifest = await _evaluate_from(tmp_path, dataset_path, source, monkeypatch)

    assert manifest["ingest_source"]["baseline_qualified"] is None
    assert manifest["ingest_source"]["disqualifications"] == ["source_manifest_missing"]
    assert manifest["baseline_qualified"] is False
    assert manifest["disqualifications"] == ["source_ingest_not_qualified"]


@pytest.mark.asyncio
async def test_from_ingest_refuses_database_that_differs_from_the_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    source = _source_run(tmp_path, dataset_path)
    other = {"sha256": "b" * 64, "tables": {}}
    runner = _runner(tmp_path, dataset_path, output_dir=tmp_path / "eval", from_ingest=source)
    BenchRuntime(monkeypatch, fingerprints=[other])
    evaluate_mock = AsyncMock()
    monkeypatch.setattr(fact_harness, "evaluate_single_parity", evaluate_mock)
    with pytest.raises(RuntimeError, match="Restore the ingest snapshot"):
        await runner.evaluate()
    evaluate_mock.assert_not_awaited()

    permissive = _runner(
        tmp_path,
        dataset_path,
        output_dir=tmp_path / "eval",
        from_ingest=source,
        allow_state_mismatch=True,
    )
    monkeypatch.setattr(
        fact_harness,
        "evaluate_single_parity",
        AsyncMock(side_effect=lambda **k: _result(k["question_id"])),
    )
    await permissive.evaluate()
    manifest = json.loads((tmp_path / "eval/run_manifest.json").read_text())
    assert manifest["phases"]["evaluate"]["state_matches_ingest_at_start"] is False


@pytest.mark.asyncio
async def test_from_ingest_rejects_a_different_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    source = _source_run(tmp_path, dataset_path)
    other_dataset = tmp_path / "other.json"
    other_dataset.write_text(json.dumps(DATASET[:1]))
    runner = _runner(tmp_path, other_dataset, output_dir=tmp_path / "eval", from_ingest=source)
    BenchRuntime(monkeypatch)
    with pytest.raises(ValueError, match="different dataset"):
        await runner.evaluate()


@pytest.mark.asyncio
async def test_evaluate_flags_state_changed_during_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path)
    runner.checkpoint_path.parent.mkdir(parents=True)
    runner.checkpoint_path.write_text(json.dumps(_ingested_checkpoint(dataset_path)))
    touched = {"sha256": "c" * 64, "tables": {}}
    BenchRuntime(monkeypatch, fingerprints=[FP_INGEST, touched])
    monkeypatch.setattr(
        fact_harness,
        "evaluate_single_parity",
        AsyncMock(side_effect=lambda **k: _result(k["question_id"])),
    )
    await runner.evaluate()
    manifest = json.loads((runner.output_path.parent / "run_manifest.json").read_text())
    assert manifest["phases"]["evaluate"]["state_unchanged_by_evaluate"] is False


# ------------------------------------------------------------------------ manifest
@pytest.mark.asyncio
async def test_manifest_pins_run_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path)
    runner.checkpoint_path.parent.mkdir(parents=True)
    runner.checkpoint_path.write_text(json.dumps(_ingested_checkpoint(dataset_path)))
    BenchRuntime(monkeypatch)
    monkeypatch.setattr(
        fact_harness,
        "evaluate_single_parity",
        AsyncMock(side_effect=lambda **k: _result(k["question_id"])),
    )
    await runner.evaluate()
    manifest = json.loads((runner.output_path.parent / "run_manifest.json").read_text())

    assert manifest["git"] == {"sha": "abc", "dirty": False, "code_sha256": "c" * 64}
    assert manifest["run_id"] == RUN_ID
    assert manifest["baseline_qualified"] is True
    assert manifest["effective_config"]["harness"]["benchmark_mode"] is True
    assert "embedding_route_id" in manifest["effective_config"]["settings"]
    assert "daemon_encryption_key" not in manifest["effective_config"]["settings"]
    assert manifest["phases"]["evaluate"]["active_seconds"] >= 0
    assert "last_progress_at" in manifest["invocations"][0]
    assert manifest["substrate"] == "fact"
    assert manifest["config_pin"]["sha256"] == durable_io.file_sha256(
        fact_harness.BENCHMARK_CONFIG_PIN_PATH
    )
    assert manifest["memory_formatter"] == fact_harness.memory_formatter_identity()
    assert manifest["dataset"]["sha256"] == durable_io.file_sha256(dataset_path)
    assert manifest["routes"]["answer"]["model"] == "openrouter/openai/gpt-4o-2024-08-06"
    assert manifest["routes"]["answer"]["seed"] == 42
    assert manifest["routes"]["extraction"]["profile"] == "background"
    assert manifest["embedding"]["mode"] in {"qualified_route", "lexical_fallback"}
    assert {item["id"] for item in manifest["parity_exceptions"]} == {
        item["id"] for item in fact_harness.PARITY_EXCEPTIONS
    }
    assert manifest["phases"]["evaluate"]["retrieval_modes"] == {"lexical": 2}
    assert manifest["phases"]["evaluate"]["answer_usage"] == {
        "prompt_tokens": 20,
        "completion_tokens": 4,
    }
    assert manifest["phases"]["evaluate"]["provider_fingerprints"] == {
        "answer": {"fp_a": 2},
        "judge": {"fp_j": 2},
    }
    assert manifest["invocations"][0]["phase"] == "evaluate"
    assert manifest["invocations"][0]["outcome"] == "completed"


@pytest.mark.asyncio
async def test_manifest_describes_the_embedding_route_after_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path)
    runner.checkpoint_path.parent.mkdir(parents=True)
    runner.checkpoint_path.write_text(json.dumps(_ingested_checkpoint(dataset_path)))
    BenchRuntime(monkeypatch)
    attested = False

    async def attest(settings: Any, pool: Any) -> dict[str, int]:
        nonlocal attested
        attested = True
        return {"routes": 1, "attested": 1, "revoked": 0, "check_failed": 0}

    def describe() -> dict[str, Any]:
        # Approval depends on attestation, as describe_embedding_configuration's does.
        return {
            "route_id": "r",
            "mode": "qualified_route",
            "configuration": "eligible" if attested else "unavailable",
            "reason_codes": [] if attested else ["route_unapproved"],
        }

    monkeypatch.setattr(fact_harness, "prepare_service_runtime", attest)
    monkeypatch.setattr(fact_harness, "_embedding_route_description", describe)
    monkeypatch.setattr(
        fact_harness,
        "evaluate_single_parity",
        AsyncMock(side_effect=lambda **k: _result(k["question_id"])),
    )
    await runner.evaluate()
    manifest = json.loads((runner.output_path.parent / "run_manifest.json").read_text())

    assert manifest["embedding"]["configuration"] == "eligible"
    assert manifest["embedding"]["reason_codes"] == []


@pytest.mark.asyncio
async def test_resume_on_different_code_is_refused_unless_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path)
    runner.checkpoint_path.parent.mkdir(parents=True)
    runner.checkpoint_path.write_text(json.dumps(_ingested_checkpoint(dataset_path)))
    runtime = BenchRuntime(monkeypatch)
    monkeypatch.setattr(
        fact_harness,
        "evaluate_single_parity",
        AsyncMock(side_effect=lambda **k: _result(k["question_id"])),
    )
    await runner.evaluate()

    runtime.git = {"sha": "def", "dirty": False, "code_sha256": "c" * 64}
    with pytest.raises(ManifestPinError, match="pinned to abc"):
        await _runner(tmp_path, dataset_path).evaluate()
    await _runner(tmp_path, dataset_path, allow_code_change=True).evaluate()
    manifest = json.loads((runner.output_path.parent / "run_manifest.json").read_text())
    assert manifest["code_pin_changes"][0]["changes"] == ["git sha abc -> def"]
    assert manifest["baseline_qualified"] is False
    assert manifest["disqualifications"] == ["code_changed_mid_run"]


def _git(repo: Path, *args: str) -> None:
    import subprocess

    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)  # nosec B603 B607


def test_code_pin_detects_a_second_uncommitted_edit_at_the_same_sha(tmp_path: Path) -> None:
    from orchestrator.eval.run_manifest import check_code_pin, git_state

    repo = tmp_path / "repo"
    (repo / "orchestrator").mkdir(parents=True)
    module = repo / "orchestrator" / "retrieval.py"
    module.write_text("LIMIT = 5\n")
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")

    clean = git_state(repo)
    module.write_text("LIMIT = 6\n")
    dirty_a = git_state(repo)
    module.write_text("LIMIT = 7\n")
    dirty_b = git_state(repo)
    assert dirty_a["sha"] == dirty_b["sha"] == clean["sha"]
    assert dirty_a["dirty"] is dirty_b["dirty"] is True
    assert len({clean["code_sha256"], dirty_a["code_sha256"], dirty_b["code_sha256"]}) == 3
    with pytest.raises(ManifestPinError, match="code content"):
        check_code_pin({"git": dirty_a}, dirty_b, allow_change=False)

    # A new untracked module counts; run outputs outside the code paths do not.
    module.write_text("LIMIT = 7\n")
    (repo / "orchestrator" / "new_helper.py").write_text("X = 1\n")
    assert git_state(repo)["code_sha256"] != dirty_b["code_sha256"]
    (repo / "orchestrator" / "new_helper.py").unlink()
    (repo / "results.jsonl").write_text("{}\n")
    assert git_state(repo)["code_sha256"] == dirty_b["code_sha256"]


@pytest.mark.asyncio
async def test_resume_with_only_an_environment_config_change_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    stop = StopController()
    runner = _runner(tmp_path, dataset_path, stop=stop)
    runner.checkpoint_path.parent.mkdir(parents=True)
    runner.checkpoint_path.write_text(json.dumps(_ingested_checkpoint(dataset_path)))
    runtime = BenchRuntime(monkeypatch)
    runtime.settings.embedding_route_id = "azure-openrouter-small-1024"

    async def fake_eval(**kwargs: Any) -> dict[str, Any]:
        stop.request(signal.SIGTERM)
        return _result(kwargs["question_id"])

    monkeypatch.setattr(fact_harness, "evaluate_single_parity", fake_eval)
    with pytest.raises(HarnessInterrupted):
        await runner.evaluate()

    # Same code, same database; only EMBEDDING_ROUTE_ID is unset now.
    runtime.settings.embedding_route_id = ""
    evaluate_mock = AsyncMock()
    monkeypatch.setattr(fact_harness, "evaluate_single_parity", evaluate_mock)
    with pytest.raises(ManifestPinError, match="settings.embedding_route_id"):
        await _runner(tmp_path, dataset_path).evaluate()
    evaluate_mock.assert_not_awaited()
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    assert list(checkpoint["phases"]["evaluate"]["results"]) == ["q1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_run_id", [None, "f" * 32])
async def test_results_from_another_run_are_never_adopted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, foreign_run_id: str | None
) -> None:
    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path)
    runner.checkpoint_path.parent.mkdir(parents=True)
    runner.checkpoint_path.write_text(json.dumps(_ingested_checkpoint(dataset_path)))
    rows = [_result("q1", run_id=foreign_run_id), _result("q2", run_id=foreign_run_id)]
    if foreign_run_id is None:
        rows = [{k: v for k, v in row.items() if k != "run_id"} for row in rows]  # legacy file
    runner.output_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    BenchRuntime(monkeypatch)
    evaluate_mock = AsyncMock()
    monkeypatch.setattr(fact_harness, "evaluate_single_parity", evaluate_mock)
    with pytest.raises(RuntimeError, match="from another run"):
        await runner.evaluate()
    evaluate_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_then_reingest_in_the_same_directory_reuses_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path)
    runner.checkpoint_path.parent.mkdir(parents=True)
    runner.checkpoint_path.write_text(json.dumps(_ingested_checkpoint(dataset_path)))
    BenchRuntime(monkeypatch)
    monkeypatch.setattr(
        fact_harness,
        "evaluate_single_parity",
        AsyncMock(side_effect=lambda **k: _result(k["question_id"])),
    )
    await runner.evaluate()
    assert runner.output_path.read_text().count("question_id") == 2

    # reset_canonical_benchmark removes only the checkpoint; results and manifest stay.
    runner.checkpoint_path.unlink()
    BenchRuntime(monkeypatch)
    fake_ingest = AsyncMock(
        side_effect=lambda **k: {
            "session_id": k["session_id"],
            "conversation_id": str(uuid.uuid4()),
            "status": "complete",
        }
    )
    monkeypatch.setattr(fact_harness, "ingest_session_parity", fake_ingest)
    with pytest.raises(ManifestPinError, match="belongs to a different run"):
        await _runner(tmp_path, dataset_path).ingest()
    fake_ingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_killed_invocation_is_closed_from_its_last_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path)
    runner.checkpoint_path.parent.mkdir(parents=True)
    runner.checkpoint_path.write_text(json.dumps(_ingested_checkpoint(dataset_path)))
    BenchRuntime(monkeypatch)
    monkeypatch.setattr(
        fact_harness,
        "evaluate_single_parity",
        AsyncMock(side_effect=lambda **k: _result(k["question_id"])),
    )
    await runner.evaluate()
    manifest_path = runner.output_path.parent / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["invocations"].append(
        {
            "phase": "evaluate",
            "started_at": "2026-10-26T01:00:00+00:00",
            "last_progress_at": "2026-10-26T01:02:30+00:00",
            "outcome": "running",
        }
    )
    manifest_path.write_text(json.dumps(manifest))
    await _runner(tmp_path, dataset_path).evaluate()
    killed = json.loads(manifest_path.read_text())["invocations"][1]
    assert killed["outcome"] == "killed"
    assert killed["ended_at"] == "2026-10-26T01:02:30+00:00"
    assert killed["active_seconds"] == 150.0


@pytest.mark.asyncio
async def test_reassert_handlers_takes_signals_back_from_a_library() -> None:
    async def body(controller: StopController) -> None:
        loop = asyncio.get_running_loop()
        swallowed: list[int] = []
        # What arq's Worker() does on import inside a running loop.
        loop.add_signal_handler(signal.SIGTERM, lambda: swallowed.append(1))
        controller.reassert_handlers()
        os.kill(os.getpid(), signal.SIGTERM)
        for _ in range(50):
            if controller.requested:
                break
            await asyncio.sleep(0.01)
        assert swallowed == []
        controller.raise_if_requested()

    result, controller = await run_interruptible(body)
    assert result is None
    assert controller.signal_name == "SIGTERM"


@pytest.mark.asyncio
async def test_ingest_reasserts_signal_handlers_after_importing_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    stop = StopController()
    reasserted: list[bool] = []
    monkeypatch.setattr(stop, "reassert_handlers", lambda: reasserted.append(True))
    runner = _runner(tmp_path, dataset_path, stop=stop)
    BenchRuntime(monkeypatch)
    imported: list[str] = []
    real_import = fact_harness.importlib.import_module

    def tracking_import(name: str) -> Any:
        imported.append(name)
        return real_import(name)

    monkeypatch.setattr(fact_harness.importlib, "import_module", tracking_import)
    monkeypatch.setattr(
        fact_harness,
        "ingest_session_parity",
        AsyncMock(
            side_effect=lambda **k: {
                "session_id": k["session_id"],
                "conversation_id": str(uuid.uuid4()),
                "status": "complete",
            }
        ),
    )
    await runner.ingest()
    assert imported == ["orchestrator.worker.jobs"]
    assert reasserted == [True]


def test_score_reports_strict_accuracy_and_category_counts(tmp_path: Path) -> None:
    payload = fact_harness.build_score_payload(
        [
            _result("q1"),
            _result("q2", "partially_correct", category="MR"),
            _result("q3", "incorrect", category="MR", error="BenchmarkProviderError"),
        ]
    )
    assert payload["strict_accuracy"] == pytest.approx(1 / 3)
    assert payload["category_counts"]["MR"] == {
        "total": 2,
        "errors": 1,
        "correct": 0,
        "partially_correct": 1,
        "incorrect": 1,
    }
    assert payload["error_count"] == 1
    assert payload["errored_question_ids"] == ["q3"]


# ----------------------------------------------------------------- session parity
@pytest.mark.asyncio
async def test_ingest_session_parity_chunks_like_the_worker_inside_account_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.memory.extraction import MAX_EXTRACTION_INPUT_CHARS
    from tests.longmemeval import ingest

    events: list[str] = []
    texts: list[str] = []
    conversation_id = uuid.uuid4()
    store = MagicMock()

    async def create_conversation(**kwargs: Any) -> dict[str, Any]:
        events.append("create_conversation")
        return {"id": conversation_id}

    message_ids = iter(range(100))

    async def insert_message(**kwargs: Any) -> dict[str, Any]:
        events.append("insert_message")
        return {"id": uuid.UUID(int=next(message_ids)), "created_at": None}

    store.create_conversation = create_conversation
    store.insert_message = insert_message

    async def fake_process_extraction(**kwargs: Any) -> tuple[bool, list[Any], bool]:
        events.append("extract")
        texts.append(kwargs["text"])
        return (len(texts) != 2, [{"id": 1}] if len(texts) != 2 else [], False)

    monkeypatch.setattr(ingest, "process_extraction", fake_process_extraction)

    @asynccontextmanager
    async def scope() -> AsyncIterator[None]:
        events.append("scope_enter")
        yield
        events.append("scope_exit")

    async def on_created(cid: uuid.UUID) -> None:
        events.append("checkpoint_conversation")

    messages = [
        {"role": "user", "content": "short opener"},
        {"role": "assistant", "content": "x" * (MAX_EXTRACTION_INPUT_CHARS * 2)},
        {"role": "user", "content": "closing detail about Miso"},
    ]
    result = await ingest.ingest_session_parity(
        store,
        USER_ID,
        "session-1",
        messages,
        0,
        scope_factory=scope,
        on_conversation_created=on_created,
    )

    assert events[:2] == ["create_conversation", "checkpoint_conversation"]
    assert len(texts) >= 3
    assert all(len(text) <= MAX_EXTRACTION_INPUT_CHARS for text in texts)
    assert "short opener" in texts[0]
    assert "closing detail about Miso" in texts[-1]
    assert events.count("scope_enter") == len(texts)
    assert result["chunk_count"] == len(texts)
    assert result["chunks_failed"] == 1
    assert result["status"] == "extraction_failed"
    assert result["conversation_id"] == str(conversation_id)


@pytest.mark.asyncio
async def test_evaluate_single_parity_answers_with_production_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.memory import injection
    from tests.longmemeval import evaluate

    calls: list[dict[str, Any]] = []
    scope_events: list[str] = []
    gather_kwargs: dict[str, Any] = {}

    async def fake_gather(store: Any, **kwargs: Any) -> injection.MemoryContextInputs:
        scope_events.append("gather")
        gather_kwargs.update(kwargs)
        return injection.MemoryContextInputs(
            retrieved=[{"content": "User adopted a cat named Miso", "category": "fact"}],
            retrieval_mode="lexical",
        )

    async def fake_llm(**kwargs: Any) -> Any:
        calls.append(kwargs)
        content = {
            "answer": "Miso",
            "judge": "CORRECT\nMatches.",
            "official_judge": "yes",
        }[kwargs["bm_call_key"]]
        response = {
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 3, "total_tokens": 103},
            "system_fingerprint": f"fp_{kwargs['bm_call_key']}",
        }
        evaluate._capture_benchmark_metadata(response, key=kwargs["bm_call_key"])
        return response

    monkeypatch.setattr(evaluate, "gather_memory_context_inputs", fake_gather)
    monkeypatch.setattr(evaluate, "_call_llm_with_provider_config", fake_llm)

    @asynccontextmanager
    async def scope() -> AsyncIterator[None]:
        scope_events.append("enter")
        yield
        scope_events.append("exit")

    result = await evaluate.evaluate_single_parity(
        MagicMock(),
        "q1",
        "What is my cat called?",
        "Miso",
        "IE-user",
        allowed_source_conversation_ids=[uuid.uuid4()],
        scope_factory=scope,
    )

    assert scope_events == ["enter", "gather", "exit"]
    assert gather_kwargs["record_side_effects"] is False
    assert gather_kwargs["pipeline"] == "cloud"
    answer_messages = calls[0]["messages"]
    assert [m["role"] for m in answer_messages] == ["system", "user"]
    assert "- Fact: User adopted a cat named Miso" in answer_messages[0]["content"]
    assert '<memory_records trust="user_data">' in answer_messages[0]["content"]
    assert [c["bm_call_key"] for c in calls] == ["answer", "judge", "official_judge"]
    assert result["judgment"] == "correct"
    assert result["hypothesis"] == "Miso"
    assert result["retrieval_mode"] == "lexical"
    assert result["answer_usage"]["prompt_tokens"] == 100
    assert result["judge_usage"]["completion_tokens"] == 3
    assert (result["answer_fingerprint"], result["judge_fingerprint"]) == ("fp_answer", "fp_judge")
    assert result["answer_finish_reason"] == result["judge_finish_reason"] == "stop"
    assert result["judge_raw_output"] == "CORRECT\nMatches."
    assert result["official_judgment"] == "correct"
    assert result["official_judge_fingerprint"] == "fp_official_judge"
    assert result["official_judge_raw_output"] == "yes"
    assert result["official_judge_finish_reason"] == "stop"
    assert result["official_judge_status"] == "completed"
    assert calls[2]["max_tokens"] == 10


# ------------------------------------------------------- question as-of time (#601)
def test_question_as_of_reads_question_date_as_utc() -> None:
    as_of = fact_harness.question_as_of({"question_date": "2023/03/01 (Wed) 19:28"})
    assert as_of == datetime(2023, 3, 1, 19, 28, tzinfo=UTC)


@pytest.mark.parametrize("entry", [{}, {"question_date": None}, {"question_date": "March 1st"}])
def test_question_as_of_refuses_missing_or_unparsable_dates(entry: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="question_date"):
        fact_harness.question_as_of(entry)


def test_parity_answer_prompt_adds_production_date_block_only_with_as_of() -> None:
    from orchestrator.daemon import with_runtime_datetime_context
    from orchestrator.memory.injection import compose_system_prompt
    from tests.longmemeval import evaluate

    block = '<memory_records trust="user_data">\n- Fact: x\n</memory_records>'
    plain = evaluate.build_parity_answer_messages("When?", block)
    assert plain[0]["content"] == compose_system_prompt(block)
    assert "Current date" not in plain[0]["content"]

    as_of = datetime(2023, 3, 1, 19, 28, tzinfo=UTC)
    dated = evaluate.build_parity_answer_messages("When?", block, as_of=as_of)
    assert dated[0]["content"] == with_runtime_datetime_context(
        compose_system_prompt(block), now_utc=as_of, default_timezone="UTC"
    )
    assert "- Current date: 2023-03-01" in dated[0]["content"]
    assert dated[1] == {"role": "user", "content": "When?"}


def test_recency_clock_defaults_to_wall_clock_and_honours_pin() -> None:
    from orchestrator.memory import retrieval

    accessed = datetime.now(UTC) - timedelta(days=40)
    memory: dict[str, object] = {"last_accessed_at": accessed}
    assert retrieval.RECENCY_REFERENCE_TIME.get() is None
    assert 39.9 < retrieval._days_since_accessed(memory) < 40.1

    token = retrieval.RECENCY_REFERENCE_TIME.set(accessed + timedelta(days=3))
    try:
        assert retrieval._days_since_accessed(memory) == pytest.approx(3.0)
        # A pin earlier than the access time clamps to the freshest bucket.
        retrieval.RECENCY_REFERENCE_TIME.set(accessed - timedelta(days=900))
        assert retrieval._days_since_accessed(memory) == 1.0
    finally:
        retrieval.RECENCY_REFERENCE_TIME.reset(token)
    assert retrieval.RECENCY_REFERENCE_TIME.get() is None


@pytest.mark.asyncio
async def test_evaluate_single_parity_pins_recency_clock_during_gather_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.memory import injection, retrieval
    from tests.longmemeval import evaluate

    seen: list[datetime | None] = []
    calls: list[dict[str, Any]] = []

    async def fake_gather(store: Any, **kwargs: Any) -> injection.MemoryContextInputs:
        seen.append(retrieval.RECENCY_REFERENCE_TIME.get())
        return injection.MemoryContextInputs(
            retrieved=[{"content": "User watched the game", "category": "fact"}],
            retrieval_mode="lexical",
        )

    async def fake_llm(**kwargs: Any) -> Any:
        calls.append(kwargs)
        content = {"answer": "17 days", "judge": "CORRECT\nok", "official_judge": "yes"}
        response = {
            "choices": [
                {"message": {"content": content[kwargs["bm_call_key"]]}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        evaluate._capture_benchmark_metadata(response, key=kwargs["bm_call_key"])
        return response

    monkeypatch.setattr(evaluate, "gather_memory_context_inputs", fake_gather)
    monkeypatch.setattr(evaluate, "_call_llm_with_provider_config", fake_llm)
    as_of = datetime(2023, 3, 1, 19, 28, tzinfo=UTC)
    result = await evaluate.evaluate_single_parity(
        MagicMock(),
        "q1",
        "How many days ago?",
        "17",
        "TR",
        allowed_source_conversation_ids=None,
        as_of=as_of,
    )
    assert seen == [as_of]
    assert retrieval.RECENCY_REFERENCE_TIME.get() is None
    assert result["as_of"] == "2023-03-01T19:28:00+00:00"
    assert "- Current date: 2023-03-01" in calls[0]["messages"][0]["content"]
    assert result["system_prompt_sha256"] == evaluate._sha256_text(
        calls[0]["messages"][0]["content"]
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        await evaluate.evaluate_single_parity(
            MagicMock(),
            "q1",
            "?",
            "a",
            "TR",
            allowed_source_conversation_ids=None,
            as_of=datetime(2023, 3, 1),
        )
    assert retrieval.RECENCY_REFERENCE_TIME.get() is None
