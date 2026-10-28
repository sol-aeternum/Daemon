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
