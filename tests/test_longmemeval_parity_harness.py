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
    monkeypatch.setattr(fact_harness, "create_test_user", create_user)
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
CONV_A = str(uuid.uuid4())
CONV_B = str(uuid.uuid4())

DATASET = [
    {
        "question_id": "q1",
        "question": "What is my cat called?",
        "answer": "Miso",
        "question_type": "single-session-user",
        "haystack_session_ids": ["s-a"],
        "haystack_sessions": [SESSION_A],
    },
    {
        "question_id": "q2",
        "question": "Where do I fly from?",
        "answer": "Adelaide",
        "question_type": "multi-session",
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
                "results": {
                    KEY_A: {"corpus_key": KEY_A, "conversation_id": CONV_A, "status": "complete"},
                    KEY_B: {"corpus_key": KEY_B, "conversation_id": CONV_B, "status": "complete"},
                },
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
        conversations: set[str] | None = None,
        fingerprints: list[dict[str, Any]] | None = None,
        git_sha: str = "abc",
    ) -> None:
        self.rollbacks: list[set[str]] = []
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
        monkeypatch.setattr(target, "create_test_user", AsyncMock(return_value=USER_ID))
        monkeypatch.setattr(target, "ensure_benchmark_account", AsyncMock())
        monkeypatch.setattr(target, "git_state", lambda root=None: dict(self.git))
        monkeypatch.setattr(
            target,
            "list_benchmark_conversation_ids",
            AsyncMock(return_value=set(conversations or set())),
        )
        monkeypatch.setattr(target, "rollback_orphan_conversations", self._rollback)
        monkeypatch.setattr(target, "fingerprint_benchmark_state", self._fingerprint)
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

    async def _rollback(self, pool: Any, user_id: Any, ids: set[str]) -> dict[str, int]:
        self.rollbacks.append(set(ids))
        return {"conversations": len(ids), "memories": 3, "memory_extraction_log": 1}

    async def _fingerprint(self, pool: Any, user_id: Any, *, tables: Any = None) -> dict[str, Any]:
        if tables is not None:
            return self.cross_session_fingerprint
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
    dataset_path: Path, results: dict[str, Any], *, pending: str | None = None
) -> dict[str, Any]:
    checkpoint = _ingested_checkpoint(dataset_path)
    checkpoint["phases"]["ingest"] = {
        "status": "interrupted",
        "started_at": "2026-10-26T00:00:00+00:00",
        "results": results,
    }
    if pending is not None:
        checkpoint["phases"]["ingest"]["pending_session"] = {
            "corpus_key": pending,
            "started_at": "2026-10-26T00:05:00+00:00",
            "state_fingerprint": FP_PRE_SESSION,
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
                {KEY_A: {"corpus_key": KEY_A, "conversation_id": CONV_A, "status": "complete"}},
                pending=KEY_B,
            )
        )
    )
    orphan = str(uuid.uuid4())
    runtime = BenchRuntime(monkeypatch, conversations={CONV_A, orphan})
    ingested: list[str] = []

    async def fake_ingest(**kwargs: Any) -> dict[str, Any]:
        ingested.append(kwargs["session_id"])
        await kwargs["on_conversation_created"](uuid.UUID(CONV_B))
        async with kwargs["scope_factory"]():
            pass
        return {"session_id": kwargs["session_id"], "conversation_id": CONV_B, "status": "complete"}

    monkeypatch.setattr(fact_harness, "ingest_session_parity", fake_ingest)
    await runner.ingest()

    assert runtime.rollbacks == [{orphan}]
    assert ingested == ["s-b"]
    assert runtime.scopes == [
        {"operation": "agent", "auto_route": True, "background": True, "profile": "background"}
    ]
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    ingest_phase = checkpoint["phases"]["ingest"]
    assert ingest_phase["status"] == "completed"
    assert ingest_phase["state_fingerprint"] == FP_INGEST
    assert len(ingest_phase["results"]) == 2
    manifest = json.loads((runner.output_path.parent / "run_manifest.json").read_text())
    assert manifest["ingest_rollbacks"][0]["orphan_conversations"] == 1
    assert manifest["ingest_rollbacks"][0]["restored_pre_session_state"] is True
    assert "pending_session" not in ingest_phase
    assert manifest["baseline_qualified"] is True
    assert manifest["routes"]["extraction"]["observed_models"] == {"luna": 2}


