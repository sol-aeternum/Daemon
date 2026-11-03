"""Synthetic-only tests: never load benchmark cases, holdout, DB or providers."""

from __future__ import annotations

import ast
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from orchestrator import compute_runtime
from orchestrator.entitlements.errors import CapabilityDenied, RateLimitExceeded
from orchestrator.eval import decomposition as rules
from orchestrator.eval import decomposition_generation as generation
from orchestrator.eval import exposure
from orchestrator.memory import injection
from scripts import memory_query_decomposition as cli


def memory(mid: str, score: float = 0.5, content: str = "Synthetic fact.") -> dict[str, Any]:
    return {
        "id": mid,
        "content": content,
        "category": "fact",
        "final_score": score,
        "local_only": False,
    }


def metric(mid: str | None, state: str = "rendered") -> dict[str, Any]:
    rows = [] if state == "absent" or mid is None else [memory(mid)]
    inputs = injection.MemoryContextInputs(retrieved=rows)
    measured = exposure.measure(
        inputs,
        target_ids=[mid] if mid else [],
        gold_conversation_ids=[],
        source_conversations={},
        ranking=rows,
        max_tokens=1 if state == "dropped" else 2500,
    )
    measured["distractors"] = None
    # Decision-only coverage fixture; maintain the same gold availability in
    # every configuration, independently of the target-state boundary lattice.
    measured["gold_session_coverage_proxy"] = 1.0
    measured["covered_gold_sessions"] = ["session"]
    return measured


def grid() -> list[dict[str, Any]]:
    return [
        {
            "question_id": f"synthetic-{index}",
            "configs": {
                key: metric(f"target-{index}" if index < 5 else None, "absent")
                for key in (rules.BASELINE, *rules.CONFIGS)
            },
            "source_conversations": {},
        }
        for index in range(50)
    ]


def counts(
    questions: list[dict[str, Any]], key: str, r: int, p: int | None = None, *, loss: bool = False
) -> None:
    p = r if p is None else p
    for index, question in enumerate(questions):
        question["configs"][key] = metric(
            f"target-{index}" if index < 5 else None,
            "rendered" if index < r else "dropped" if index < p else "absent",
        )
        if loss:
            question["configs"][rules.BASELINE]["gold_session_coverage_proxy"] = 1.0
            question["configs"][rules.BASELINE]["covered_gold_sessions"] = ["session"]
            question["configs"][key]["gold_session_coverage_proxy"] = 0.5
            question["configs"][key]["covered_gold_sessions"] = []


def config(source: str = "generated", merge: str = "rrf", k: int = 5) -> str:
    return rules.configuration(source, merge, k)


def test_generation_is_blind() -> None:
    assert list(inspect.signature(generation.generate_request).parameters) == ["question", "as_of"]
    tree = ast.parse(Path(generation.__file__).read_text())
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert set(imports) <= {"__future__", "datetime", "json", "typing"}
    request = generation.generate_request("What bicycle did I buy?", "2026-11-03")
    assert request["seed"] == 42
    assert request["response_format"]["json_schema"]["strict"] is True
    assert not {"temperature", "reasoning_effort", "include_reasoning", "top_p"} & request.keys()
    assert set(request) == {"model", "messages", "seed", "response_format", "max_tokens", "stream"}


@pytest.mark.parametrize(
    "value", [[], [""], ["  "], [" x "], ["x"] * 4, ["x" * 301], [None], [3], "query"]
)
def test_parser_rejects_invalid(value: Any) -> None:
    with pytest.raises(ValueError):
        generation.parse_queries(json.dumps({"queries": value}))


@pytest.mark.parametrize(
    "value",
    ['{"queries":["x"],"answer":"y"}', '{"queries":["x"],"queries":["y"]}', "[]", "not-json"],
)
def test_strict_json(value: str) -> None:
    with pytest.raises(ValueError):
        generation.parse_queries(value)


def test_query_boundaries() -> None:
    assert generation.parse_queries(json.dumps({"queries": ["x", "y", "z" * 300]})) == [
        "x",
        "y",
        "z" * 300,
    ]
    assert rules.query_set("  original\nquestion ", ["original question", " other ", "other"]) == [
        "original question",
        "other",
    ]
    with pytest.raises(ValueError):
        rules.query_set(" ", ["other"])


