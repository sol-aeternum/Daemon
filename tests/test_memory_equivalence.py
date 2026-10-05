"""Offline production PLAN/revalidate/COMMIT safety and conservative disposition."""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.compute_runtime import ComputeUnavailable
from orchestrator.memory import dedup, equivalence, tools
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.extraction import ExtractedFact
from orchestrator.memory.store import MemoryStore, compute_memory_content_hash

USER = uuid.UUID(int=100)
NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)
PIZZA = "User's favourite pizza is pepperoni"


def incoming(**kwargs: Any) -> equivalence.IncomingMemory:
    values: dict[str, Any] = dict(
        user_id=USER,
        content="User prefers pepperoni pizza above other pizzas",
        category="preference",
        source_type="extracted",
        conversation_id=uuid.UUID(int=7),
        slot="food.favorite_pizza",
    )
    values.update(kwargs)
    return equivalence.IncomingMemory(**values)


def row(**kwargs: Any) -> dict[str, Any]:
    values: dict[str, Any] = dict(
        id=uuid.UUID(int=1),
        user_id=USER,
        content=PIZZA,
        updated_at=NOW,
        category="preference",
        source_type="user_created",
        memory_slot="food.favourite",
        local_only=False,
        tier="l1",
        status="active",
        valid_from=NOW,
        valid_to=None,
        confidence=0.9,
        metadata={},
        source_conversation_id=None,
    )
    values.update(kwargs)
    return values


def reply(ids, verdict="equivalent", finish="stop"):
    return {
        "choices": [
            {
                "finish_reason": finish,
                "message": {
                    "content": json.dumps(
                        {
                            "verdicts": [{"candidate_id": str(i), "verdict": verdict} for i in ids],
                        }
                    )
                },
            }
        ]
    }


@pytest.fixture
def judge(monkeypatch):
    monkeypatch.setattr(equivalence, "current_scope", lambda: SimpleNamespace(user_id=USER))
    mock = AsyncMock(return_value=reply([uuid.UUID(int=1)]))
    monkeypatch.setattr(equivalence, "guarded_completion", mock)
    return mock


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", [None, "unknown", "content_filter", "length", "tool_calls", ""])
async def test_equivalent_json_requires_normal_terminal(judge, finish):
    judge.return_value = reply([uuid.UUID(int=1)], finish=finish)
    assert (await equivalence.plan_equivalence(incoming(), [row()])).equivalent_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [{"refusal": "refused"}, {"tool_calls": [{"id": "x"}]}, {"function_call": {"name": "x"}}],
)
@pytest.mark.parametrize("sdk", [False, True])
async def test_equivalent_json_with_refusal_or_calls_is_not_accepted(judge, extra, sdk):
    response = reply([uuid.UUID(int=1)])
    response["choices"][0]["message"].update(extra)
    if sdk:
        choice = response["choices"][0]
        choice["message"] = SimpleNamespace(**choice["message"])
        response = SimpleNamespace(choices=[SimpleNamespace(**choice)])
    judge.return_value = response
    assert (await equivalence.plan_equivalence(incoming(), [row()])).equivalent_id is None


@pytest.mark.asyncio
async def test_missing_terminal_and_multiple_choices_retain_fact(judge):
    response = reply([uuid.UUID(int=1)])
    del response["choices"][0]["finish_reason"]
    judge.return_value = response
    assert (await equivalence.plan_equivalence(incoming(), [row()])).equivalent_id is None
    response = reply([uuid.UUID(int=1)])
    response["choices"] *= 2
    judge.return_value = response
    assert (await equivalence.plan_equivalence(incoming(), [row()])).equivalent_id is None


@pytest.mark.parametrize("style", ["dict", "sdk", "dump"])
def test_normal_terminal_text_control(style):
    response = reply([uuid.UUID(int=1)])
    expected = response["choices"][0]["message"]["content"]
    if style == "sdk":
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content=expected, refusal=None, tool_calls=[], function_call=None
                    ),
                )
            ]
        )
    elif style == "dump":
        original = response
        response = SimpleNamespace(model_dump=lambda: original)
    assert equivalence.strict_judge_content(response) == expected


@pytest.mark.asyncio
async def test_pizza_full_payload_cross_source_slot_and_role(judge):
    fact = incoming()
    plan = await equivalence.plan_equivalence(fact, [row()])
    assert plan.equivalent_id == uuid.UUID(int=1)
    sent = judge.await_args.kwargs
    payload = json.loads(sent["messages"][1]["content"])
    assert payload["incoming"]["content"] == fact.content
    assert payload["candidates"][0]["content"] == PIZZA
    assert "model" not in sent and equivalence.EQUIVALENCE_PROFILE == "background"
    assert "reason" not in payload["candidates"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["distinct", "correction", "uncertain"])
