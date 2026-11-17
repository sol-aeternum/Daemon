"""Real Postgres full-text matching for BM25 lexical search (#580); isolated test DB required."""

import pytest

from orchestrator.memory import revisions
from orchestrator.memory.temporal import TemporalReadScope
from tests.durable_tasks_support import durable_env_fixture
from tests.test_entity_account_guard_postgres import registered_sql

env = durable_env_fixture(initialized=True)

QUESTION = "What book am I currently reading?"
SPACE = "test:space-a:1024"
OTHER = "test:space-b:1024"
VECTOR = [0.1] * 1024


async def _seed(env):
    reading = await env.memory.insert_memory(
        env.alice, "User is currently reading The Seven Husbands of Evelyn Hugo", "fact", "manual"
    )
    await env.memory.insert_memory(env.alice, "User plays guitar on weekends", "fact", "manual")
    await env.memory.insert_memory(
        env.bob, "Bob is currently reading a book about sailing", "fact", "manual"
    )
    return reading


@pytest.mark.asyncio
async def test_every_term_is_required_by_default(env):
    await _seed(env)
    # "book" is not in Alice's fact, so the all-terms query finds nothing.
    assert await env.memory.search_memories_bm25(env.alice, QUESTION) == []


@pytest.mark.asyncio
async def test_any_term_matches_a_whole_question_and_ranks_by_overlap(env):
    reading = await _seed(env)
    rows = await env.memory.search_memories_bm25(env.alice, QUESTION, match_any_term=True)
    # Only Alice's rows; the guitar fact shares no lexeme with the question.
    assert [row["id"] for row in rows] == [reading["id"]]
    assert rows[0]["content"] == "User is currently reading The Seven Husbands of Evelyn Hugo"
    assert rows[0]["bm25_score"] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "the a of", "O'Brien's R&D 100% \\"])
async def test_any_term_handles_stopwords_and_punctuation(env, query):
    await _seed(env)
    rows = await env.memory.search_memories_bm25(
        env.alice, query, match_any_term=True, category="fact"
    )
    assert rows == []


@pytest.mark.asyncio
async def test_any_term_ranks_more_overlapping_rows_first(env):
    # "book", "current" and "read" all match the first row; only "book" matches the second.
    broad = await env.memory.insert_memory(
        env.alice, "User is currently reading a book about sailing", "fact", "manual"
    )
    narrow = await env.memory.insert_memory(env.alice, "User bought a new book", "fact", "manual")
    rows = await env.memory.search_memories_bm25(env.alice, QUESTION, match_any_term=True)
    assert [row["id"] for row in rows] == [broad["id"], narrow["id"]]
    assert rows[0]["bm25_score"] > rows[1]["bm25_score"] > 0


@pytest.mark.asyncio
async def test_any_term_keeps_the_category_filter(env):
    reading = await _seed(env)
    await env.memory.insert_memory(
        env.alice, "User prefers reading paper books", "preference", "manual"
    )
    rows = await env.memory.search_memories_bm25(
        env.alice, QUESTION, match_any_term=True, category="fact"
    )
    assert [row["id"] for row in rows] == [reading["id"]]


async def _hidden_row(env, kind):
    """Insert a matching row that the default search must not return."""
    content = "User is currently reading a hidden book"
    if kind == "local":
        return await env.memory.insert_memory(
            env.alice, content, "fact", "manual", local_only=True
        ), {"include_local": True}
    row = await env.memory.insert_memory(env.alice, content, "fact", "manual")
    if kind == "deleted":
        await registered_sql(env, "UPDATE memories SET status = 'deleted' WHERE id = $1", row["id"])
        return row, None
    if kind == "historical":
        await registered_sql(env, "UPDATE memories SET valid_to = now() WHERE id = $1", row["id"])
        return row, {"include_historical": True}
    raise AssertionError(kind)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["local", "deleted", "historical"])
async def test_any_term_keeps_visibility_filters(env, kind):
    reading = await _seed(env)
    hidden, reveal = await _hidden_row(env, kind)
    rows = await env.memory.search_memories_bm25(env.alice, QUESTION, match_any_term=True)
    assert [row["id"] for row in rows] == [reading["id"]]
    if reveal is not None:
        # The opt-in flag still reveals the row, so the filter is what hid it.
        rows = await env.memory.search_memories_bm25(
            env.alice, QUESTION, match_any_term=True, **reveal
        )
        assert {row["id"] for row in rows} == {reading["id"], hidden["id"]}


@pytest.mark.asyncio
async def test_any_term_keeps_the_source_conversation_filter(env):
    allowed = await env.memory.create_conversation(env.alice)
    other = await env.memory.create_conversation(env.alice)
    kept = await env.memory.insert_memory(
        env.alice,
        "User is currently reading a book about sailing",
        "fact",
        "extracted",
        source_conversation_id=allowed["id"],
    )
    await env.memory.insert_memory(
        env.alice,
        "User is currently reading a book about chess",
        "fact",
        "extracted",
        source_conversation_id=other["id"],
    )
    rows = await env.memory.search_memories_bm25(
        env.alice, QUESTION, match_any_term=True, source_conversation_ids=[allowed["id"]]
    )
    assert [row["id"] for row in rows] == [kept["id"]]