def test_merges_and_ties() -> None:
    rankings = [
        [memory("b", 0.5), memory("a", 0.6), memory("d", 0.9)],
        [memory("a", 0.6), memory("b", 0.5), memory("c", 0.9)],
    ]
    assert [row["id"] for row in rules.fuse(rankings, "rrf")] == ["a", "b", "c", "d"]
    assert [row["id"] for row in rules.fuse(rankings, "round_robin")] == ["b", "a", "d", "c"]
    assert [row["id"] for row in rules.fuse(rankings, "union_rescore")] == ["c", "d", "a", "b"]
    assert [row["id"] for row in rules.fuse([[memory("z"), memory("a")]], "rrf")] == ["z", "a"]
    assert len(rules.fuse([[memory("a"), memory("a")]], "rrf")) == 1


def test_render_substitution_byte_parity() -> None:
    inputs = injection.MemoryContextInputs(
        l0_memories=[memory("frozen")],
        retrieved=[memory(str(i), 1 - i / 100) for i in range(10)],
        summaries=[{"content": "Synthetic summary."}],
        retrieval_mode="embedding:synthetic",
    )
    original = deepcopy(inputs)
    for merge in rules.MERGES:
        substituted = rules.substitute(inputs, rules.fuse([inputs.retrieved], merge), 5)
        assert substituted.l0_memories is inputs.l0_memories
        assert substituted.summaries is inputs.summaries
        assert substituted.retrieval_mode == inputs.retrieval_mode
        assert injection.render_memory_context(substituted) == injection.render_memory_context(
            inputs
        )
    assert inputs == original and injection.MAX_MEMORY_ITEMS == 5


def test_unrenderable_reproduction_now_raises() -> None:
    questions = grid()
    for key in rules.CONFIGS:
        counts(questions, key, 4 if key.startswith("oracle_") else 3)
        rows = [memory("target-4", content="   ")]
        questions[4]["configs"][key] = exposure.measure(
            injection.MemoryContextInputs(retrieved=rows),
            target_ids=["target-4"],
            gold_conversation_ids=[],
            source_conversations={},
            ranking=rows,
        )
        questions[4]["configs"][key]["distractors"] = None
    row = questions[4]["configs"][config()]
    assert (
        row["target_pre_budget"] and not row["target_exposed"] and row["target_budget_dropped"] == 0
    )
    with pytest.raises(ValueError, match="case=synthetic-4 memory=target-4"):
        rules.check_case("synthetic-4", config(), row)
    with pytest.raises(ValueError, match="case=synthetic-4 memory=target-4"):
        rules.decide_decomposition(questions)


def test_ceiling_veto_and_prefer_k5() -> None:
    questions = grid()
    for k in rules.WIDTHS:
        counts(questions, config(k=k), 4)
        counts(questions, config("oracle", k=k), 3)
    decision = rules.decide_decomposition(questions)
    assert set(decision["flags"]) == {"CEILING_FAILS", "DECOMPOSITION_CANDIDATE"}
    assert decision["candidate"] == config() and decision["vetoed"] and decision["proposal"] is None
    counts(questions, config("oracle", k=10), 4)
    decision = rules.decide_decomposition(questions)
    assert decision["proposal"] == config() and not decision["vetoed"]


@pytest.mark.parametrize(
    "r,p,loss,expected",
    [
        (3, 3, False, "MODEL_GAP"),
        (3, 4, False, "BUDGET_LIMITED"),
        (4, 4, False, "DECOMPOSITION_CANDIDATE"),
        (4, 4, True, "COVERAGE_REGRESSION"),
        (4, 5, False, "BUDGET_LIMITED"),
    ],
)
def test_decision_boundaries(r: int, p: int, loss: bool, expected: str) -> None:
    questions = grid()
    counts(questions, config("oracle"), 4)
    for k in rules.WIDTHS:
        counts(questions, config(k=k), r, p, loss=loss)
    assert expected in rules.decide_decomposition(questions)["flags"]