@pytest.mark.parametrize(
    "content",
    [
        "User no longer likes pepperoni pizza",
        "User likes mushroom pizza",
        "User's partner prefers pepperoni pizza",
        "User used to prefer pepperoni pizza in 2024",
        "User prefers pepperoni pizza only when travelling",
        "User might like pepperoni pizza",
    ],
)
async def test_differences_never_merge(judge, verdict, content):
    judge.return_value = reply([uuid.UUID(int=1)], verdict)
    plan = await equivalence.plan_equivalence(
        incoming(content=content, slot="food.current"), [row()]
    )
    assert plan.equivalent_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,finish",
    [
        ("not json", "stop"),
        ("{}", "stop"),
        ('{"verdicts": []}', "stop"),
        (
            json.dumps(
                {"verdicts": [{"candidate_id": str(uuid.UUID(int=2)), "verdict": "equivalent"}]}
            ),
            "stop",
        ),
        (
            json.dumps({"verdicts": [{"candidate_id": str(uuid.UUID(int=1)), "verdict": "same"}]}),
            "stop",
        ),
        (
            json.dumps(
                {
                    "verdicts": [
                        {
                            "candidate_id": str(uuid.UUID(int=1)),
                            "verdict": "equivalent",
                            "reason": "x",
                        }
                    ]
                }
            ),
            "stop",
        ),
        (
            json.dumps(
                {"verdicts": [{"candidate_id": str(uuid.UUID(int=1)), "verdict": "equivalent"}]}
            ),
            "length",
        ),
        ("", "stop"),
    ],
)
async def test_unusable_or_truncated_verdict_preserves(judge, payload, finish):
    judge.return_value = {"choices": [{"finish_reason": finish, "message": {"content": payload}}]}
    assert (await equivalence.plan_equivalence(incoming(), [row()])).equivalent_id is None


@pytest.mark.asyncio
async def test_complete_verdict_requires_each_id_once(judge):
    rows = [row(), row(id=uuid.UUID(int=2))]
    judge.return_value = reply([uuid.UUID(int=1), uuid.UUID(int=1)])
    assert (await equivalence.plan_equivalence(incoming(), rows)).equivalent_id is None
    judge.return_value = reply([uuid.UUID(int=1)])
    assert (await equivalence.plan_equivalence(incoming(), rows)).equivalent_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"user_id": uuid.UUID(int=101)},
        {"local_only": True},
        {"tier": "l0"},
        {"tier": "l2"},
        {"source_type": "dream"},
        {"status": "candidate"},
        {"status": "deleted"},
        {"valid_to": NOW},
        {"category": "fact"},
        {"content": "x" * 2001},
    ],
)
async def test_ineligible_candidates_never_exposed(judge, change):
    assert (await equivalence.plan_equivalence(incoming(), [row(**change)])).equivalent_id is None
    judge.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"local_only": True},
        {"tier": "l0"},
        {"status": "candidate"},
        {"source_type": "dream"},
        {"content": "x" * 2001},
    ],
)
async def test_ineligible_incoming_never_exposed(judge, change):
    await equivalence.plan_equivalence(incoming(**change), [row()])
    judge.assert_not_awaited()


@pytest.mark.asyncio
async def test_candidate_bound_and_exclusion_and_oversized_payload(judge):
    candidates = [row(id=uuid.UUID(int=i)) for i in range(1, 12)]
    judge.return_value = reply([uuid.UUID(int=i) for i in range(2, 8)])
    plan = await equivalence.plan_equivalence(
        incoming(), candidates, excluded_memory_ids={uuid.UUID(int=1)}
    )
    assert len(plan.candidates) == 6
    assert plan.equivalent_id == uuid.UUID(int=2)
    assert uuid.UUID(int=1) not in {c.memory_id for c in plan.candidates}
    judge.reset_mock()
    await equivalence.plan_equivalence(
        incoming(content="\U0001f355" * 2000), [row(content="\U0001f355" * 2000)]
    )
    judge.assert_not_awaited()  # JSON escaping exceeds payload bound; no truncation.


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["budget_exceeded", "route_unavailable", "capacity_unavailable"])
async def test_judge_denial_preserves(judge, code):
    judge.side_effect = ComputeUnavailable(code, "no private detail")
    assert (await equivalence.plan_equivalence(incoming(), [row()])).equivalent_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ComputeUnavailable("settlement_failed", "x"),
        ComputeUnavailable("settlement_conflict", "x"),
        ComputeUnavailable("accounting_fault", "x"),
        RuntimeError("accounting fault"),
        asyncio.CancelledError(),
    ],
)
async def test_accounting_unknown_and_cancellation_propagate(judge, error):
    judge.side_effect = error
    with pytest.raises(type(error)):
        await equivalence.plan_equivalence(incoming(), [row()])