@pytest.mark.asyncio
async def test_term_coverage_keeps_all_terms_rows_and_scores_first(env):
    # Hybrid passes (#580): every-term rows keep the all-terms score and lead;
    # unembedded partial rows follow with the share of query lexemes they contain.
    full = await env.memory.insert_memory(
        env.alice, "User is currently reading a book about sailing", "fact", "manual"
    )
    partial = await env.memory.insert_memory(env.alice, "User bought a new book", "fact", "manual")
    await env.memory.insert_memory(env.alice, "User plays guitar on weekends", "fact", "manual")
    baseline = await env.memory.search_memories_bm25(env.alice, QUESTION)
    rows = await env.memory.search_memories_bm25(env.alice, QUESTION, term_coverage_space=SPACE)
    assert [row["id"] for row in rows] == [full["id"], partial["id"]]
    assert [row["id"] for row in baseline] == [full["id"]]
    assert rows[0]["bm25_score"] == pytest.approx(baseline[0]["bm25_score"])
    assert (rows[0]["bm25_full"], rows[0]["bm25_coverage"]) == (True, 1.0)
    # "book" is one of the question's three lexemes: book, current, read.
    assert rows[1]["bm25_full"] is False
    assert rows[1]["bm25_coverage"] == pytest.approx(1 / 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("category", [None, "fact"])
async def test_default_search_reports_every_row_as_a_full_match(env, category):
    await env.memory.insert_memory(
        env.alice, "User is currently reading a book about sailing", "fact", "manual"
    )
    rows = await env.memory.search_memories_bm25(env.alice, QUESTION, category=category)
    assert [(row["bm25_full"], row["bm25_coverage"]) for row in rows] == [(True, 1.0)]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["local", "deleted", "historical"])
async def test_term_coverage_keeps_visibility_filters(env, kind):
    reading = await _seed(env)
    hidden, reveal = await _hidden_row(env, kind)
    rows = await env.memory.search_memories_bm25(env.alice, QUESTION, term_coverage_space=SPACE)
    assert [row["id"] for row in rows] == [reading["id"]]
    if reveal is not None:
        rows = await env.memory.search_memories_bm25(
            env.alice, QUESTION, term_coverage_space=SPACE, **reveal
        )
        assert {row["id"] for row in rows} == {reading["id"], hidden["id"]}


async def _spaced_rows(env):
    """One all-terms row and one partial ("book") row per reachability case."""
    full = await env.memory.insert_memory(
        env.alice,
        "User is currently reading a book about sailing",
        "fact",
        "manual",
        embedding=VECTOR,
        embedding_model=SPACE,
    )
    in_space = await env.memory.insert_memory(
        env.alice,
        "User bought a new book",
        "fact",
        "manual",
        embedding=VECTOR,
        embedding_model=SPACE,
    )
    other_space = await env.memory.insert_memory(
        env.alice,
        "User lent a book to a neighbour",
        "fact",
        "manual",
        embedding=VECTOR,
        embedding_model=OTHER,
    )
    unembedded = await env.memory.insert_memory(
        env.alice, "User returned a library book", "fact", "manual"
    )
    return full, in_space, other_space, unembedded


@pytest.mark.asyncio
@pytest.mark.parametrize("category", [None, "fact"])
async def test_term_coverage_holds_rows_the_vector_reaches_to_every_term(env, category):
    full, in_space, other_space, unembedded = await _spaced_rows(env)
    rows = await env.memory.search_memories_bm25(
        env.alice,
        QUESTION,
        category=category,
        embedding_models=[SPACE, OTHER],
        term_coverage_space=SPACE,
    )
    ids = [row["id"] for row in rows]
    # A partial row with a vector in the pass's space is left to the vector.
    assert ids[0] == full["id"] and in_space["id"] not in ids
    assert set(ids[1:]) == {other_space["id"], unembedded["id"]}
    assert all(row["bm25_full"] is False for row in rows[1:])
    # Naming the other space flips which embedded partial row is admitted.
    rows = await env.memory.search_memories_bm25(
        env.alice,
        QUESTION,
        category=category,
        embedding_models=[SPACE, OTHER],
        term_coverage_space=OTHER,
    )
    assert {row["id"] for row in rows} == {full["id"], in_space["id"], unembedded["id"]}


@pytest.mark.asyncio
async def test_scoped_term_coverage_holds_rows_the_vector_reaches_to_every_term(env):
    full, in_space, other_space, unembedded = await _spaced_rows(env)
    scope = await revisions.resolve(env.memory, env.alice, TemporalReadScope())
    rows = await revisions.search(
        env.memory, env.alice, scope, query=QUESTION, term_coverage_space=SPACE
    )
    ids = [row["id"] for row in rows]
    assert ids[0] == full["id"] and in_space["id"] not in ids
    assert set(ids[1:]) == {other_space["id"], unembedded["id"]}
    assert [row["bm25_full"] for row in rows] == [True, False, False]
    # Without a space, the scoped search still needs every term.
    rows = await revisions.search(env.memory, env.alice, scope, query=QUESTION)
    assert [row["id"] for row in rows] == [full["id"]]