def test_secondary_lead_and_multiple_flags() -> None:
    questions = grid()
    counts(questions, config("oracle"), 4)
    counts(questions, config(merge="round_robin"), 4)
    counts(questions, config(merge="union_rescore", k=10), 4, loss=True)
    result = rules.decide_decomposition(questions)
    assert set(result["flags"]) == {"MODEL_GAP", "MERGE_LEAD", "COVERAGE_REGRESSION"}
    assert result["proposal"] is None
    # Oracle-only regression never raises the generated coverage flag.
    questions = grid()
    counts(questions, config("oracle"), 4, loss=True)
    assert rules.decide_decomposition(questions)["flags"] == ["MODEL_GAP"]


def test_totality_for_all_valid_single_target_counts() -> None:
    # Every emitted input passes the per-target check. Exhaust the R/P boundary
    # lattice, both oracle threshold sides, and coverage preserved/lost.
    for p in range(6):
        for r in range(p + 1):
            for oracle_r in (3, 4):
                for loss in (False, True):
                    questions = grid()
                    for k in rules.WIDTHS:
                        counts(questions, config(k=k), r, p, loss=loss)
                        counts(questions, config("oracle", k=k), oracle_r)
                    for question in questions[:5]:
                        for key, row in question["configs"].items():
                            rules.check_case(question["question_id"], key, row)
                    assert rules.decide_decomposition(questions)["flags"]


def test_incomplete_grid_rejected() -> None:
    questions = grid()
    with pytest.raises(ValueError, match="50 distinct"):
        rules.decide_decomposition(questions[:-1])
    questions[0]["configs"].pop(config())
    with pytest.raises(ValueError, match="complete configuration"):
        rules.decide_decomposition(questions)


def test_cost_cap_and_missing_receipts() -> None:
    accounting = cli.Accounting()
    accounting.before(Decimal(".10"))
    accounting.add(
        kind="chat",
        qid="synthetic",
        ceiling=Decimal(".10"),
        started=0,
        receipt={"usage": {"cost": ".10"}},
        status="received",
    )
    accounting.before(Decimal(0))
    with pytest.raises(ValueError, match="cap"):
        accounting.before(Decimal(".0000001"))
    for receipt in (
        {},
        {"usage": {}},
        {"usage": {"cost": "NaN"}},
        {"usage": {"cost": -1}},
        {"usage": {"cost": True}},
    ):
        with pytest.raises(ValueError):
            cli.reported_cost(receipt)
    unknown = cli.Accounting()
    unknown.add(
        kind="chat",
        qid="synthetic",
        ceiling=Decimal(".09"),
        started=0,
        receipt=None,
        status="failed",
    )
    with pytest.raises(ValueError, match="cap"):
        unknown.before(Decimal(".02"))


def response(text: str) -> dict[str, Any]:
    return {
        "usage": {"cost": ".00001"},
        "choices": [{"finish_reason": "stop", "message": {"content": text}}],
    }


@pytest.mark.asyncio
async def test_identical_retries_separate_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "verify_chat_route", lambda: object())
    monkeypatch.setattr(cli, "chat_ceiling", lambda request, route: Decimal(".001"))
    monkeypatch.setattr(cli.asyncio, "sleep", AsyncMock())
    requests = []
    results: list[Any] = [
        compute_runtime.ComputeUnavailable("capacity", "synthetic", status_code=429),
        response("bad json"),
        compute_runtime.ComputeUnavailable("capacity", "synthetic", status_code=429),
        response('{"queries":["synthetic query"]}'),
    ]

    async def dispatch(**request: Any) -> Any:
        requests.append(rules.canonical(request))
        result = results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    accounting = cli.Accounting()
    row = await cli.generate_one("Synthetic?", "2026-11-03", "synthetic", accounting, dispatch)
    assert len(set(requests)) == 1
    assert (row["attempts"], row["retries_429"], row["parse_retries"]) == (4, 2, 1)
    assert len(accounting.records) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["parse", "429", "503", "cost"])
