from __future__ import annotations

import json
import logging
import uuid

logger = logging.getLogger(__name__)
from dataclasses import dataclass
from typing import Any

import litellm

from orchestrator.memory.store import MemoryStore
from orchestrator.memory.embedding import embed_text


@dataclass
class ExtractedFact:
    content: str
    category: str  # 'fact', 'preference', 'project', 'summary'
    confidence: float


EXTRACTION_PROMPT = """
You are extracting durable memory items from a conversation excerpt.

Output JSON object with two fields:
- facts: array of memory objects
- open_threads: array of unresolved goals/questions (optional)

Each memory object:
- content: string, <= 280 chars, atomic and self-contained
- category: fact | preference | project | correction | summary
- confidence: 0.0-1.0
- supersedes: optional string that this memory corrects

Guidelines:
- Prefer precise, user-specific facts over generic statements
- "I like X" → preference (high confidence)
- "I need to do X" → project (task)
- Corrections ("actually", "not X, Y") → category=correction and include supersedes
- Open questions or pending tasks → open_threads
- Skip ephemeral info (time, weather) unless it encodes a preference or plan
- Confidence: 0.9+ explicit, 0.7-0.8 inferred, <0.7 only if likely durable

Examples:
Input: "Actually I don't use Slack anymore, use Teams." →
facts: [{"content": "User prefers Microsoft Teams over Slack", "category": "correction", "confidence": 0.92, "supersedes": "User uses Slack"}]

Input: "I'm moving to Berlin next month and I love minimalist desks." →
facts: [
  {"content": "User is moving to Berlin next month", "category": "fact", "confidence": 0.88},
  {"content": "User prefers minimalist desks", "category": "preference", "confidence": 0.92}
]

Existing summary (if any):
{summary}

Conversation excerpt:
{text}
"""


async def extract_facts_from_text(
    text: str,
    model: str = "openrouter/openai/gpt-4o-mini",
    *,
    summary: str | None = None,
) -> list[ExtractedFact]:
    """Extract facts from text using GPT-4o-mini."""
    try:
        response = await litellm.acompletion(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You extract structured facts from text.",
                },
                {
                    "role": "user",
                    "content": EXTRACTION_PROMPT.format(
                        summary=(summary or "None"),
                        text=text[:4000],
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )

        response_data: Any = response
        if hasattr(response, "model_dump"):
            response_data = response.model_dump()
        elif hasattr(response, "dict"):
            response_data = response.dict()

        content = None
        if isinstance(response_data, dict):
            choices = response_data.get("choices")
            if isinstance(choices, list) and choices:
                message = (
                    choices[0].get("message") if isinstance(choices[0], dict) else None
                )
                if isinstance(message, dict):
                    content = message.get("content")

        if not isinstance(content, str) or not content:
            return []

        data = json.loads(content)

        facts = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict) and "facts" in data:
            items = data["facts"]
        else:
            items = [data] if data else []

        for item in items:
            if isinstance(item, dict) and "content" in item:
                facts.append(
                    ExtractedFact(
                        content=item["content"],
                        category=item.get("category", "fact"),
                        confidence=item.get("confidence", 0.8),
                    )
                )

        return facts
    except Exception as e:
        logger.error("Extraction error", exc_info=True)
        return []


async def process_extraction(
    store: MemoryStore, user_id: uuid.UUID, conversation_id: uuid.UUID, text: str
) -> None:
    """Orchestrate extraction → dedup → insert."""
    from orchestrator.memory.dedup import deduplicate_facts

    lock_key = conversation_id.int % (2**63)
    async with store._pool.acquire() as conn:
        locked = await conn.fetchval("SELECT pg_try_advisory_lock($1)", lock_key)
        if not locked:
            return

        try:
            conversation = await store.get_conversation(conversation_id)
            summary = None
            if conversation:
                summary = conversation.get("summary")

            model = "openrouter/openai/gpt-4o-mini"
            facts = await extract_facts_from_text(text, model=model, summary=summary)
            if not facts:
                return

            result = await deduplicate_facts(
                store,
                user_id,
                facts,
                conversation_id,
                status="active",
            )

            await store.log_extraction(
                user_id=user_id,
                conversation_id=conversation_id,
                input_snippet=text[:1000],
                extracted_facts=[
                    {
                        "content": f.content,
                        "category": f.category,
                        "confidence": f.confidence,
                    }
                    for f in facts
                ],
                dedup_results={
                    "merged": len(result.merged),
                    "superseded": len(result.superseded),
                    "new": len(result.new),
                },
                model_used=model,
            )
        finally:
            await conn.fetchval("SELECT pg_advisory_unlock($1)", lock_key)
