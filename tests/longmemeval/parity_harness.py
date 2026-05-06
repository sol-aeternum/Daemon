from __future__ import annotations

import asyncpg
import uuid
from typing import Any

from orchestrator.memory.embedding import embed_query
from orchestrator.memory.injection import (
    assemble_system_prompt,
    build_memory_context,
)
from orchestrator.memory.retrieval import retrieve_memories_for_text
from orchestrator.memory.store import MemoryStore

from tests.longmemeval.ingest import (
    SYNTHETIC_USER_NAMESPACE,
    create_synthetic_user,
    ingest_session,
)
from tests.longmemeval.evaluate import (
    answer_with_llm,
    judge_answer,
)

TOP_K_MEMORIES = 5
MAX_TOKENS = 2500


async def create_answer_conversation(
    store: MemoryStore,
    user_id: uuid.UUID,
    question_text: str,
    question_id: str,
) -> uuid.UUID:
    """Create an answer/evaluation conversation owned by the synthetic user.

    The conversation contains the question as a user message, so that
    build_memory_context(store, answer_conversation_id, ...) can extract
    the query_text from recent messages and perform retrieval against
    the haystack memories already ingested under this synthetic user.
    """
    conversation = await store.create_conversation(
        user_id=user_id,
        pipeline="cloud",
        title=f"LongMemEval Answer: {question_id[:16]}",
    )
    answer_conversation_id = conversation["id"]

    await store.insert_message(
        conversation_id=answer_conversation_id,
        user_id=user_id,
        role="user",
        content=question_text,
        status="complete",
        metadata={
            "source": "longmemeval_parity",
            "question_id": question_id,
        },
    )

    return answer_conversation_id


async def parity_evaluate_single(
    store: MemoryStore,
    pool: asyncpg.Pool,
    question_id: str,
    question_text: str,
    reference: str,
    category: str,
    haystack_sessions: list[list[dict[str, Any]]],
    haystack_session_ids: list[str],
    benchmark_mode: bool = False,
    log_retrieval: bool = False,
) -> dict[str, Any]:
    synthetic_user_id = uuid.uuid5(SYNTHETIC_USER_NAMESPACE, question_id)

    await create_synthetic_user(pool, question_id)

    ingested_conversation_ids: list[str] = []
    total_messages_ingested = 0
    extraction_outcomes: list[str] = []

    for sess_idx, session_messages in enumerate(haystack_sessions):
        session_id = (
            haystack_session_ids[sess_idx]
            if sess_idx < len(haystack_session_ids)
            else f"{question_id}_session_{sess_idx}"
        )

        result = await ingest_session(
            store=store,
            pool=pool,
            user_id=synthetic_user_id,
            session_id=session_id,
            messages=session_messages,
            session_index=sess_idx,
        )
        ingested_conversation_ids.append(result["conversation_id"])
        total_messages_ingested += result.get("message_count", 0)
        extraction_outcomes.append(result.get("outcome", "unknown"))

    answer_conversation_id = await create_answer_conversation(
        store,
        synthetic_user_id,
        question_text,
        question_id,
    )

    query_embedding = await embed_query(question_text)

    memories: list[dict[str, Any]] = []
    memories = await retrieve_memories_for_text(
        store=store,
        query_text=question_text,
        user_id=synthetic_user_id,
        query_embedding=query_embedding,
        limit=TOP_K_MEMORIES,
        include_l0=True,
        log_retrieval=log_retrieval,
        retrieval_triggered_by="longmemeval_parity",
        include_dream_observations=True,
    )

    memory_context = await build_memory_context(
        store,
        answer_conversation_id,
        max_tokens=MAX_TOKENS,
    )

    system_prompt = await assemble_system_prompt(
        memory_context=memory_context,
        conversation_id=answer_conversation_id,
    )

    hypothesis = await answer_with_llm(
        question_text,
        memories,
    )

    judgment = await judge_answer(question_text, hypothesis, reference)

    retrieved_memory_ids = [str(m.get("id")) for m in memories if m.get("id")]

    result: dict[str, Any] = {
        "question_id": question_id,
        "synthetic_user_id": str(synthetic_user_id),
        "answer_conversation_id": str(answer_conversation_id),
        "question": question_text,
        "reference": reference,
        "category": category,
        "hypothesis": hypothesis,
        "judgment": judgment,
        "memories_used": len(memories),
        "retrieved_memory_ids": retrieved_memory_ids,
        "haystack_conversation_ids": ingested_conversation_ids,
        "total_messages_ingested": total_messages_ingested,
        "extraction_outcomes": extraction_outcomes,
        "memory_context": memory_context,
        "system_prompt": system_prompt,
    }

    return result