async def test_generation_failure_stops(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setattr(cli, "verify_chat_route", lambda: object())
    monkeypatch.setattr(cli, "chat_ceiling", lambda request, route: Decimal(".001"))
    monkeypatch.setattr(cli.asyncio, "sleep", AsyncMock())

    async def dispatch(**request: Any) -> Any:
        if mode in {"429", "503"}:
            raise compute_runtime.ComputeUnavailable("capacity", "synthetic", status_code=int(mode))
        if mode == "cost":
            return {"usage": {}, "choices": []}
        return response("not-json")

    accounting = cli.Accounting()
    with pytest.raises(ValueError):
        await cli.generate_one("Synthetic?", "2026-11-03", "synthetic-id", accounting, dispatch)
    assert len(accounting.records) == {"parse": 2, "429": 3, "503": 1, "cost": 1}[mode]


def test_table_write_allowlist_exact() -> None:
    before = {key: [0, 0, 0] for key in cli.WRITE_ALLOWLIST}
    before["public.memories"] = [1, 2, 3]
    after = deepcopy(before)
    after["public.entitlement_reservations"] = [1, 1, 0]
    assert cli.check_table_writes(before, after) == {"public.entitlement_reservations": [1, 1, 0]}
    after["public.memories"] = [1, 3, 3]
    with pytest.raises(ValueError, match="Unapproved table"):
        cli.check_table_writes(before, after)
    with pytest.raises(ValueError, match="inventory"):
        cli.check_table_writes(before, {})
    with pytest.raises(ValueError, match="reset"):
        cli.check_table_writes(after, before)
    assert "public.entitlement_limit_encounters" not in cli.WRITE_ALLOWLIST


def test_denial_encounter_write_is_fatal() -> None:
    before = {"public.entitlement_limit_encounters": [0, 0, 0]}
    after = {"public.entitlement_limit_encounters": [1, 0, 0]}
    with pytest.raises(
        ValueError, match="Unapproved table writes: public.entitlement_limit_encounters"
    ):
        cli.check_table_writes(before, after)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "denial", [CapabilityDenied("chat"), RateLimitExceeded(requests_in_window=1, ceiling=1)]
)
async def test_entitlement_denial_stops_without_retry(
    monkeypatch: pytest.MonkeyPatch, denial: Exception
) -> None:
    monkeypatch.setattr(cli, "verify_chat_route", lambda: object())
    monkeypatch.setattr(cli, "chat_ceiling", lambda request, route: Decimal(".001"))
    dispatch = AsyncMock(side_effect=denial)
    with pytest.raises(ValueError, match="Generation dispatch stopped: synthetic-denied"):
        await cli.generate_one(
            "Synthetic?", "2026-11-03", "synthetic-denied", cli.Accounting(), dispatch
        )
    assert dispatch.await_count == 1
    monkeypatch.setattr(
        cli.embedding_adapter,
        "approved_route",
        lambda route_id: SimpleNamespace(estimate_microusd=lambda tokens: 1),
    )
    embed = AsyncMock(side_effect=denial)
    monkeypatch.setattr(cli.embedding, "embed_query_with_metadata", embed)
    with pytest.raises(type(denial)):
        await cli.embed_subquery("Synthetic query", "synthetic-denied", cli.Accounting())
    assert embed.await_count == 1


def test_sanitizer_single_field_exception() -> None:
    data = {
        "sub_queries": {"generated": {"synthetic-id": ["What bicycle did I buy?"]}, "oracle": {}},
        "settings": {"depth": 10},
    }
    cli.validate_sanitized(data)
    for value in (
        {**data, "memory_context": "private"},
        {**data, "settings": {"sub_queries": ["private text"]}},
        {**data, "question": "private"},
        {**data, "sub_queries": {"generated": {"synthetic": {"content": "private"}}, "oracle": {}}},
    ):
        with pytest.raises(ValueError):
            cli.validate_sanitized(value)


def test_frozen_design_and_clean_commit_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    frozen = tmp_path / "frozen.json"
    frozen.write_bytes(rules.frozen_design())
    monkeypatch.setattr(cli, "FROZEN", frozen)
    monkeypatch.setattr(cli, "require_committed", lambda path: None)
    monkeypatch.setattr(cli, "git", lambda *args: b"")
    assert cli.freeze_check() == cli.sweep.digest_bytes(frozen.read_bytes())
    monkeypatch.setattr(cli, "git", lambda *args: b" M modified")
    with pytest.raises(ValueError, match="clean committed"):
        cli.freeze_check()
    frozen.write_text("{}")
    with pytest.raises(ValueError, match="in-code"):
        cli.freeze_check()


