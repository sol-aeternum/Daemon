"""Offline-only exactness audit: synthetic corpus, fake connections, no providers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock
import uuid

import asyncpg
from cryptography.fernet import Fernet
import pytest

from orchestrator.eval import exposure
from orchestrator.eval import retrieval_exactness as rules
from orchestrator.eval.bench_db import fingerprint_benchmark_users, question_user_id
from orchestrator.memory import injection, retrieval
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from scripts import memory_exposure_sweep as sweep
from scripts import memory_retrieval_exactness as audit
from tests import test_memory_exposure_sweep as synthetic


def summaries(probes=1):
    return {
        config.key: {
            "cases_strict": 3,
            "cases_pre_budget": 3,
            "target_budget_dropped": 0,
            "coverage": {f"q{pos}": 1.0 for pos in range(50)},
            "coverage_causes": {},
            "rendered_tokens": 100,
            "distractors": {"added": 0, "plausible_support": 0, "own_plausible_support": 0},
            "distractor_measured_ids": ["q0"],
            "distractor_measured_questions": 1,
            "distractors_by_question": {
                f"q{pos}": {"added": 0, "plausible_support": 0, "own_plausible_support": 0}
                if pos == 0
                else None
                for pos in range(50)
            },
        }
        for config in rules.configurations(probes)
    }


def decide(rows, probes=1, differences=(), connection_probes=None):
    return rules.decide_exactness(
        rows,
        connection_probes=connection_probes or {"harness": probes, "campaign": 10, "sweep": 10},
        plan_shape_differences=differences,
        missing_target_ranks=[{"memory_id": "target", "exact_cosine_rank": 101, "bm25_rank": None}],
    )


def qualify(rows, config):
    rows[config.key].update(cases_strict=4, cases_pre_budget=4)


def test_fixed_priority_vector_resolves_original_a_b_ambiguity():
    a, b = rules.Configuration(50, "probes10", 5), rules.Configuration(10, "probes10", 10)
    assert a.sort_key(1) == (2, (0, 1, 3), (1, 2, 0))
    assert b.sort_key(1) == (2, (0, 2, 3), (1, 0, 1))
    rows = summaries()
    for config in (b, a):
        qualify(rows, config)
    assert decide(rows)["minimal"] == a.key


def test_full_sort_of_all_23_nonbaseline_configs_is_total_without_ties():
    configs = [config for config in rules.configurations(1) if config != rules.BASELINE]
    keys = [config.sort_key(1) for config in configs]
    assert len(keys) == len(set(keys)) == 23
    ordered = sorted(configs, key=lambda config: config.sort_key(1))
    assert [config.key for config in ordered] == [
        "pool10_probes10_k5",
        "pool10_exact_k5",
        "pool25_harness_k5",
        "pool50_harness_k5",
        "pool100_harness_k5",
        "pool10_harness_k10",
        "pool25_probes10_k5",
        "pool50_probes10_k5",
        "pool100_probes10_k5",
        "pool25_exact_k5",
        "pool50_exact_k5",
        "pool100_exact_k5",
        "pool10_probes10_k10",
        "pool10_exact_k10",
        "pool25_harness_k10",
        "pool50_harness_k10",
        "pool100_harness_k10",
        "pool25_probes10_k10",
        "pool50_probes10_k10",
        "pool100_probes10_k10",
        "pool25_exact_k10",
        "pool50_exact_k10",
        "pool100_exact_k10",
    ]


def test_single_variable_beats_multiple_and_width_only_is_eligible():
    rows = summaries()
    multi = rules.Configuration(25, "probes10", 5)
    width = rules.Configuration(10, "harness", 10)
    qualify(rows, multi)
    qualify(rows, width)
    payload = {"added": 2, "plausible_support": 1, "own_plausible_support": 0}
    rows[width.key].update(rendered_tokens=150, distractors=payload)
    rows[width.key]["distractors_by_question"]["q0"] = dict(payload)
    result = decide(rows)
    assert result["minimal"] == width.key
    assert result["candidates"][0]["token_delta"] == 50
    assert result["candidates"][0]["distractor_deltas"] == payload
    assert result["candidates"][0]["distractor_measured_questions"] == 1
    assert rules.Configuration(25, "harness", 5).sort_key(1) < width.sort_key(1)


def test_harness10_collapses_duplicates_in_grid_and_sort_key():
    configs = rules.configurations(10)
    assert len(configs) == 16
    assert all(config.mode != "probes10" for config in configs)
    assert rules.Configuration(25, "probes10", 10).sort_key(10) == rules.Configuration(
        25, "harness", 10
    ).sort_key(10)
    assert len({config.sort_key(10) for config in configs}) == 16
    rows = summaries(10)
    assert rules.PARITY_GAP not in decide(rows, probes=10)["flags"]
    rows["pool10_probes10_k5"] = deepcopy(rows[rules.BASELINE.key])
    with pytest.raises(ValueError, match="collapsed grid"):
        decide(rows, probes=10)


@pytest.mark.parametrize("which", ["harness", "campaign", "sweep"])
def test_parity_flag_for_each_connection_is_independent(which):
    probes = {name: 10 for name in ("harness", "campaign", "sweep")}
    probes[which] = 1
    result = decide(summaries(probes["harness"]), connection_probes=probes)
    assert result["flags"] == {rules.PARITY_GAP, rules.QUERY_FORMULATION_CONFIRMED}


def test_parity_plan_shape_boundary_and_all_flags_at_once():
    rows = summaries(10)
    assert rules.PARITY_GAP not in decide(rows, probes=10)["flags"]
    good = rules.Configuration(25, "harness", 5)
    loss = rules.Configuration(50, "harness", 5)
    budget = rules.Configuration(100, "harness", 5)
    qualify(rows, good)
    qualify(rows, loss)
    rows[loss.key]["coverage"]["q0"] = 0.5
    rows[loss.key]["coverage_causes"]["q0"] = "prefix_instability"
    rows[budget.key].update(cases_pre_budget=4, target_budget_dropped=1)
    result = decide(rows, probes=10, differences=["q0:vector"])
    assert result["flags"] == {
        rules.PARITY_GAP,
        rules.RETRIEVAL_CANDIDATE,
        rules.COVERAGE_REGRESSION,
        rules.BUDGET_LIMITED,
        rules.QUERY_FORMULATION_CONFIRMED,
    }
    assert result["missing_target_ranks"][0]["exact_cosine_rank"] == 101


@pytest.mark.parametrize("cases", [3, 4])
@pytest.mark.parametrize("drops", [0, 1])
@pytest.mark.parametrize("coverage", [1.0, 0.5])
def test_flag_boundaries_and_none_is_unreachable_on_a_complete_valid_grid(cases, drops, coverage):
    rows = summaries(10)
    key = rules.Configuration(100, "exact", 10).key
    rows[key].update(cases_strict=cases, cases_pre_budget=cases, target_budget_dropped=drops)
    rows[key]["coverage"]["q0"] = coverage
    rows[key]["coverage_causes"]["q0"] = "non_target_budget_drop"
    result = decide(rows, probes=10)
    assert (rules.QUERY_FORMULATION_CONFIRMED in result["flags"]) == (cases < 4)
    assert (rules.RETRIEVAL_CANDIDATE in result["flags"]) == (
        cases >= 4 and drops == 0 and coverage == 1
    )
    assert (rules.COVERAGE_REGRESSION in result["flags"]) == (
        cases >= 4 and drops == 0 and coverage < 1
    )
    assert (rules.BUDGET_LIMITED in result["flags"]) == (cases >= 4 and drops > 0)
    # Exact/pool100/k10 necessarily triggers one of those four. Preserve the
    # registered NONE condition; do not fabricate a reachable NONE fixture.
    assert rules.NONE_OF_THE_ABOVE not in result["flags"]
    assert result["missing_target_ranks"] == (
        [] if cases >= 4 else [{"memory_id": "target", "exact_cosine_rank": 101, "bm25_rank": None}]
    )


def test_baseline_is_not_a_candidate_and_invalid_decision_inputs_fail_closed():
    rows = summaries()
    qualify(rows, rules.BASELINE)
    assert decide(rows)["candidates"] == []
    for mutation in ("missing", "count", "negative", "coverage", "cause"):
        changed = deepcopy(rows)
        row = changed[rules.Configuration(25, "harness", 5).key]
        if mutation == "missing":
            changed.pop(rules.BASELINE.key)
        elif mutation == "count":
            row["cases_strict"] = 4
        elif mutation == "negative":
            row["target_budget_dropped"] = -1
        elif mutation == "coverage":
            row["coverage"].pop("q0")
        else:
            row["coverage"]["q0"] = 0
        with pytest.raises(ValueError):
            decide(changed)


class FakeConnection(synthetic.FakePool):
    def __init__(self, dataset, encryption, probes=1):
        super().__init__(dataset, encryption)
        self.settings = {
            "ivfflat.probes": str(probes),
            **{name: "on" for name in audit.ENABLE_SETTINGS},
        }
        self.in_transaction = False
        self.statements = []
        self.bm25_limits = []
        self.closed = False
        self.force_bad_exact_plan = False
        self.wrong_probes100 = False
        self.fingerprint_drift = False
        self.fingerprint_reads = 0
        self.empty_retrieval_user: uuid.UUID | None = None
        self.empty_retrieval_limit = 25

    @asynccontextmanager
    async def transaction(self, *, isolation, readonly):
        assert isolation == "repeatable_read" and readonly is True
        assert not self.in_transaction
        self.in_transaction = True
        saved = dict(self.settings)
        try:
            yield
        finally:
            self.settings = saved
            self.in_transaction = False

    async def execute(self, sql):
        assert self.in_transaction
        assert sql.startswith("SET LOCAL ")
        assert not any(word in sql for word in ("ALTER", "ANALYZE", "INSERT", "UPDATE", "DELETE"))
        name, value = sql.removeprefix("SET LOCAL ").split(" = ")
        assert name in ("ivfflat.probes", *audit.ENABLE_SETTINGS)
        self.settings[name] = value
        self.statements.append(sql)

    async def fetch(self, sql, *args):
        assert self.in_transaction
        if "FROM pg_settings" in sql:
            return [
                {
                    "name": "ivfflat.probes",
                    "setting": self.settings["ivfflat.probes"],
                    "source": "default",
                    "reset_val": "1",
                }
            ]
        if "FROM pg_db_role_setting" in sql:
            return [{"setdatabase": 10, "setrole": 0, "option": "ivfflat.probes=1"}]
        if "FROM pg_indexes" in sql:
            return [
                {
                    "indexname": "idx_memories_embedding_ivfflat",
                    "indexdef": "CREATE INDEX idx_memories_embedding_ivfflat ON public.memories USING ivfflat (embedding vector_cosine_ops) WITH (lists='100')",
                },
                {
                    "indexname": "idx_memories_user",
                    "indexdef": "CREATE INDEX idx_memories_user ON public.memories USING btree (user_id, created_at)",
                },
            ]
        if "GROUP BY embedding_model" in sql:
            return [{"embedding_model": "synthetic", "count": len(self.rows)}]
        if "GROUP BY user_id" in sql:
            return [{"user_id": user, "count": 20} for user in args[0]]
        if " AS bm25_score" in sql:
            self.bm25_limits.append(args[5])
            if args[0] == self.empty_retrieval_user and args[5] == self.empty_retrieval_limit:
                return []
            rows = [row for row in self.rows if row["user_id"] == args[0]]
            return [
                {**row, "bm25_score": (pos + 1) / 100} for pos, row in list(enumerate(rows))[::-1]
            ][: args[5]]
        rows = await super().fetch(sql, *args)
        if (
            " AS similarity" in sql
            and args[0] == self.empty_retrieval_user
            and args[5] == self.empty_retrieval_limit
        ):
            return []
        if (
            self.wrong_probes100
            and " AS similarity" in sql
            and self.settings["ivfflat.probes"] == "100"
        ):
            return rows[:-1]
        return rows

    async def fetchrow(self, sql, *args):
        assert self.in_transaction
        row = await super().fetchrow(sql, *args)
        self.fingerprint_reads += 1
        if self.fingerprint_drift and self.fingerprint_reads > 5:
            return {**row, "digest": "changed"}
        return row

    async def fetchval(self, sql, *args) -> Any:
        assert self.in_transaction
        if sql == "SHOW transaction_read_only":
            return "on"
        if sql == "SHOW transaction_isolation":
            return "repeatable read"
        if sql.startswith("SHOW "):
            return self.settings[sql.removeprefix("SHOW ")]
        if "FROM pg_extension" in sql:
            return "0.8.0"
        if sql.startswith(audit.EXPLAIN):
            exact = self.settings["enable_indexscan"] == "off" and not self.force_bad_exact_plan
            node = {"Node Type": "Seq Scan" if exact else "Index Scan"}
            if not exact:
                node["Index Name"] = (
                    "idx_memories_embedding_ivfflat"
                    if self.settings["ivfflat.probes"] == "1"
                    else "idx_memories_user"
                )
            return json.dumps(
                [
                    {
                        "Plan": {
                            "Node Type": "Sort",
                            "Plans": [node],
                            "Filter": "private query terms must stay raw",
                        }
                    }
                ]
            )
        return await super().fetchval(sql, *args)

    async def close(self):
        self.closed = True


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("EMBEDDING_ROUTE_ID", "synthetic-selected-route")
    monkeypatch.setenv("RETRIEVAL_LOGGING_ENABLED", "false")
    monkeypatch.setenv("RETRIEVAL_LOGGING_DEBUG", "false")
    monkeypatch.setattr(audit, "PLAN_QUESTION_PREFIXES", ("q00", "q01"))


@asynccontextmanager
async def environment(monkeypatch, *, probes=1, pilot=True):
    inputs, _, _ = await synthetic.synthetic_inputs()
    encryption = ContentEncryption(Fernet.generate_key().decode())
    connection = FakeConnection(inputs.dataset, encryption, probes)
    reader = audit.RecordingReader(connection)
    store = MemoryStore(cast(asyncpg.Pool, reader), encryption)
    monkeypatch.setattr(
        audit,
        "MISMATCH_MEMORY_PREFIXES",
        tuple(mids[0][:8] for mids in list(inputs.targets.values())[:2]),
    )
    async with audit.readonly_snapshot(connection) as controls:
        inputs.fingerprint = (
            await fingerprint_benchmark_users(
                reader, [question_user_id(row["question_id"]) for row in inputs.dataset]
            )
        )["sha256"]
        if pilot:
            with sweep.no_providers():
                for entry in inputs.dataset:
                    block, _, _, _ = await audit.gather(
                        store, reader, inputs, entry, [1.0, 2.0], pool=10, k=5
                    )
                    inputs.pilot_hashes[entry["question_id"]] = sweep.digest_bytes(block.encode())
        connection.vector_limits.clear()
        connection.bm25_limits.clear()
        connection.retrievals = 0
        connection.fingerprint_reads = 0
        yield inputs, connection, reader, store, controls


@pytest.mark.asyncio
async def test_all_settings_are_local_in_readonly_repeatable_read_and_restore_on_exception(
    configured, monkeypatch
):
    async with environment(monkeypatch, pilot=False) as (_, connection, _, _, controls):
        for mode in rules.MODES:
            with pytest.raises(RuntimeError, match="synthetic"):
                async with controls.mode(mode):
                    assert connection.settings["ivfflat.probes"] == (
                        "10" if mode == "probes10" else "1"
                    )
                    assert connection.settings["enable_indexscan"] == (
                        "off" if mode == "exact" else "on"
                    )
                    raise RuntimeError("synthetic")
            assert connection.settings == {
                "ivfflat.probes": "1",
                **{name: "on" for name in audit.ENABLE_SETTINGS},
            }
        async with controls.mode("harness", probes=100):
            assert connection.settings["ivfflat.probes"] == "100"
        assert connection.statements and all(
            sql.startswith("SET LOCAL ") for sql in connection.statements
        )
        for name, value in (
            ("ivfflat.probes", "10; DROP"),
            ("evil", "on"),
            ("enable_indexscan", "evil"),
        ):
            with pytest.raises(ValueError):
                await controls.set_local(name, value)
    with pytest.raises(ValueError, match="active read-only"):
        await controls.set_local("ivfflat.probes", 10)


@pytest.mark.asyncio
async def test_pool_width_overrides_reach_both_sql_limits_and_render_slice(configured, monkeypatch):
    original = retrieval.retrieve_memories
    async with environment(monkeypatch, pilot=False) as (inputs, connection, reader, store, _):
        entry = inputs.dataset[0]
        block, gathered, queries, scored = await audit.gather(
            store, reader, inputs, entry, [1.0, 2.0], pool=25, k=10
        )
        assert connection.vector_limits == connection.bm25_limits == [25]
        assert queries["vector"].args[5] == queries["bm25"].args[5] == 25
        assert len(gathered.retrieved) == 10
        with audit.overrides(25, 10):
            assert len(exposure.rendered_memory_rows(gathered, block)) == 10
        assert (
            len(exposure.rendered_memory_rows(gathered, injection.render_memory_context(gathered)))
            == 5
        )
        assert all(row["recency_boost"] == 1 for row in scored)
        assert all("bm25_normalized" in row and "vector_sim" in row for row in scored)
    with pytest.raises(RuntimeError):
        with audit.overrides(100, 10), audit.capture_components():
            assert retrieval.INITIAL_VECTOR_CANDIDATES == 100 and injection.MAX_MEMORY_ITEMS == 10
            raise RuntimeError("synthetic")
    assert retrieval.INITIAL_VECTOR_CANDIDATES == 10 and injection.MAX_MEMORY_ITEMS == 5
    assert retrieval.retrieve_memories is original
    assert retrieval.RECENCY_REFERENCE_TIME.get() is None


@pytest.mark.asyncio
async def test_captured_queries_and_parameters_drive_exact_reference_without_retyped_filters(
    configured, monkeypatch
):
    async with environment(monkeypatch, pilot=False) as (
        inputs,
        connection,
        reader,
        store,
        controls,
    ):
        _, _, queries, _ = await audit.gather(
            store, reader, inputs, inputs.dataset[0], [1.0, 2.0], pool=10, k=5
        )
        query = queries["vector"]
        expanded = query.with_limit(None)
        assert expanded.sql == query.sql
        assert expanded.args[:5] == query.args[:5] and expanded.args[6:] == query.args[6:]
        for clause in (
            "status != 'deleted'",
            "tier != 'l0'",
            "valid_to IS NULL",
            "local_only = FALSE",
            "source_type != 'dream'",
            "embedding_model =",
            "memory_slot =",
            "source_conversation_id = ANY",
            "embedding IS NOT NULL",
        ):
            assert clause in query.sql
        raw, clean = await audit.ranking_audit(
            reader,
            controls,
            queries,
            inputs.targets[inputs.dataset[0]["question_id"]],
            verify_probes100=True,
        )
        assert clean["probes100_verified"] and len(clean["exact_ids"]) == 20
        assert clean["exact_plan"]["indexes"] == []
        assert raw["exact_plan"] != clean["exact_plan"]
        assert len(clean["approximations"]) == 6
        assert clean["approximations"][0]["recall10"] == 1
        assert clean["approximations"][0]["recall50"] == 0.5
        connection.force_bad_exact_plan = True
        with pytest.raises(ValueError, match="still uses an index"):
            await audit.ranking_audit(reader, controls, queries, [], verify_probes100=False)
        connection.force_bad_exact_plan = False
        connection.wrong_probes100 = True
        with pytest.raises(ValueError, match="differs from probes100"):
            await audit.ranking_audit(reader, controls, queries, [], verify_probes100=True)
    with pytest.raises(ValueError, match="terminal"):
        audit.CapturedQuery("vector", "SELECT 1", ()).with_limit(None)


def test_plan_hash_is_shape_only_and_distinguishes_indexes_and_node_types():
    def plan(node="Index Scan", index="idx_memories_embedding_ivfflat", cost=10):
        return [
            {
                "Plan": {
                    "Node Type": node,
                    "Index Name": index,
                    "Total Cost": cost,
                    "Filter": "secret prose",
                }
            }
        ]

    a = rules.plan_summary(plan())
    assert a == rules.plan_summary(plan(cost=100))
    assert a["ivfflat_used"] and not a["exact_non_ivfflat_plan"]
    assert a["shape_sha256"] != rules.plan_summary(plan(index="user_btree"))["shape_sha256"]
    assert a["shape_sha256"] != rules.plan_summary(plan(node="Bitmap Index Scan"))["shape_sha256"]
    sweep.validate_sanitized(a)
    assert "secret" not in json.dumps(a)


def test_stability_uses_observed_components_and_marks_missing_union_members_unknown():
    rows = {
        "10": [
            {"id": "a", "final_score": 0.5, "bm25_normalized": 1.0},
            {"id": "b", "final_score": 0.4, "bm25_normalized": 0.5},
        ],
        "25": [
            {"id": "a", "final_score": 0.2, "bm25_normalized": 0.1},
            {"id": "b", "final_score": 0.4, "bm25_normalized": 0.5},
        ],
    }
    stable = rules.pool_stability(rows)
    assert stable["fixed_union_order_observable"] and stable["fixed_union_order_changed"]
    assert stable["comparisons"][0]["changed_components"] == {
        "a": ["bm25_normalized", "final_score"]
    }
    rows["25"].append({"id": "c", "final_score": 0.1})
    missing = rules.pool_stability(rows)
    assert missing["missing_from_pool"]["10"] == ["c"]
    assert missing["fixed_union_order_changed"] is None
    assert "c" not in missing["components"]["10"]


@pytest.mark.asyncio
@pytest.mark.parametrize("probes,expected_count", [(1, 24), (10, 16)])
async def test_full_audit_production_path_baseline_grid_components_reports_and_zero_calls(
    configured, monkeypatch, probes, expected_count
):
    forbidden = Mock(side_effect=AssertionError("No real DB/provider access"))
    monkeypatch.setattr(asyncpg, "connect", forbidden)
    monkeypatch.setattr(asyncpg, "create_pool", forbidden)
    async with environment(monkeypatch, probes=probes) as (
        inputs,
        connection,
        reader,
        store,
        controls,
    ):
        # Exercise an overreach question without the _abs suffix as well.
        monkeypatch.setattr(sweep, "ABSTENTION_OVERREACH", (*sweep.ABSTENTION_OVERREACH, "q02"))
        raw, clean = await audit.audit_snapshot(
            reader,
            store,
            inputs,
            controls,
            {"harness": probes, "campaign": probes, "sweep": probes},
        )
        assert clean["pilot_matches"] == 50 and clean["grid_count"] == expected_count
        assert clean["probes100_verifications"] == 5
        assert len(clean["rankings"]) == len(clean["stability"]) == 50
        assert len(clean["plans"]) == 12
        assert all(len(question["configs"]) == expected_count for question in clean["questions"])
        measured = {
            entry["question_id"]
            for entry in inputs.dataset
            if entry["question_id"].endswith("_abs")
            or entry["question_id"] in sweep.ABSTENTION_OVERREACH
        }
        for question in clean["questions"]:
            for metrics in question["configs"].values():
                assert (metrics["distractors"] is not None) == (question["question_id"] in measured)
        for summary in clean["summaries"].values():
            assert summary["distractor_measured_questions"] == len(measured)
            assert summary["empty_retrievals"] == 0
        baseline = clean["summaries"][rules.BASELINE.key]["distractors"]
        assert baseline["added"] == baseline["plausible_support"] == 0
        assert baseline["own_plausible_support"] > 0
        # Each configuration is gathered separately; extra vector reads are T1
        # and the explicit gather20/rank50 mismatch probes, never cached grids.
        assert connection.vector_limits[:50] == [10] * 50
        assert clean["provider_calls"] == clean["embedding_calls"] == clean["chat_calls"] == 0
        assert clean["fingerprint_before"] == clean["fingerprint_after"]
        assert all(row["explanation"] == "unexplained" for row in clean["mismatches"])
        assert clean["settings_statement_count"] == len(connection.statements)
        assert raw["questions"] and raw["settings_statements"]
        assert (rules.PARITY_GAP in clean["decision"]["flags"]) == (probes != 10)
        clean.update(
            git_sha="b" * 40,
            raw_sha256="c" * 64,
            connection_recipes={"campaign": {"recipe_only": True}},
        )
        report = audit.render_report(clean)
        assert report == audit.render_report(clean)
        for section in (
            "T0",
            "T1",
            "T2",
            "T3",
            "Primary notes",
            "unexplained",
            "decide_exactness()",
            "no separate BM25",
            "NONE_OF_THE_ABOVE is retained as a safety net but is unreachable",
            "Measured questions",
            "own plausible support independently measures",
            "Empty retrievals",
        ):
            assert section in report
        sweep.validate_sanitized(clean)
        assert "Ari enjoys tea" not in json.dumps(clean)
        clean["decision"]["minimal"] = "tampered"
        with pytest.raises(ValueError, match="Saved decision"):
            audit.render_report(clean)
    forbidden.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["pilot", "missing_hash", "vector", "target", "before", "after", "window"]
)
async def test_preflight_and_baseline_stop_without_treating_other_grid_outputs_as_results(
    configured, monkeypatch, failure
):
    async with environment(monkeypatch) as (inputs, connection, reader, store, controls):
        if failure == "pilot":
            inputs.pilot_hashes[inputs.dataset[0]["question_id"]] = "a" * 64
        elif failure == "missing_hash":
            inputs.pilot_hashes.pop(inputs.dataset[0]["question_id"])
        elif failure == "vector":
            connection.logs.pop()
        elif failure == "target":
            inputs.targets[inputs.dataset[0]["question_id"]].append("missing")
        elif failure == "before":
            inputs.fingerprint = "a" * 64
        elif failure == "after":
            connection.fingerprint_drift = True
        else:
            inputs.vector_from = inputs.vector_until
        with pytest.raises(ValueError):
            await audit.audit_snapshot(
                reader, store, inputs, controls, {"harness": 1, "campaign": 1, "sweep": 1}
            )
        if failure == "pilot":
            assert connection.vector_limits == [10] * 50
        elif failure != "after":
            assert connection.retrievals == 0
        assert retrieval.INITIAL_VECTOR_CANDIDATES == 10 and injection.MAX_MEMORY_ITEMS == 5
        assert retrieval.RECENCY_REFERENCE_TIME.get() is None


@pytest.mark.asyncio
async def test_live_connection_recipes_are_source_equivalent_without_campaign_side_effects(
    configured, monkeypatch
):
    inputs, _, _ = await synthetic.synthetic_inputs()
    encryption = ContentEncryption(Fernet.generate_key().decode())
    connections = [FakeConnection(inputs.dataset, encryption, probes) for probes in (1, 10, 2)]
    pool_calls, connect_calls = [], []

    class Pool:
        def __init__(self, connection):
            self.connection = connection

        @asynccontextmanager
        async def acquire(self):
            yield self.connection

        async def close(self):
            await self.connection.close()

    async def create_pool(*args, **kwargs):
        pool_calls.append((args, kwargs))
        return Pool(connections[len(pool_calls) - 1])

    async def connect(*args, **kwargs):
        connect_calls.append((args, kwargs))
        return connections[2]

    async def fake_audit(reader, store, inputs, controls, probes):
        assert reader.connection is connections[2] and connections[2].in_transaction
        assert probes == {"harness": 1, "campaign": 10, "sweep": 2}
        assert connections[0].closed and connections[1].closed
        return {}, {"connection_probes": probes}

    dsn = "postgresql://synthetic.invalid/daemon_lme_bench_synthetic"
    monkeypatch.setattr(
        audit,
        "get_settings",
        lambda: SimpleNamespace(
            database_url=dsn,
            daemon_encryption_key=Fernet.generate_key().decode(),
            retrieval_logging_enabled=False,
            retrieval_logging_debug=False,
        ),
    )
    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(asyncpg, "connect", connect)
    monkeypatch.setattr(audit, "audit_snapshot", fake_audit)
    _, clean = await audit.run_live(inputs, concurrency=3)
    assert pool_calls == [
        ((), {"dsn": dsn, "min_size": 2, "max_size": 14}),
        (
            (dsn,),
            {
                "min_size": 10,
                "max_size": 10,
                "max_queries": 1000000,
                "max_inactive_connection_lifetime": 0,
                "timeout": 10,
                "command_timeout": 30,
            },
        ),
    ]
    assert connect_calls == [
        (
            (dsn,),
            {
                "timeout": 10,
                "server_settings": {
                    "default_transaction_read_only": "on",
                    "statement_timeout": "30000",
                },
            },
        )
    ]
    assert clean["connection_recipes"]["campaign"]["recipe_only"]
    assert all(connection.closed and not connection.statements for connection in connections)


def test_dry_run_reuses_input_pinning_and_never_connects_or_writes(
    configured, monkeypatch, tmp_path
):
    inputs = SimpleNamespace(
        fingerprint="5cb6ab14" + "a" * 56,
        vector_from=audit.VECTOR_FROM,
        vector_until=audit.VECTOR_UNTIL,
    )
    monkeypatch.setattr(sweep, "prepare_inputs", lambda args: inputs)
    forbidden = Mock(side_effect=AssertionError("Live access forbidden"))
    monkeypatch.setattr(audit, "run_live", forbidden)
    args = []
    for name in (
        "dataset",
        "ingest-checkpoint",
        "ingest-manifest",
        "phase3b-manifest",
        "pilot-results",
    ):
        args.extend([f"--{name}", str(tmp_path / f"{name}.json")])
    args.extend(
        [
            "--vector-from",
            audit.VECTOR_FROM.isoformat(),
            "--vector-until",
            audit.VECTOR_UNTIL.isoformat(),
            "--raw-out",
            str(tmp_path / "raw.json"),
            "--sanitized-out",
            str(tmp_path / "clean.json"),
            "--report-out",
            str(tmp_path / "report.md"),
            "--dry-run",
        ]
    )
    assert audit.main(args) == 0
    forbidden.assert_not_called()
    assert not (tmp_path / "raw.json").exists()
    inputs.fingerprint = "a" * 64
    with pytest.raises(ValueError, match="5cb6ab14"):
        audit.main(args)


def test_shared_output_sanitizer_rejects_text_and_preserves_raw_digest(tmp_path):
    raw = {"query_text": "synthetic private text", "sql": "SELECT synthetic"}
    raw_path, clean_path = tmp_path / "raw.json", tmp_path / "clean.json"
    sweep.write_outputs(raw_path, clean_path, raw, {"ids": ["synthetic-id"]})
    clean = json.loads(clean_path.read_text())
    assert clean["raw_sha256"] == sweep.digest_bytes(raw_path.read_bytes())
    assert "private text" not in clean_path.read_text()
    for value in ({"content": "hidden"}, {"plan": "SQL contains prose"}):
        with pytest.raises(ValueError):
            sweep.validate_sanitized(value)


def test_summarize_grid_keeps_unmeasured_none_and_counts_only_measured_questions():
    candidate = rules.Configuration(25, "harness", 5)
    questions = []
    for pos in range(50):
        qid = f"q{pos}"
        metrics = exposure.measure(
            injection.MemoryContextInputs(retrieved=[synthetic.memory(0)]),
            target_ids=[],
            gold_conversation_ids=[],
            source_conversations={},
            ranking=[],
        )
        metrics.update(
            empty_retrieval=False,
            distractors={"added": 0, "plausible_support": 0, "own_plausible_support": 2}
            if pos < 2
            else None,
        )
        current = deepcopy(metrics)
        if pos < 2:
            current["distractors"] = {
                "added": 3,
                "plausible_support": 1,
                "own_plausible_support": 4,
            }
        questions.append(
            {
                "question_id": qid,
                "distractors_measured": pos < 2,
                "source_conversations": {},
                "configs": {rules.BASELINE.key: metrics, candidate.key: current},
            }
        )
    baseline = rules.summarize_grid(questions, rules.BASELINE)
    result = rules.summarize_grid(questions, candidate)
    assert result["distractor_measured_questions"] == 2
    assert result["distractors"] == {"added": 6, "plausible_support": 2, "own_plausible_support": 8}
    assert result["distractors_by_question"]["q2"] is None
    assert baseline["distractors"] == {
        "added": 0,
        "plausible_support": 0,
        "own_plausible_support": 4,
    }
    rows = summaries()
    for row in rows.values():
        row.update(
            {
                key: baseline[key]
                for key in (
                    "distractors",
                    "distractors_by_question",
                    "distractor_measured_ids",
                    "distractor_measured_questions",
                )
            }
        )
    rows[candidate.key].update(
        {
            key: result[key]
            for key in (
                "distractors",
                "distractors_by_question",
                "distractor_measured_ids",
                "distractor_measured_questions",
            )
        }
    )
    qualify(rows, candidate)
    selected = decide(rows)["candidates"][0]
    assert selected["distractor_measured_questions"] == 2
    assert selected["distractor_deltas"] == {
        "added": 6,
        "plausible_support": 2,
        "own_plausible_support": 4,
    }
    for question in questions:
        question["distractors_measured"] = False
        for metrics in question["configs"].values():
            metrics["distractors"] = None
    unmeasured = rules.summarize_grid(questions, candidate)
    assert unmeasured["distractors"] is None
    assert unmeasured["distractor_measured_questions"] == 0


@pytest.mark.parametrize("config", [rules.BASELINE, rules.Configuration(25, "harness", 5)])
@pytest.mark.parametrize("missing", ["none", "payload", "field", "aggregate", "unmeasured"])
def test_decide_rejects_missing_measured_payload_even_when_config_does_not_qualify(config, missing):
    rows = summaries()
    row = rows[config.key]
    if missing == "none":
        row["distractors_by_question"]["q0"] = None
    elif missing == "payload":
        row["distractors_by_question"].pop("q0")
    elif missing == "field":
        row["distractors_by_question"]["q0"].pop("plausible_support")
    elif missing == "aggregate":
        row["distractors"]["added"] = 1
    else:
        row["distractors_by_question"]["q1"] = {
            "added": 0,
            "plausible_support": 0,
            "own_plausible_support": 0,
        }
    with pytest.raises(ValueError):
        decide(rows)


def test_zero_measured_cohort_produces_none_deltas_not_fictitious_zeros():
    rows = summaries()
    candidate = rules.Configuration(25, "harness", 5)
    for row in rows.values():
        row.update(
            distractors=None,
            distractor_measured_ids=[],
            distractor_measured_questions=0,
            distractors_by_question={qid: None for qid in row["coverage"]},
        )
    qualify(rows, candidate)
    selected = decide(rows)["candidates"][0]
    assert selected["distractor_deltas"] is None
    assert selected["distractor_measured_questions"] == 0


@pytest.mark.asyncio
async def test_nonbaseline_empty_candidates_are_recorded_and_reported(configured, monkeypatch):
    async with environment(monkeypatch) as (inputs, connection, reader, store, controls):
        entry = inputs.dataset[10]
        connection.empty_retrieval_user = question_user_id(entry["question_id"])
        with sweep.no_providers():
            block, gathered, queries, scored = await audit.gather(
                store,
                reader,
                inputs,
                entry,
                [1.0, 2.0],
                pool=25,
                k=10,
            )
        assert block == "" and gathered.retrieved == scored == []
        assert set(queries) == {"vector", "bm25"}
        _, clean = await audit.audit_snapshot(
            reader,
            store,
            inputs,
            controls,
            {"harness": 1, "campaign": 1, "sweep": 1},
        )
        for summary in clean["summaries"].values():
            assert summary["empty_retrievals"] == (1 if summary["config"]["pool"] == 25 else 0)
        question = next(
            row for row in clean["questions"] if row["question_id"] == entry["question_id"]
        )
        empty = question["configs"][rules.Configuration(25, "harness", 10).key]
        assert empty["empty_retrieval"] and empty["retrieved_ids"] == []
        assert empty["distractors"] is None
        clean.update(git_sha="b" * 40, raw_sha256="c" * 64, connection_recipes={})
        report = audit.render_report(clean)
        assert "Empty retrievals" in report
        for key, summary in clean["summaries"].items():
            if summary["config"]["pool"] == 25:
                line = next(line for line in report.splitlines() if line.startswith(f"| {key} |"))
                assert line.split(" | ")[7] == "1"
        sweep.validate_sanitized(clean)


@pytest.mark.asyncio
async def test_empty_baseline_is_fatal_before_the_remaining_grid(configured, monkeypatch):
    async with environment(monkeypatch) as (inputs, connection, reader, store, controls):
        connection.empty_retrieval_user = question_user_id(inputs.dataset[0]["question_id"])
        connection.empty_retrieval_limit = 10
        with pytest.raises(ValueError, match="Empty baseline retrieval"):
            await audit.audit_snapshot(
                reader,
                store,
                inputs,
                controls,
                {"harness": 1, "campaign": 1, "sweep": 1},
            )
        assert connection.vector_limits == connection.bm25_limits == [10]
        assert retrieval.INITIAL_VECTOR_CANDIDATES == 10 and injection.MAX_MEMORY_ITEMS == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["setup", "body"])
async def test_failed_transaction_restore_preserves_original_exception_before_and_after_yield(
    configured, monkeypatch, phase
):
    async with environment(monkeypatch, pilot=False) as (_, connection, _, _, controls):
        execute = connection.execute
        aborted = False
        original = RuntimeError("original diagnostic failure")

        async def fail_restore(sql):
            nonlocal aborted
            if aborted:
                raise asyncpg.InFailedSQLTransactionError("transaction aborted")
            if phase == "setup":
                aborted = True
                raise original
            await execute(sql)

        monkeypatch.setattr(connection, "execute", fail_restore)
        with pytest.raises(RuntimeError) as captured:
            async with controls.mode("exact"):
                aborted = True
                raise original
        assert captured.value is original
        assert original.__notes__ == ["SET LOCAL restore failed: InFailedSQLTransactionError"]


@pytest.mark.asyncio
async def test_restore_failure_without_original_exception_still_fails_closed(
    configured, monkeypatch
):
    async with environment(monkeypatch, pilot=False) as (_, connection, _, _, controls):
        execute = connection.execute
        fail = False

        async def fail_restore(sql):
            if fail:
                raise asyncpg.InFailedSQLTransactionError("transaction aborted")
            await execute(sql)

        monkeypatch.setattr(connection, "execute", fail_restore)
        with pytest.raises(asyncpg.InFailedSQLTransactionError):
            async with controls.mode("exact"):
                fail = True


def test_missing_ranks_consumes_real_exposure_measure_target_schema():
    visible = synthetic.memory(0)
    absent = synthetic.memory(1)
    mids = [str(visible["id"]), str(absent["id"])]
    metrics = exposure.measure(
        injection.MemoryContextInputs(retrieved=[visible]),
        target_ids=mids,
        gold_conversation_ids=[],
        source_conversations={},
        ranking=[visible],
    )
    questions = {"q": {"configs": {rules.Configuration(100, "exact", 10).key: metrics}}}
    rankings = [
        {
            "question_id": "q",
            "exact_cosine_ranks": dict(zip(mids, [1, 101], strict=True)),
            "bm25_ranks": dict(zip(mids, [2, None], strict=True)),
        }
    ]
    assert audit.missing_ranks(questions, {"q": mids}, rankings) == [
        {"question_id": "q", "memory_id": mids[1], "exact_cosine_rank": 101, "bm25_rank": None},
    ]
    metrics["targets"][mids[1]].pop("rendered")
    with pytest.raises(KeyError, match="rendered"):
        audit.missing_ranks(questions, {"q": mids}, rankings)
