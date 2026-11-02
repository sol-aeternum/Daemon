"""Synthetic, offline exposure metrics, preflight and production-path parity."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import UTC, datetime
import json
from typing import Any, cast
from unittest.mock import Mock
import uuid

import asyncpg
from cryptography.fernet import Fernet
import pytest

from orchestrator import compute_runtime
from orchestrator.eval import exposure
from orchestrator.eval.bench_db import fingerprint_benchmark_users, question_user_id
from orchestrator.eval.fact_harness import build_corpus_key, ingest_result_key, question_as_of
from orchestrator.memory import embedding_adapter, injection, retrieval
from orchestrator.memory.embedding import EmbeddingVectorResult
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from scripts import memory_exposure_sweep as sweep
from tests.longmemeval.evaluate import build_parity_memory_context


def memory(index: int, content: str = "Ari likes tea") -> dict[str, Any]:
    return {"id": uuid.UUID(int=index + 1), "content": content, "category": "fact"}


def measure(rows, *, targets=(), gold=(), budget=injection.DEFAULT_MAX_TOKENS):
    return exposure.measure(
        injection.MemoryContextInputs(retrieved=rows),
        target_ids=targets,
        gold_conversation_ids=gold,
        source_conversations={str(row["id"]): "session" for row in rows},
        ranking=rows,
        max_tokens=budget,
    )


def test_target_present_absent_strict_any_empty_gold_and_rank():
    rows = [memory(0), memory(1)]
    ids = [str(row["id"]) for row in rows]
    present = measure(rows, targets=ids, gold=["session", "other"])
    assert present["target_exposed"] and present["target_any_exposed"]
    assert present["gold_session_coverage_proxy"] == 0.5
    assert present["targets"][ids[1]]["rank"] == 2
    partial = measure(rows, targets=[ids[0], "missing"])
    assert not partial["target_exposed"] and partial["target_any_exposed"]
    assert partial["targets"]["missing"] == {
        "rendered": False,
        "rank": None,
        "budget_dropped": False,
    }
    absent = measure(rows, targets=["missing"])
    assert not absent["target_exposed"] and not absent["target_any_exposed"]
    assert absent["gold_session_coverage_proxy"] is None


def test_budget_drops_use_renderer_and_ignore_blank_lines_and_width_slice():
    rows = [memory(0), memory(1, ""), memory(2)]
    result = measure(rows, targets=[str(rows[2]["id"])], budget=1)
    assert result["target_pre_budget"] and not result["target_exposed"]
    assert result["target_budget_dropped"] == 1
    assert result["budget_dropped"] == 2  # blank row wasn't removed by budget
    assert result["effective_k"] == 0
    assert result["rendered_tokens"] == injection.estimate_tokens("")
    six = [memory(pos) for pos in range(6)]
    sliced = measure(six, targets=[str(six[5]["id"])])
    assert not sliced["target_pre_budget"] and sliced["budget_dropped"] == 0


def test_duplicate_rules_count_slots_not_pairs():
    assert exposure.duplicate_occupancy(
        [
            "Ari likes green tea",
            "  ARI likes green tea ",
            "Ari likes green tea!",
            "Ari likes green tea daily",
            "Jules dislikes coffee",
        ]
    ) == {"exact": 1, "similarity": 3}
    assert exposure.duplicate_occupancy(["A B C D", "A B C E"])["similarity"] == 0
    # Displayed truncation, not invisible source suffixes, defines occupancy.
    rows = [memory(0, "x" * 600 + "first"), memory(1, "x" * 600 + "second")]
    assert measure(rows)["duplicates"]["exact"] == 1


def test_rendered_ids_not_substring_matches_and_l0_supported():
    rows = [memory(0, "same"), memory(1, "same")]
    inputs = injection.MemoryContextInputs(l0_memories=[memory(2, "frozen")], retrieved=rows)
    block = injection.render_memory_context(inputs)
    assert [row["id"] for row in exposure.rendered_memory_rows(inputs, block)] == [
        memory(2)["id"],
        rows[0]["id"],
        rows[1]["id"],
    ]
    assert exposure.rendered_memory_texts(block) == ["frozen", "same", "same"]


def summaries():
    coverage = {f"q{index}": 1.0 for index in range(exposure.QUESTION_COUNT)}
    return {
        k: {
            "cases_strict": 3,
            "cases_pre_budget": 3,
            "target_budget_dropped": 0,
            "coverage": dict(coverage),
            "coverage_causes": {},
        }
        for k in exposure.SWEEP_KS
    }


def test_decide_smallest_qualifying_width_and_4_of_5_boundary():
    rows = summaries()
    assert exposure.decide(rows)["outcome"] == "INVESTIGATE_RETRIEVAL"
    rows[15].update(cases_strict=4, cases_pre_budget=4)
    rows[20].update(cases_strict=5, cases_pre_budget=5)
    assert exposure.decide(rows)["k"] == 15
    rows[10].update(cases_strict=4, cases_pre_budget=4)
    assert exposure.decide(rows)["k"] == 10
    assert exposure.decide(rows)["outcome"] == "PROPOSE_WIDTH_CAMPAIGN"


def test_decide_budget_boundary_and_fifth_case_drop_residual():
    rows = summaries()
    rows[15].update(cases_pre_budget=4, target_budget_dropped=1)
    assert exposure.decide(rows)["outcome"] == "BUDGET_LIMITED"
    assert exposure.decide(rows)["k"] == 15
    # Four cases rendered, but a retrieved target from case 5 is budget-dropped.
    rows[10].update(cases_strict=4, cases_pre_budget=5, target_budget_dropped=1)
    assert exposure.decide(rows)["outcome"] == "BUDGET_LIMITED"
    assert exposure.decide(rows)["k"] == 10
    # Ordered chain: a later qualifying width wins over an earlier budget issue.
    rows[20].update(cases_strict=4, cases_pre_budget=4)
    assert exposure.decide(rows)["outcome"] == "PROPOSE_WIDTH_CAMPAIGN"
    assert exposure.decide(rows)["k"] == 20


@pytest.mark.parametrize("cause", ["prefix_instability", "non_target_budget_drop"])
def test_decide_gap_example_is_coverage_regression(cause):
    rows = summaries()
    for k in exposure.CANDIDATE_KS:
        rows[k].update(cases_strict=4, cases_pre_budget=4)
        rows[k]["coverage"]["q0"] = 0.0
        rows[k]["coverage_causes"]["q0"] = cause
    assert exposure.decide(rows) == {
        "outcome": "COVERAGE_REGRESSION",
        "k": 10,
        "reasons": ["coverage_decreased", "no_campaign"],
        "questions": {"q0": cause},
    }


def test_decide_budget_precedes_coverage_and_none_comparison():
    rows = summaries()
    rows[10].update(cases_strict=4, cases_pre_budget=4)
    rows[10]["coverage"]["q0"] = None
    rows[10]["coverage_causes"]["q0"] = "prefix_instability"
    rows[15].update(cases_pre_budget=4, target_budget_dropped=1)
    assert exposure.decide(rows)["outcome"] == "BUDGET_LIMITED"
    assert exposure.coverage_losses({"q": None}, {"q": None}) == []
    assert exposure.coverage_losses({"q": None}, {"q": 0.0}) == ["q"]
    assert exposure.coverage_losses({"q": 0.0}, {"q": None}) == ["q"]


def test_investigate_implies_every_rendered_count_below_four():
    # Exhaust every feasible R/P/D combination, repeated at all candidate widths.
    for rendered in range(6):
        for pre_budget in range(rendered, 6):
            for dropped in range(2):
                rows = summaries()
                for k in exposure.CANDIDATE_KS:
                    rows[k].update(
                        cases_strict=rendered,
                        cases_pre_budget=pre_budget,
                        target_budget_dropped=dropped,
                    )
                if exposure.decide(rows)["outcome"] == "INVESTIGATE_RETRIEVAL":
                    assert all(rows[k]["cases_strict"] < 4 for k in exposure.CANDIDATE_KS)


def test_decide_rejects_incomplete_or_inconsistent_inputs():
    rows = summaries()
    rows[10]["cases_strict"] = 4
    with pytest.raises(ValueError, match="R <= P"):
        exposure.decide(rows)
    rows = summaries()
    rows[5]["coverage"].pop("q0")
    with pytest.raises(ValueError, match="50"):
        exposure.decide(rows)


def test_distractor_rule_entity_and_empty_cases():
    assert exposure.distractor_entity_tokens("What tea does Ari like?") == {"ari"}
    assert exposure.distractor_entity_tokens("what tea do i like?") == {"tea"}
    assert exposure.distractor_counts(
        "What did Ari buy?", [{"content": "Ari bought tea"}, {"content": "Jules bought tea"}]
    ) == {"added": 2, "plausible_support": 1}
    assert exposure.distractor_entity_tokens("What did I do?") == set()


def test_summary_attributes_prefix_vs_non_target_budget_loss():
    baseline = measure([memory(0)], gold=["session"])
    dropped = measure([memory(0)], gold=["session"], budget=1)
    changed = exposure.measure(
        injection.MemoryContextInputs(retrieved=[memory(1)]),
        target_ids=[],
        gold_conversation_ids=["session"],
        source_conversations={str(memory(1)["id"]): "other"},
        ranking=[],
    )
    questions = [
        {
            "question_id": f"q{pos}",
            "widths": {str(k): deepcopy(baseline) for k in exposure.SWEEP_KS},
            "source_conversations": {
                str(memory(0)["id"]): "session",
                str(memory(1)["id"]): "other",
            },
        }
        for pos in range(50)
    ]
    questions[0]["widths"]["10"] = changed
    questions[1]["widths"]["10"] = dropped
    summary = exposure.summarize(questions)[10]
    assert summary["coverage_losses"] == ["q0", "q1"]
    assert summary["coverage_causes"] == {
        "q0": "prefix_instability",
        "q1": "non_target_budget_drop",
    }


def test_logged_vector_matches_exact_text_user_model_and_duplicates():
    user = uuid.UUID(int=10)
    row = {
        "log_id": "one",
        "user_id": user,
        "query_text": "Ari tea?",
        "query_embedding_model": "model",
        "query_embedding": "[1,2]",
    }
    vector, ids = sweep.select_logged_vector(
        [row, {**row, "log_id": "two"}], user_id=user, text="Ari tea?", model="model", dimensions=2
    )
    assert vector == [1.0, 2.0] and ids == ["one", "two"]
    for field, value in (
        ("user_id", uuid.UUID(int=11)),
        ("query_text", "Ari  tea?"),
        ("query_embedding_model", "wrong"),
    ):
        with pytest.raises(ValueError, match="No matching"):
            sweep.select_logged_vector(
                [{**row, field: value}], user_id=user, text="Ari tea?", model="model", dimensions=2
            )
    with pytest.raises(ValueError, match="one,two"):
        sweep.select_logged_vector(
            [row, {**row, "log_id": "two", "query_embedding": "[1,3]"}],
            user_id=user,
            text="Ari tea?",
            model="model",
            dimensions=2,
        )
    for value in ("[0,0]", "[1]", "[NaN,1]", "[true,1]"):
        with pytest.raises(ValueError):
            sweep.select_logged_vector(
                [{**row, "query_embedding": value}],
                user_id=user,
                text="Ari tea?",
                model="model",
                dimensions=2,
            )


@pytest.mark.asyncio
async def test_cached_seam_refuses_uncached_and_restores_on_exception():
    original = injection.embed_query_with_metadata
    vector = EmbeddingVectorResult(
        embedding=[1.0], provider="cached", model="model", storage_model="model"
    )
    with pytest.raises(sweep.ProviderAccessDenied, match="Uncached"):
        with sweep.cached_query("Ari tea", vector):
            assert await injection.embed_query_with_metadata("Ari tea") is vector
            await injection.embed_query_with_metadata("Other text")
    assert injection.embed_query_with_metadata is original
    with pytest.raises(RuntimeError):
        with sweep.memory_width(20):
            assert injection.MAX_MEMORY_ITEMS == 20
            raise RuntimeError("synthetic")
    assert injection.MAX_MEMORY_ITEMS == 5


class FakePool:
    """Only synthetic SELECT results; writes and unexpected SQL fail immediately."""

    def __init__(self, dataset, encryption):
        self.rows = []
        self.logs = []
        self.vector_limits = []
        self.retrievals = 0
        self.encryption = encryption
        for entry in dataset:
            user = question_user_id(entry["question_id"])
            for pos in range(20):
                self.rows.append(
                    {
                        "id": uuid.uuid5(user, str(pos)),
                        "user_id": user,
                        "source_conversation_id": uuid.uuid5(user, "session"),
                        "content": encryption.encrypt(f"Ari enjoys tea number {pos}"),
                        "category": "fact",
                        "local_only": False,
                        "similarity": 1 - pos / 100,
                        "confidence": 1.0,
                        "trust_score": 1.0,
                        "last_accessed_at": datetime(2020, 1, 1, tzinfo=UTC),
                    }
                )
            self.logs.append(
                {
                    "log_id": str(uuid.uuid5(user, "log")),
                    "user_id": user,
                    "query_text": sweep.parity_query_text(entry["question"]),
                    "query_embedding_model": embedding_adapter.MODEL,
                    "query_embedding": "[1,2]",
                }
            )

    async def fetchrow(self, sql, *args):
        assert sql.lstrip().startswith("SELECT count(*) AS rows")
        return {"rows": 0, "digest": "synthetic"}

    async def fetch(self, sql, *args):
        assert sql.lstrip().startswith("SELECT")
        if "FROM retrieval_log" in sql:
            return self.logs
        if sql.startswith("SELECT id, user_id, source_conversation_id FROM memories"):
            return self.rows
        if "AS similarity" in sql:
            self.retrievals += 1
            self.vector_limits.append(args[5])
            return [row for row in self.rows if row["user_id"] == args[0]][: args[5]]
        if "FROM memories" in sql or "FROM entities" in sql:
            return []
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        if "SELECT EXISTS" in sql:
            return False
        raise AssertionError(sql)


def dataset():
    qids = [f"q{pos:02}" for pos in range(45)] + [
        *sweep.ABSTENTION_OVERREACH,
        "extra1_abs",
        "extra2_abs",
    ]
    return [
        {
            "question_id": qid,
            "question": " What\n tea does Ari like? ",
            "question_date": "2020/01/02 (Thu) 12:00",
            "haystack_dates": ["2020/01/01 (Wed) 12:00"],
            "haystack_session_ids": ["s1"],
            "haystack_sessions": [[{"role": "user", "content": "Ari enjoys tea"}]],
            "answer_session_ids": ["s1"],
        }
        for qid in qids
    ]


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("EMBEDDING_ROUTE_ID", "synthetic-selected-route")
    monkeypatch.setenv("RETRIEVAL_LOGGING_ENABLED", "false")
    monkeypatch.setenv("RETRIEVAL_LOGGING_DEBUG", "false")


async def synthetic_inputs():
    entries = dataset()
    encryption = ContentEncryption(Fernet.generate_key().decode())
    pool = FakePool(entries, encryption)
    fp = await fingerprint_benchmark_users(
        pool, [question_user_id(row["question_id"]) for row in entries]
    )
    inputs = sweep.SweepInputs(
        dataset=entries,
        targets={
            row["question_id"]: [str(uuid.uuid5(question_user_id(row["question_id"]), "8"))]
            for row in entries[:5]
        },
        gold={
            row["question_id"]: [str(uuid.uuid5(question_user_id(row["question_id"]), "session"))]
            for row in entries
        },
        model=embedding_adapter.MODEL,
        storage_model=embedding_adapter.STORAGE_MODEL,
        dimensions=2,
        fingerprint=fp["sha256"],
        shas={"dataset": "a" * 64},
    )
    return inputs, pool, MemoryStore(cast(asyncpg.Pool, pool), encryption)


@pytest.mark.asyncio
async def test_real_gather_renderer_parity_width_limit_clock_and_zero_compute(
    configured, monkeypatch
):
    inputs, pool, store = await synthetic_inputs()
    reserve = Mock(side_effect=AssertionError("reserve must never run"))
    metered = Mock(side_effect=AssertionError("metered must never run"))
    monkeypatch.setattr(compute_runtime.ComputeScope, "reserve_inference", reserve)
    monkeypatch.setattr(compute_runtime, "metered_embedding_call", metered)
    monkeypatch.setattr(embedding_adapter, "metered_embedding_call", metered)
    original = injection.embed_query_with_metadata
    raw, clean = await sweep.sweep_snapshot(pool, store, inputs)
    reserve.assert_not_called()
    metered.assert_not_called()
    assert clean["provider_calls"] == clean["embedding_calls"] == clean["chat_calls"] == 0
    assert clean["decision"]["outcome"] == "PROPOSE_WIDTH_CAMPAIGN"
    assert clean["decision"]["k"] == 10
    assert pool.vector_limits[:5] == [50, 10, 10, 15, 20]
    assert all(row["recency_boost"] == 1.0 for row in raw["questions"][0]["ranking"])
    assert [
        len(raw["questions"][0]["widths"][str(k)]["inputs"]["retrieved"]) for k in exposure.SWEEP_KS
    ] == [5, 10, 15, 20]
    for question in clean["questions"]:
        assert all(row["prefix_stable"] for row in question["widths"].values())
        assert question["vector_match_count"] == 1
    # Independent invocation of the unchanged parity wrapper at k=5.
    entry = inputs.dataset[0]
    result = EmbeddingVectorResult(
        embedding=[1.0, 2.0],
        provider="azure-openrouter",
        model=inputs.model,
        storage_model=inputs.storage_model,
    )
    token = retrieval.RECENCY_REFERENCE_TIME.set(question_as_of(entry))
    try:
        with (
            sweep.no_providers(),
            sweep.cached_query(sweep.parity_query_text(entry["question"]), result),
        ):
            block, _ = await build_parity_memory_context(
                store,
                user_id=question_user_id(entry["question_id"]),
                question_text=entry["question"],
                allowed_source_conversation_ids=None,
                query_reference_time=question_as_of(entry),
            )
    finally:
        retrieval.RECENCY_REFERENCE_TIME.reset(token)
    assert block == raw["questions"][0]["widths"]["5"]["memory_context"]
    assert injection.MAX_MEMORY_ITEMS == 5 and injection.embed_query_with_metadata is original
    assert retrieval.RECENCY_REFERENCE_TIME.get() is None
    assert clean["pilot_matches"] == clean["pilot_recoverable"] == 0
    sweep.validate_sanitized(clean)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    ["missing_vector", "conflicting_vector", "wrong_model", "missing_target", "fingerprint"],
)
async def test_preflight_failures_before_any_retrieval_or_output(configured, failure):
    inputs, pool, store = await synthetic_inputs()
    if failure == "missing_vector":
        pool.logs.pop()
    elif failure == "conflicting_vector":
        pool.logs.append({**pool.logs[0], "log_id": "different", "query_embedding": "[2,3]"})
    elif failure == "wrong_model":
        pool.logs[-1]["query_embedding_model"] = "wrong"
    elif failure == "missing_target":
        inputs.targets[inputs.dataset[0]["question_id"]].append("missing")
    else:
        inputs.fingerprint = "wrong"
    with pytest.raises(ValueError):
        await sweep.sweep_snapshot(pool, store, inputs)
    assert pool.retrievals == 0


@pytest.mark.asyncio
async def test_provider_entrypoints_refuse_and_restore():
    original = embedding_adapter.embed
    with pytest.raises(sweep.ProviderAccessDenied):
        with sweep.no_providers():
            await embedding_adapter.embed(["synthetic"], route_id="no-route", api_key="dummy")
    assert embedding_adapter.embed is original


def test_sanitized_writer_rejects_text_and_hashes_raw(tmp_path):
    raw = {"memory_context": "fictional memory text"}
    for value in ("x" * 129, "short prose", {"content": "tiny"}, {"question": "tiny"}):
        with pytest.raises(ValueError):
            sweep.validate_sanitized({"value": value})
    raw_path, clean_path = tmp_path / "raw.json", tmp_path / "sanitized.json"
    sweep.write_outputs(raw_path, clean_path, raw, {"id": "short-id"})
    saved = json.loads(clean_path.read_text())
    assert saved["raw_sha256"] == sweep.digest_bytes(raw_path.read_bytes())
    assert "fictional" not in clean_path.read_text()
    assert raw_path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="new"):
        sweep.write_outputs(raw_path, clean_path, raw, {"id": "short-id"})


def test_pilot_hash_only_recovery_and_conflicting_rows(tmp_path):
    path = tmp_path / "pilot.jsonl"
    path.write_text(json.dumps({"question_id": "q", "memory_context": "fictional block"}) + "\n")
    assert sweep.pilot_hashes(path) == {"q": sweep.digest_bytes(b"fictional block")}
    path.write_text(
        path.read_text() + json.dumps({"question_id": "q", "memory_context": "different"}) + "\n"
    )
    with pytest.raises(ValueError, match="Conflicting pilot"):
        sweep.pilot_hashes(path)


def local_input_files(tmp_path, monkeypatch):
    entries = dataset()
    results = {}
    for entry in entries:
        results[
            ingest_result_key(entry["question_id"], build_corpus_key(entry["haystack_sessions"][0]))
        ] = {"conversation_id": str(uuid.uuid5(question_user_id(entry["question_id"]), "session"))}
    checkpoint = {"phases": {"ingest": {"results": results}}}
    manifest = {"embedding": {"route_id": "synthetic-route"}}
    ingest = {
        "phases": {
            "ingest": {"memory_embeddings": {embedding_adapter.STORAGE_MODEL: {"memories": 1000}}}
        }
    }
    cases = {
        "cases": [
            {
                "question_id": row["question_id"],
                "classification": "prompt-selection-gap",
                "source_memories": [
                    {"id": str(uuid.uuid5(question_user_id(row["question_id"]), "8"))}
                ],
            }
            for row in entries[:5]
        ]
    }
    paths = {}
    shas = {}
    for name, data in (
        ("dataset", entries),
        ("ingest_checkpoint", checkpoint),
        ("ingest_manifest", ingest),
        ("phase3b_manifest", manifest),
        ("cases", cases),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(data))
        paths[name] = path
        shas[name] = sweep.digest_bytes(path.read_bytes())
    index = {
        "pins": {
            "ingest_checkpoint_sha256": shas["ingest_checkpoint"],
            "ingest_manifest_sha256": shas["ingest_manifest"],
        },
        "arms": {
            "A1": {
                "dataset_sha256": shas["dataset"],
                "artifacts_sha256": {"run_manifest.json": shas["phase3b_manifest"]},
                "restore": {"fingerprint_sha256": "f" * 64},
            }
        },
    }
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps(index))
    pilot = tmp_path / "pilot.jsonl"
    pilot.write_text(
        "\n".join(
            json.dumps({"question_id": row["question_id"], "memory_context_sha256": "a" * 64})
            for row in entries
        )
    )
    monkeypatch.setattr(sweep, "INDEX", index_path)
    monkeypatch.setattr(sweep, "CASES", paths["cases"])
    args = argparse.Namespace(
        **{name: paths[name] for name in paths if name != "cases"},
        phase3b_arm="A1",
        pilot_results=pilot,
    )
    return args


def test_input_pins_mapping_and_dry_run_without_db_or_provider(tmp_path, monkeypatch, capsys):
    args = local_input_files(tmp_path, monkeypatch)
    inputs = sweep.prepare_inputs(args)
    assert len(inputs.dataset) == 50 and len(inputs.targets) == 5
    assert len(inputs.gold) == 50 and len(inputs.pilot_hashes) == 50
    forbidden = Mock(side_effect=AssertionError("DB access forbidden"))
    monkeypatch.setattr(asyncpg, "connect", forbidden)
    argv = []
    for name in (
        "dataset",
        "ingest_checkpoint",
        "ingest_manifest",
        "phase3b_manifest",
        "pilot_results",
    ):
        argv.extend([f"--{name.replace('_', '-')}", str(getattr(args, name))])
    argv.extend(
        [
            "--raw-out",
            str(tmp_path / "raw.json"),
            "--sanitized-out",
            str(tmp_path / "clean.json"),
            "--dry-run",
        ]
    )
    with sweep.no_providers():
        assert sweep.main(argv) == 0
    forbidden.assert_not_called()
    assert not (tmp_path / "raw.json").exists()
    assert "no DB/provider access" in capsys.readouterr().out
    args.dataset.write_text("[]")
    with pytest.raises(ValueError, match="pin mismatch"):
        sweep.prepare_inputs(args)


def test_holdout_refused_without_opening(tmp_path):
    with pytest.raises(ValueError, match="Holdout"):
        sweep.pinned_json(tmp_path / "holdout.json", "a" * 64)


@pytest.mark.asyncio
async def test_reports_all_four_outcomes_deterministic_and_pilot_matches(configured):
    inputs, pool, store = await synthetic_inputs()
    _, clean = await sweep.sweep_snapshot(pool, store, inputs)
    clean.update(git_sha="b" * 40, raw_sha256="c" * 64)
    prior = {
        "zero_call_diagnostic": "width 10/20/30: 1/5; ranks 12–34 at pool 60",
        "v3_pilot": "failed: 4/50 majority flips; 10/150 matched flips",
    }
    for outcome in (
        "PROPOSE_WIDTH_CAMPAIGN",
        "BUDGET_LIMITED",
        "COVERAGE_REGRESSION",
        "INVESTIGATE_RETRIEVAL",
    ):
        changed = deepcopy(clean)
        for k in exposure.CANDIDATE_KS:
            row = changed["summaries"][str(k)]
            if outcome == "BUDGET_LIMITED":
                row["target_budget_dropped"] = 1
            elif outcome == "COVERAGE_REGRESSION":
                qid = inputs.dataset[0]["question_id"]
                row["coverage"][qid] = 0.0
                row["coverage_causes"][qid] = "prefix_instability"
            elif outcome == "INVESTIGATE_RETRIEVAL":
                row.update(cases_strict=3, cases_pre_budget=3)
        changed["decision"] = exposure.decide(
            {int(k): row for k, row in changed["summaries"].items()}
        )
        assert changed["decision"]["outcome"] == outcome
        report = sweep.render_report(changed, prior)
        assert report == sweep.render_report(changed, prior)
        for section in (
            "Input pins",
            "Calls and cost",
            "Exposure by width",
            "Per-target ranks",
            "Prefix stability",
            "Abstention distractor",
            "Decision",
            "Coverage changes",
        ):
            assert section in report
        assert outcome in report and "13/13" in report
        if outcome == "PROPOSE_WIDTH_CAMPAIGN":
            assert "v2 only" in report and "New restore and budget approvals" in report
    clean["decision"]["outcome"] = "BUDGET_LIMITED"
    with pytest.raises(ValueError, match="Saved decision"):
        sweep.render_report(clean, prior)