@pytest.mark.asyncio
async def test_no_scope_or_wrong_owner_never_dispatch(judge, monkeypatch):
    monkeypatch.setattr(
        equivalence,
        "current_scope",
        MagicMock(side_effect=ComputeUnavailable("account_unavailable", "x")),
    )
    assert (await equivalence.plan_equivalence(incoming(), [row()])).equivalent_id is None
    monkeypatch.setattr(
        equivalence, "current_scope", lambda: SimpleNamespace(user_id=uuid.UUID(int=101))
    )
    assert (await equivalence.plan_equivalence(incoming(), [row()])).equivalent_id is None
    judge.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["user_created", "manual"])
async def test_explicit_incoming_does_not_suppress_origin(judge, source):
    await equivalence.plan_equivalence(incoming(source_type=source), [row(source_type="extracted")])
    judge.assert_not_awaited()


class _Acquire:
    def __init__(self, pool):
        self.pool = pool
        self.conn = None

    async def get(self):
        await self.pool.slot.acquire()
        self.pool.acquires += 1
        self.conn = _Connection(self.pool)
        return self.conn

    def __await__(self):
        return self.get().__await__()

    async def __aenter__(self):
        return await self.get()

    async def __aexit__(self, *_):
        await self.pool.release(self.conn)


class _Pool:
    """One-slot offline SQL driver, with observable connection/transaction use."""

    def __init__(self, rows=()):
        self.rows = {r["id"]: copy.deepcopy(r) for r in rows}
        self.slot = asyncio.Semaphore(1)
        self.acquires = 0
        self.events = []
        self.fail_insert = None
        self.conversation = None
        self.fail_conversation = None

    def acquire(self):
        return _Acquire(self)

    async def release(self, conn):
        assert not conn.in_transaction
        self.events.append("release")
        self.slot.release()

    async def fetchrow(self, sql, *args):
        async with self.acquire() as conn:
            conn.autocommit = True
            return await conn.fetchrow(sql, *args)

    async def fetch(self, sql, *args):
        assert "LIMIT 6" in sql
        assert "status = 'active' AND tier = 'l1' AND valid_to IS NULL" in sql
        assert "local_only = FALSE AND source_type != 'dream'" in sql
        async with self.acquire():
            self.events.append("discovery")
            owner, category, _, _, _, _, excluded = args
            return [
                copy.deepcopy(r)
                for r in self.rows.values()
                if r["user_id"] == owner
                and r["category"] == category
                and r["status"] == "active"
                and r["tier"] == "l1"
                and not r["local_only"]
                and r["valid_to"] is None
                and r["source_type"] != "dream"
                and r["id"] not in excluded
            ][:6]