def test_frozen_artifact_matches_source_bytes() -> None:
    assert cli.FROZEN.read_bytes() == rules.frozen_design()


@pytest.mark.asyncio
async def test_no_answer_judge_transport_guard() -> None:
    with cli.no_answers_or_judges(), pytest.raises(cli.sweep.ProviderAccessDenied):
        await cli.evaluate._call_llm_with_provider_config(
            model="synthetic", messages=[], temperature=0, max_tokens=1
        )


def test_route_validation_negative_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    options = {
        "only": ["azure/eu"],
        "order": ["azure/eu"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "data_collection": "deny",
        "zdr": True,
    }
    route = SimpleNamespace(
        route_id=generation.ROUTE_ID,
        model=generation.MODEL,
        provider="openrouter",
        endpoint=cli.embedding_adapter.ENDPOINT,
        price_ceiling=object(),
        is_approved=lambda req: True,
        transport_payload=lambda req: {"extra_body": {"provider": options}},
    )
    monkeypatch.setattr(
        cli,
        "load_inference_policy",
        lambda: SimpleNamespace(routes={generation.ROUTE_ID: route}, requirements=object()),
    )
    assert cli.verify_chat_route() is route
    for field, value in (
        ("model", "wrong"),
        ("route_id", "wrong"),
        ("provider", "wrong"),
        ("endpoint", "wrong"),
        ("price_ceiling", None),
    ):
        previous = getattr(route, field)
        setattr(route, field, value)
        with pytest.raises(ValueError):
            cli.verify_chat_route()
        setattr(route, field, previous)
    for key, value in (
        ("zdr", False),
        ("allow_fallbacks", True),
        ("require_parameters", False),
        ("only", ["azure"]),
    ):
        previous = options[key]
        options[key] = value
        with pytest.raises(ValueError):
            cli.verify_chat_route()
        options[key] = previous


@pytest.mark.asyncio
async def test_fingerprint_drift_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli, "fingerprint_benchmark_users", AsyncMock(return_value={"sha256": "wrong"})
    )
    with pytest.raises(ValueError, match="fingerprint"):
        await cli.fingerprint(object(), ["synthetic"], "5cb6ab14-synthetic")