@pytest.mark.asyncio
async def test_ingest_refuses_when_rollback_cannot_restore_the_pre_session_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path)
    runner.checkpoint_path.parent.mkdir(parents=True)
    runner.checkpoint_path.write_text(
        json.dumps(
            _ingest_checkpoint(
                dataset_path,
                {KEY_A: {"corpus_key": KEY_A, "conversation_id": CONV_A, "status": "complete"}},
                pending=KEY_B,
            )
        )
    )
    runtime = BenchRuntime(monkeypatch, conversations={CONV_A, str(uuid.uuid4())})
    # The interrupted session superseded session A's memory: deleting its own
    # rows leaves that change behind.
    runtime.cross_session_fingerprint = {"sha256": "d" * 64, "tables": {}}
    fake_ingest = AsyncMock()
    monkeypatch.setattr(fact_harness, "ingest_session_parity", fake_ingest)
    with pytest.raises(RuntimeError, match="cannot restore the pre-session state"):
        await runner.ingest()
    fake_ingest.assert_not_awaited()
    manifest = json.loads((runner.output_path.parent / "run_manifest.json").read_text())
    assert manifest["ingest_rollbacks"][0]["restored_pre_session_state"] is False
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    assert checkpoint["phases"]["ingest"]["pending_session"]["corpus_key"] == KEY_B

    # The refusal persists: a plain resume never "repairs" the altered state.
    runtime_again = BenchRuntime(monkeypatch, conversations={CONV_A})
    runtime_again.cross_session_fingerprint = {"sha256": "d" * 64, "tables": {}}
    with pytest.raises(RuntimeError, match="cannot restore the pre-session state"):
        await _runner(tmp_path, dataset_path).ingest()
    fake_ingest.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_refuses_database_not_built_by_this_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset_path)
    runtime = BenchRuntime(monkeypatch, conversations={str(uuid.uuid4()), str(uuid.uuid4())})
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
                    KEY_A: {"corpus_key": KEY_A, "conversation_id": CONV_A, "status": "complete"},
                    KEY_B: {
                        "corpus_key": KEY_B,
                        "conversation_id": CONV_B,
                        "status": "extraction_failed",
                    },
                },
            )
        )
    )
    runtime = BenchRuntime(monkeypatch, conversations={CONV_A, CONV_B})
    new_conv = str(uuid.uuid4())

    async def fake_ingest(**kwargs: Any) -> dict[str, Any]:
        return {
            "session_id": kwargs["session_id"],
            "conversation_id": new_conv,
            "status": "complete",
        }

    monkeypatch.setattr(fact_harness, "ingest_session_parity", fake_ingest)
    await runner.ingest()
    assert runtime.rollbacks == [{CONV_B}]
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    assert checkpoint["phases"]["ingest"]["results"][KEY_B]["conversation_id"] == new_conv
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


@pytest.mark.asyncio
async def test_ingest_stops_after_consecutive_extraction_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = [[{"role": "user", "content": f"fact number {i}"}] for i in range(8)]
    dataset = [
        {
            "question_id": "q1",
            "question": "?",
            "answer": "a",
            "question_type": "multi-session",
            "haystack_session_ids": [f"s-{i}" for i in range(8)],
            "haystack_sessions": sessions,
        }
    ]
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset))
    runner = _runner(tmp_path, dataset_path)
    BenchRuntime(monkeypatch)
    calls: list[str] = []

    async def failing_ingest(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["session_id"])
        return {"session_id": kwargs["session_id"], "status": "extraction_failed"}

    monkeypatch.setattr(fact_harness, "ingest_session_parity", failing_ingest)
    with pytest.raises(RuntimeError, match="consecutive sessions failed"):
        await runner.ingest()
    assert len(calls) == fact_harness.INGEST_FAILURE_BREAKER
    checkpoint = json.loads(runner.checkpoint_path.read_text())
    assert checkpoint["phases"]["ingest"]["status"] == "failed"
    assert len(checkpoint["phases"]["ingest"]["results"]) == fact_harness.INGEST_FAILURE_BREAKER


# --------------------------------------------------------------- evaluate-only rerun
def _source_run(tmp_path: Path, dataset_path: Path) -> Path:
    source_dir = tmp_path / "ingest_run"
    source_dir.mkdir()
    checkpoint_path = source_dir / "longmemeval_checkpoint.json"
    checkpoint_path.write_text(json.dumps(_ingested_checkpoint(dataset_path)))
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

    async def fake_eval(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["allowed_source_conversation_ids"]
        return _result(kwargs["question_id"])

    monkeypatch.setattr(fact_harness, "evaluate_single_parity", fake_eval)
    await runner.evaluate()
    runner.score()

    ingest_mock.assert_not_awaited()
    manifest = json.loads((tmp_path / "eval_a/run_manifest.json").read_text())
    assert manifest["ingest_source"]["checkpoint_sha256"] == durable_io.file_sha256(source)
    assert manifest["ingest_snapshot"]["sha256"] == durable_io.file_sha256(snapshot)
    assert manifest["phases"]["evaluate"]["state_matches_ingest_at_start"] is True
    assert manifest["phases"]["evaluate"]["state_unchanged_by_evaluate"] is True
    assert manifest["phases"]["score"]["strict_accuracy"] == 1.0
    with pytest.raises(ValueError, match="evaluate-only"):
        await runner.ingest()


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
    assert manifest["invocations"][0]["phase"] == "evaluate"
    assert manifest["invocations"][0]["outcome"] == "completed"


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
        content = "Miso" if kwargs["bm_call_key"] == "answer" else "CORRECT\nMatches."
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 3, "total_tokens": 103},
        }

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
    assert [c["bm_call_key"] for c in calls] == ["answer", "judge"]
    assert result["judgment"] == "correct"
    assert result["hypothesis"] == "Miso"
    assert result["retrieval_mode"] == "lexical"
    assert result["answer_usage"]["prompt_tokens"] == 100
    assert result["judge_usage"]["completion_tokens"] == 3