class _Connection:
    def __init__(self, pool):
        self.pool = pool
        self.in_transaction = False
        self.before = None
        self.autocommit = False

    @asynccontextmanager
    async def transaction(self):
        await self.execute("BEGIN")
        try:
            yield
        except BaseException:
            await self.execute("ROLLBACK")
            raise
        else:
            await self.execute("COMMIT")

    async def execute(self, sql, *args):
        if sql == "BEGIN":
            assert not self.in_transaction
            self.before = copy.deepcopy(self.pool.rows)
            self.in_transaction = True
        elif sql in {"COMMIT", "ROLLBACK"}:
            assert self.in_transaction
            if sql == "ROLLBACK":
                self.pool.rows = self.before
            self.in_transaction = False
        elif "pg_advisory_xact_lock" in sql:
            assert self.in_transaction
        elif "last_accessed_at" in sql:
            assert self.in_transaction
            self.pool.events.append(("touch", args[0]))
        elif "SET valid_to" in sql:
            assert self.in_transaction
            target = self.pool.rows.get(args[0])
            if (
                target is None
                or target["user_id"] != args[1]
                or target["valid_to"] is not None
                or target["status"] != "active"
            ):
                return "UPDATE 0"
            target["valid_to"] = NOW
            target["updated_at"] = NOW
            self.pool.events.append(("close", args[0]))
            return "UPDATE 1"
        else:
            raise AssertionError(sql)
        self.pool.events.append(sql if sql in {"BEGIN", "COMMIT", "ROLLBACK"} else "lock")
        return "SELECT 1"

    async def fetchrow(self, sql, *args):
        if "FROM conversations c" in sql:
            self.pool.events.append(("conversation", self.in_transaction))
            if self.pool.fail_conversation is not None:
                raise self.pool.fail_conversation
            return copy.deepcopy(self.pool.conversation)
        if "COUNT(*)" in sql:
            return {
                "count": sum(
                    r["user_id"] == args[0] and r["status"] == "active" and r["valid_to"] is None
                    for r in self.pool.rows.values()
                )
            }
        if "FOR UPDATE" in sql:
            assert self.in_transaction
            self.pool.events.append(("rowlock", args[1]))
            value = self.pool.rows.get(args[1])
            return copy.deepcopy(value) if value and value["user_id"] == args[0] else None
        if "INSERT INTO memories" in sql:
            assert self.in_transaction or self.autocommit
            assert "ON CONFLICT DO NOTHING" in sql
            if self.pool.fail_insert is not None:
                raise self.pool.fail_insert
            (
                owner,
                content,
                content_hash,
                _,
                _,
                category,
                source,
                conv,
                local,
                confidence,
                status,
                slot,
                metadata,
                _,
            ) = args
            conflict = any(
                r["user_id"] == owner
                and r.get("content_hash") == content_hash
                and r["local_only"] == local
                and r["status"] == "active"
                and r["valid_to"] is None
                for r in self.pool.rows.values()
            )
            if conflict:
                self.pool.events.append("conflict")
                return None
            value = row(
                id=uuid.uuid4(),
                user_id=owner,
                content=content,
                content_hash=content_hash,
                category=category,
                source_type=source,
                source_conversation_id=conv,
                local_only=local,
                confidence=confidence,
                status=status,
                memory_slot=slot,
                metadata=json.loads(metadata),
            )
            self.pool.rows[value["id"]] = value
            self.pool.events.append("insert")
            return copy.deepcopy(value)
        if "content_hash = $2" in sql:
            return next(
                (
                    copy.deepcopy(r)
                    for r in self.pool.rows.values()
                    if r["user_id"] == args[0]
                    and r.get("content_hash") == args[1]
                    and r["local_only"] == args[2]
                    and r["status"] == "active"
                    and r["valid_to"] is None
                ),
                None,
            )
        if "WHERE id = $1" in sql:
            return copy.deepcopy(self.pool.rows.get(args[0]))
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        assert "RETURNING id" in sql
        return args[0] if await self.execute(sql, *args) == "UPDATE 1" else None


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.memory.store.get_settings",
        lambda: SimpleNamespace(
            daemon_environment="development",
            daemon_auth_pepper="memory-test-pepper-12345678901234567890",
        ),
    )
    enc = MagicMock(spec=ContentEncryption)
    enc.encrypt.side_effect = lambda text: "encrypted:" + text
    enc.decrypt.side_effect = lambda text: text.removeprefix("encrypted:")
    pool = _Pool()
    memory_store = MemoryStore(db_pool=cast(Any, pool), encryption=enc)
    monkeypatch.setattr(dedup, "prepare_memory_embedding", AsyncMock(return_value=None))
    return memory_store


def add_row(store, **kwargs: Any) -> uuid.UUID:
    value = row(**kwargs)
    value["content_hash"] = compute_memory_content_hash(value["content"])
    value["content"] = store._enc.encrypt(value["content"])
    store._pool.rows[value["id"]] = value
    return value["id"]


async def commit(store, fact, plan):
    simple_fact = ExtractedFact(fact.content, fact.category, fact.confidence, fact.slot)
    return await dedup.deduplicate_facts(
        store,
        fact.user_id,
        [simple_fact],
        fact.conversation_id,
        source_type=fact.source_type,
        status=fact.status,
        prepared_embeddings=[None],
        prepared_plans=[plan],
    )


@pytest.mark.asyncio
async def test_pizza_revalidates_same_conn_retains_canonical(store, judge):
    existing = add_row(store)
    fact = incoming()
    plan = await dedup.prepare_memory_plan(store, fact)
    assert not store._pool.slot.locked()  # Discovery connection released before judge.
    result = await asyncio.wait_for(commit(store, fact, plan), 1)
    assert result.merged[0]["id"] == existing and result.new == []
    assert result.merged[0]["content"] == PIZZA
    assert result.merged[0]["source_type"] == "user_created"
    assert result.merged[0]["memory_slot"] == "food.favourite"
    assert len(store._pool.rows) == 1
    assert ("rowlock", existing) in store._pool.events
    assert ("touch", existing) in store._pool.events
    assert store._pool.events[-2:] == ["COMMIT", "release"]
    assert judge.await_count == 1


