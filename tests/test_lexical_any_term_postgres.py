"""Real Postgres full-text matching for BM25 lexical search (#580); isolated test DB required."""

import pytest

from tests.durable_tasks_support import durable_env_fixture

env = durable_env_fixture()

QUESTION = "What book am I currently reading?"


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
        await env.pool.execute("UPDATE memories SET status = 'deleted' WHERE id = $1", row["id"])
        return row, None
    if kind == "historical":
        await env.pool.execute("UPDATE memories SET valid_to = now() WHERE id = $1", row["id"])
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