@pytest.mark.asyncio
async def test_full_measurement_fake_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from scripts import memory_exposure_sweep as sweep

    qids = [f"synthetic-{i}" for i in range(50)]
    all_rows = {qid: [memory(f"target-{i}")] for i, qid in enumerate(qids)}
    dataset = [
        {"question_id": qid, "question": f"Synthetic question {i}"} for i, qid in enumerate(qids)
    ]

    async def gather(store: Any, *, user_id: Any, question_text: str, **kwargs: Any) -> Any:
        index = int(question_text.rsplit(" ", 1)[1])
        inputs = injection.MemoryContextInputs(
            retrieved=all_rows[qids[index]], retrieval_mode="embedding:synthetic"
        )
        return injection.render_memory_context(inputs), inputs

    async def retrieve(**kwargs: Any) -> Any:
        assert kwargs["limit"] == 10 and kwargs["touch_results"] is False
        assert kwargs["include_local"] is False
        assert kwargs["retrieval_triggered_by"] == "longmemeval"
        return all_rows[qids[int(kwargs["query_text"].rsplit(" ", 1)[1])]]

    async def fetch(sql: str, *args: Any) -> Any:
        if "FROM memories" in sql:
            return [
                {
                    "id": f"target-{i}",
                    "user_id": cli.question_user_id(qid),
                    "source_conversation_id": "synthetic-session",
                }
                for i, qid in enumerate(qids)
            ]
        return []

    inputs = sweep.SweepInputs(
        dataset=dataset,
        targets={qid: [f"target-{i}"] for i, qid in enumerate(qids[:5])},
        gold={qid: [] for qid in qids},
        model="synthetic",
        storage_model="synthetic",
        dimensions=2,
        fingerprint="5cb6ab14-synthetic",
        shas={},
        route_id="synthetic",
        vector_from=cli.exactness.VECTOR_FROM,
        vector_until=cli.exactness.VECTOR_UNTIL,
        pilot_hashes={
            qid: sweep.digest_bytes(
                injection.render_memory_context(
                    injection.MemoryContextInputs(retrieved=all_rows[qid])
                ).encode()
            )
            for qid in qids
        },
    )
    monkeypatch.setattr(sweep, "verify_embedding_context", lambda inputs: None)
    monkeypatch.setattr(
        sweep, "select_logged_vector", lambda *args, **kwargs: ([1.0, 0.0], ["synthetic-log"], {})
    )
    monkeypatch.setattr(cli, "fingerprint", AsyncMock(return_value=inputs.fingerprint))
    monkeypatch.setattr(cli, "question_as_of", lambda entry: datetime(2026, 11, 3, tzinfo=UTC))
    monkeypatch.setattr(cli.evaluate, "build_parity_memory_context", gather)
    monkeypatch.setattr(cli.retrieval, "retrieve_memories_for_text", retrieve)
    generated = {entry["question_id"]: [entry["question"]] for entry in dataset}
    oracle = {qid: generated[qid] for qid in qids[:5]}
    raw, clean = await cli.measure_snapshot(
        SimpleNamespace(fetch=fetch), object(), inputs, generated, oracle, {}
    )
    assert clean["pilot_matches"] == clean["single_query_matches"] == 50
    assert clean["decision"]["flags"] == ["DECOMPOSITION_CANDIDATE"]
    assert len(clean["target_attribution"]) == 10
    assert len(raw["questions"]) == 50
    assert injection.MAX_MEMORY_ITEMS == 5
    clean.update(
        {
            "questions": cli.compact_questions(clean["questions"]),
            "sub_queries": {"generated": generated, "oracle": oracle},
            "frozen_sha256": "a" * 64,
            "oracle_sha256": "b" * 64,
            "fingerprint_before": "5cb6ab14-synthetic",
            "fingerprint_after": "5cb6ab14-synthetic",
            "git_sha": "c" * 40,
            "accounting": cli.accounting_summary(cli.Accounting()),
            "table_changes": {},
            "query_count_distribution": {"1": 50},
            "added_query_share": 0,
        }
    )
    paths = [tmp_path / name for name in ("raw.json", "sanitized.json", "report.md")]
    cli.write_outputs(paths[0], paths[1], paths[2], raw, clean)
    saved = json.loads(paths[1].read_bytes())
    assert saved["decision"] == clean["decision"]
    assert len(paths[1].read_bytes()) <= 512_000
    assert len(paths[2].read_text().splitlines()) <= 400
    assert "## Primary notes" in paths[2].read_text()
    assert "memory_context" not in paths[1].read_text()
    all_rows[qids[4]][0]["content"] = "   "
    inputs.pilot_hashes[qids[4]] = sweep.digest_bytes(b"")
    with pytest.raises(ValueError, match="case=synthetic-4 memory=target-4"):
        await cli.measure_snapshot(
            SimpleNamespace(fetch=fetch), object(), inputs, generated, oracle, {}
        )


@pytest.mark.asyncio
async def test_embedding_cost_capture_with_fake_production_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = SimpleNamespace(estimate_microusd=lambda tokens: 5)
    monkeypatch.setattr(cli.embedding_adapter, "approved_route", lambda route_id: route)
    receipt = {"usage": {"cost": ".000001"}}
    monkeypatch.setattr(cli.embedding_adapter, "_post", AsyncMock(return_value=receipt))

    async def embed(text: str) -> Any:
        await cast(Any, cli.embedding_adapter._post)(None, route, [text], "fictional-test-only", {})
        return SimpleNamespace(
            model=cli.embedding_adapter.MODEL,
            storage_model=cli.embedding_adapter.STORAGE_MODEL,
            embedding=[1.0] * 1024,
        )

    monkeypatch.setattr(cli.embedding, "embed_query_with_metadata", embed)
    accounting = cli.Accounting()
    assert len(await cli.embed_subquery("Synthetic query", "synthetic", accounting)) == 1024
    assert accounting.records[0]["receipt"] == receipt
    assert accounting.total == Decimal(".000001")
    receipt["usage"] = {}
    with pytest.raises(Exception):
        await cli.embed_subquery("Synthetic query", "synthetic", accounting)
    assert accounting.records[-1]["cost_usd"] is None