@pytest.mark.asyncio
async def test_two_conversation_pizza_paraphrases_reuse_explicit_null_vector(store, judge):
    existing = add_row(store)
    for conversation in (uuid.UUID(int=7), uuid.UUID(int=8)):
        fact = incoming(conversation_id=conversation)
        result = await commit(store, fact, await dedup.prepare_memory_plan(store, fact))
        assert result.merged[0]["id"] == existing and result.new == []
    assert len(store._pool.rows) == 1
    assert store._pool.rows[existing]["source_type"] == "user_created"
    assert store._pool.rows[existing]["source_conversation_id"] is None
    assert judge.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"content": "edited without changing timestamp"},
        {"updated_at": None},
        {"status": "deleted"},
        {"valid_to": NOW},
        {"tier": "l0"},
        {"local_only": True},
        {"category": "fact"},
        {"source_type": "dream"},
        {"memory_slot": "food.other"},
        {"source_conversation_id": uuid.UUID(int=19)},
        {"metadata": {"scope": "different"}},
        {"confidence": 0.1},
        {"valid_from": None},
        {"user_id": uuid.UUID(int=101)},
    ],
)
async def test_stale_state_preserves_without_touch_rejudge_or_close(store, judge, change):
    existing = add_row(store)
    fact = incoming()
    plan = await dedup.prepare_memory_plan(store, fact)
    store._pool.rows[existing].update(change)
    result = await commit(store, fact, plan)
    assert len(result.new) == 1 and result.merged == [] and result.superseded == []
    assert not any(isinstance(e, tuple) and e[0] in {"touch", "close"} for e in store._pool.events)
    assert store._pool.events.index("COMMIT") < store._pool.events.index("insert")
    assert judge.await_count == 1


@pytest.mark.asyncio
async def test_deleted_candidate_and_commit_exclusion_insert(store, judge):
    existing = add_row(store)
    fact = incoming()
    plan = await dedup.prepare_memory_plan(store, fact)
    del store._pool.rows[existing]
    assert len((await commit(store, fact, plan)).new) == 1
    add_row(store)
    async with store._pool.acquire() as conn:
        async with conn.transaction():
            result = await dedup._commit_memory_plan(
                store, plan, conn=conn, embedding_result=None, excluded_memory_ids={existing}
            )
    assert len(result.merged) == 1  # Exact conflict reuse, not semantic touch.
    assert ("touch", existing) not in store._pool.events


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["distinct", "correction", "uncertain"])
async def test_same_current_slot_non_equivalent_never_closes_or_threshold_merges(
    store, judge, verdict
):
    existing = add_row(store, memory_slot="food.current")
    fact = incoming(slot="food.current")
    judge.return_value = reply([existing], verdict)
    plan = await dedup.prepare_memory_plan(store, fact)
    result = await commit(store, fact, plan)
    assert len(result.new) == 1 and result.superseded == [] and result.merged == []
    assert store._pool.rows[existing]["valid_to"] is None


@pytest.mark.asyncio
async def test_exact_whitespace_batch_and_repeat_report_reused_not_new(store, judge):
    judge.side_effect = ComputeUnavailable("budget_exceeded", "x")
    facts = [
        ExtractedFact("User likes pizza", "preference", 0.8, "food.pizza"),
        ExtractedFact("  User   likes pizza  ", "preference", 0.8, "food.other"),
    ]
    result = await dedup.deduplicate_facts(store, USER, facts, None)
    assert len(result.new) == len(result.merged) == 1
    assert len(store._pool.rows) == 1
    repeat = await dedup.deduplicate_facts(store, USER, facts[:1], None)
    assert len(repeat.merged) == 1 and repeat.new == []
    assert store._pool.events.count("conflict") == 2


@pytest.mark.asyncio
async def test_sequential_batch_plans_see_prior_commit(store, judge):
    async def verdict(**kwargs):
        assert not store._pool.slot.locked(), "network call held a DB connection"
        payload = json.loads(kwargs["messages"][1]["content"])
        return reply([c["candidate_id"] for c in payload["candidates"]])

    judge.side_effect = verdict
    facts = [
        ExtractedFact(PIZZA, "preference", 0.8, "food.favourite"),
        ExtractedFact(incoming().content, "preference", 0.8, "food.favorite_pizza"),
    ]
    result = await dedup.deduplicate_facts(store, USER, facts, None)
    assert len(result.new) == len(result.merged) == 1
    assert len(store._pool.rows) == 1
    assert judge.await_count == 1


@pytest.mark.asyncio
async def test_concurrent_empty_plans_retain_both_paraphrases(store, judge):
    first, second = incoming(content=PIZZA), incoming()
    plans = await asyncio.gather(
        dedup.prepare_memory_plan(store, first), dedup.prepare_memory_plan(store, second)
    )
    assert all(plan.equivalent_id is None for plan in plans)
    results = await asyncio.gather(commit(store, first, plans[0]), commit(store, second, plans[1]))
    assert all(len(result.new) == 1 for result in results)
    assert len(store._pool.rows) == 2  # No claim of global semantic exactly-once.
    assert "BEGIN" not in store._pool.events  # Standalone inserts need no explicit tx.
    judge.assert_not_awaited()


@pytest.mark.asyncio
async def test_locked_unplanned_no_pool_reacquire_or_network(store, judge, monkeypatch):
    embed = AsyncMock(side_effect=AssertionError("embedding under lock"))
    monkeypatch.setattr(dedup, "prepare_memory_embedding", embed)
    async with store._pool.acquire() as conn:
        async with conn.transaction():
            result = await asyncio.wait_for(
                dedup.deduplicate_facts(
                    store,
                    USER,
                    [ExtractedFact(PIZZA, "preference", 0.8)],
                    None,
                    lock_conn=conn,
                ),
                1,
            )
    assert len(result.new) == 1 and store._pool.acquires == 1
    judge.assert_not_awaited()
    embed.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_equivalent_preserves_origin_by_insert(store, judge):
    add_row(store, source_type="extracted")
    fact = incoming(source_type="user_created")
    result = await commit(store, fact, await dedup.prepare_memory_plan(store, fact))
    assert result.new[0]["source_type"] == "user_created" and len(store._pool.rows) == 2
    judge.assert_not_awaited()


@pytest.mark.asyncio
async def test_commit_also_guards_explicit_provenance(store):
    existing = add_row(store, source_type="extracted")
    fact = incoming(source_type="user_created")
    snapshot = equivalence.CandidateSnapshot.capture(row(source_type="extracted"))
    plan = equivalence.EquivalencePlan(fact, (snapshot,), existing)
    result = await commit(store, fact, plan)
    assert result.merged == [] and result.new[0]["source_type"] == "user_created"
    assert store._pool.rows[existing]["source_type"] == "extracted"


@pytest.fixture
def tool_offline(store, monkeypatch):
    monkeypatch.setattr(tools, "prepare_memory_embedding", AsyncMock(return_value=None))
    monkeypatch.setattr(tools.MemoryWriteTool, "_check_and_set_contradiction", AsyncMock())
    monkeypatch.setattr(
        tools.MemoryWriteTool,
        "_check_write_quota",
        AsyncMock(return_value=tools._WriteQuotaDecision(None, None)),
    )
    return tools.MemoryWriteTool(store, USER)


@pytest.mark.asyncio
async def test_tool_create_plan_before_lock_one_slot(store, judge, tool_offline):
    add_row(store, source_type="user_created")

    async def verdict(**kwargs):
        assert not store._pool.slot.locked()
        assert "BEGIN" not in store._pool.events
        return reply([uuid.UUID(int=1)])

    judge.side_effect = verdict
    result = await asyncio.wait_for(
        tool_offline.execute(
            action="create",
            content=incoming().content,
            category="preference",
            slot="food.favorite_pizza",
        ),
        1,
    )
    assert "Memory created" in result and len(store._pool.rows) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["cloud", "local", "missing", "wrong-owner", "failure", "cancel", "insert-failure"]
)
async def test_update_source_guard_uses_held_slot_and_rolls_back(
    store, judge, tool_offline, monkeypatch, outcome
):
    from orchestrator.memory import embedding

    monkeypatch.setattr(embedding, "get_selected_embedding_route_id", lambda: "selected")
    cid = uuid.UUID(int=7)
    target = add_row(store, source_type="extracted", source_conversation_id=cid)
    pool = store._pool
    before = copy.deepcopy(pool.rows)
    pool.conversation = {
        "id": cid,
        "user_id": USER,
        "pipeline": "cloud",
        "actual_message_count": 0,
        "effective_last_activity_at": NOW,
    }

    async def prepare_after_preflight(*args):
        assert not pool.slot.locked()
        assert ("conversation", False) in pool.events
        if outcome == "local":
            pool.conversation["pipeline"] = "local"
        elif outcome == "wrong-owner":
            pool.conversation["user_id"] = uuid.UUID(int=101)
        elif outcome == "missing":
            pool.conversation = None
        elif outcome in ("failure", "cancel"):
            pool.fail_conversation = (
                asyncio.CancelledError()
                if outcome == "cancel"
                else RuntimeError("synthetic read failure")
            )
        elif outcome == "insert-failure":
            pool.fail_insert = RuntimeError("synthetic insert failure")
        return None

    monkeypatch.setattr(
        tools, "prepare_memory_embedding", AsyncMock(side_effect=prepare_after_preflight)
    )

    async def update():
        return await asyncio.wait_for(
            tool_offline.execute(
                action="update", memory_id=str(target), content="Replacement pizza fact"
            ),
            1,
        )

    if outcome == "cloud":
        assert "Memory updated" in await update()
        assert pool.rows[target]["valid_to"] is not None
        assert len(pool.rows) == 2
    else:
        error = (
            asyncio.CancelledError
            if outcome == "cancel"
            else RuntimeError
            if outcome in ("failure", "insert-failure")
            else embedding.EmbeddingConfigurationError
        )
        with pytest.raises(error):
            await update()
        assert pool.rows == before
        assert "ROLLBACK" in pool.events
    assert ("conversation", True) in pool.events
    assert pool.events.count(("conversation", False)) == 1
    assert ("close", target) in pool.events
    assert pool.events.index(("close", target)) < pool.events.index(("conversation", True))
    start = pool.events.index("BEGIN")
    end = pool.events.index("COMMIT" if outcome == "cloud" else "ROLLBACK", start)
    assert "release" not in pool.events[start:end]
    assert not pool.slot.locked()
    judge.assert_not_awaited()
    # The same owner can acquire/finish another transaction after success or failure.
    conn, _ = await asyncio.wait_for(store.acquire_user_cap_lock(USER), 1)
    await conn.execute("ROLLBACK")
    await pool.release(conn)
    assert not pool.slot.locked()


@pytest.mark.asyncio
async def test_unlocked_conversation_lookup_preserves_pool_and_listing_metadata(store):
    cid = uuid.UUID(int=7)
    store._pool.conversation = {
        "id": cid,
        "user_id": USER,
        "pipeline": "cloud",
        "actual_message_count": 3,
        "effective_last_activity_at": NOW,
    }
    result = await store.get_conversation(cid)
    assert result is not None and result["message_count"] == 3
    assert ("conversation", False) in store._pool.events
    assert not store._pool.slot.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_state", ["local", "missing", "wrong-owner", "unknown", "read-error", "cancel"]
)
async def test_update_invalid_source_preflight_never_prepares_or_locks(
    store, judge, tool_offline, monkeypatch, source_state
):
    from orchestrator.memory import embedding

    monkeypatch.setattr(embedding, "get_selected_embedding_route_id", lambda: "selected")
    cid = uuid.UUID(int=7)
    target = add_row(store, source_type="extracted", source_conversation_id=cid)
    pool = store._pool
    before = copy.deepcopy(pool.rows)
    pool.conversation = {
        "id": cid,
        "user_id": USER,
        "pipeline": "local",
        "actual_message_count": 0,
        "effective_last_activity_at": NOW,
    }
    if source_state == "missing":
        pool.conversation = None
    elif source_state == "wrong-owner":
        pool.conversation.update(user_id=uuid.UUID(int=101), pipeline="cloud")
    elif source_state == "unknown":
        pool.conversation["pipeline"] = None
    elif source_state in ("read-error", "cancel"):
        pool.fail_conversation = (
            RuntimeError("synthetic source read")
            if source_state == "read-error"
            else asyncio.CancelledError()
        )
    planner = AsyncMock()
    monkeypatch.setattr(tools, "prepare_memory_plan", planner)
    call = tool_offline.execute(
        action="update",
        memory_id=str(target),
        content="Replacement",
        pipeline="cloud",
        source_conversation_id=None,
    )
    if source_state in ("read-error", "cancel"):
        with pytest.raises(
            RuntimeError if source_state == "read-error" else asyncio.CancelledError
        ):
            await asyncio.wait_for(call, 1)
    else:
        assert (
            await asyncio.wait_for(call, 1) == "Memory source is unavailable for this cloud update."
        )
    cast(AsyncMock, tools.prepare_memory_embedding).assert_not_awaited()
    planner.assert_not_awaited()
    judge.assert_not_awaited()
    tool_offline._check_write_quota.assert_not_awaited()
    assert pool.rows == before
    assert "BEGIN" not in pool.events and "insert" not in pool.events
    assert ("close", target) not in pool.events
    assert not pool.slot.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("selected,source", [("", uuid.UUID(int=7)), ("selected", None)])
async def test_update_skips_source_lookup_without_selected_route_or_source(
    store, judge, tool_offline, monkeypatch, selected, source
):
    from orchestrator.memory import embedding

    monkeypatch.setattr(embedding, "get_selected_embedding_route_id", lambda: selected)
    target = add_row(store, source_type="extracted", source_conversation_id=source)
    store._pool.fail_conversation = AssertionError("Unexpected source lookup")
    result = await asyncio.wait_for(
        tool_offline.execute(
            action="update", memory_id=str(target), content="Replacement pizza fact"
        ),
        1,
    )
    assert "Memory updated" in result
    assert not any(
        isinstance(event, tuple) and event[0] == "conversation" for event in store._pool.events
    )
    assert not store._pool.slot.locked()


@pytest.mark.asyncio
async def test_update_concurrent_admin_edit_refuses_without_close(store, judge, tool_offline):
    target = add_row(store, source_type="user_created")
    original_prepare = tools.prepare_memory_plan

    async def edit_after_plan(*args, **kwargs):
        plan = await original_prepare(*args, **kwargs)
        store._pool.rows[target]["content"] = "encrypted:Admin changed content"
        return plan

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(tools, "prepare_memory_plan", edit_after_plan)
        result = await tool_offline.execute(
            action="update", memory_id=str(target), content="Replacement pizza fact"
        )
    assert "Retry the update" in result
    assert store._pool.rows[target]["content"] == "encrypted:Admin changed content"
    assert store._pool.rows[target]["valid_to"] is None
    assert "ROLLBACK" in store._pool.events
    assert not any(isinstance(e, tuple) and e[0] == "close" for e in store._pool.events)


@pytest.mark.asyncio
@pytest.mark.parametrize("locality", [True, None, "true", 0, "missing"])
@pytest.mark.parametrize("explicit", [False, True])
async def test_locality_update_refused_before_external_or_write_work(
    store, judge, tool_offline, monkeypatch, locality, explicit
):
    target = add_row(store, local_only=locality)
    if locality == "missing":
        del store._pool.rows[target]["local_only"]
    original = copy.deepcopy(store._pool.rows)
    planner = AsyncMock()
    monkeypatch.setattr(tools, "prepare_memory_plan", planner)
    args = {"action": "update", "memory_id": str(target)}
    if explicit:
        args["content"] = "Explicit replacement is not permission to clear locality"
    result = await tool_offline.execute(**args)
    assert result == "Local-only memories cannot be updated through this cloud tool."
    tool_offline._check_write_quota.assert_not_awaited()
    cast(AsyncMock, tools.prepare_memory_embedding).assert_not_awaited()
    planner.assert_not_awaited()
    judge.assert_not_awaited()
    assert store._pool.rows == original
    assert "BEGIN" not in store._pool.events
    assert "insert" not in store._pool.events


@pytest.mark.asyncio
async def test_other_owner_locality_not_disclosed(store, judge, tool_offline, monkeypatch):
    target = add_row(store, user_id=uuid.UUID(int=101), local_only=True)
    original = copy.deepcopy(store._pool.rows)
    planner = AsyncMock()
    monkeypatch.setattr(tools, "prepare_memory_plan", planner)
    result = await tool_offline.execute(action="update", memory_id=str(target), content="New")
    assert result == "Memory not found"
    tool_offline._check_write_quota.assert_not_awaited()
    cast(AsyncMock, tools.prepare_memory_embedding).assert_not_awaited()
    planner.assert_not_awaited()
    judge.assert_not_awaited()
    assert store._pool.rows == original


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("insert failed"), asyncio.CancelledError()])
async def test_update_close_insert_failure_rolls_back(store, judge, tool_offline, failure):
    target = add_row(store)
    store._pool.fail_insert = failure
    with pytest.raises(type(failure)):
        await tool_offline.execute(
            action="update", memory_id=str(target), content="Replacement pizza fact"
        )
    assert store._pool.rows[target]["valid_to"] is None
    assert len(store._pool.rows) == 1 and not store._pool.slot.locked()
    assert "ROLLBACK" in store._pool.events


@pytest.mark.asyncio
async def test_update_excludes_target_and_locks_merge_in_uuid_order(store, judge, tool_offline):
    target = add_row(store, id=uuid.UUID(int=5))
    merge = add_row(
        store,
        id=uuid.UUID(int=1),
        content="User prefers pepperoni pizza",
        source_type="user_created",
    )
    result = await tool_offline.execute(
        action="update",
        memory_id=str(target),
        content=incoming().content,
        category="preference",
        slot="food.favorite_pizza",
    )
    assert "Memory updated" in result
    payload = json.loads(judge.await_args.kwargs["messages"][1]["content"])
    assert [c["candidate_id"] for c in payload["candidates"]] == [str(merge)]
    assert [e[1] for e in store._pool.events if isinstance(e, tuple) and e[0] == "rowlock"] == [
        merge,
        target,
    ]
    assert store._pool.rows[target]["valid_to"] is not None
    assert store._pool.rows[merge]["valid_to"] is None
    assert len(store._pool.rows) == 2 and "insert" not in store._pool.events
