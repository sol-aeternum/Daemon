from __future__ import annotations

import json
import uuid
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
Extract facts, preferences, and tasks from this conversation excerpt.

Categories: fact | preference | project | summary

Return JSON array: [{"content": "...", "category": "...", "confidence": 0.0-1.0}]

Guidelines:
- Extract atomic, self-contained facts
- "I like X" → preference with high confidence
- "I need to do X" → task → project category
- Open questions → project category (thread)
- Skip ephemeral info (time, weather)
- Confidence: 0.9+ for explicit statements, 0.7-0.8 for inferred

Conversation excerpt:
{text}
"""


async def extract_facts_from_text(
    text: str, model: str = "gpt-4o-mini"
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
                {"role": "user", "content": EXTRACTION_PROMPT.format(text=text[:4000])},
            ],
            temperature=0.1,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
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
        print(f"Extraction error: {e}")
        return []


async def process_extraction(
    store: MemoryStore, user_id: uuid.UUID, conversation_id: uuid.UUID, text: str
) -> None:
    """Orchestrate extraction → dedup → insert."""
    from orchestrator.memory.dedup import deduplicate_facts

    # Extract facts
    facts = await extract_facts_from_text(text)
    if not facts:
        return

    # Run deduplication
    result = await deduplicate_facts(store, user_id, facts, conversation_id)

    # Log extraction
    await store.log_extraction(
        user_id=user_id,
        conversation_id=conversation_id,
        input_snippet=text[:1000],
        extracted_facts=[
            {"content": f.content, "category": f.category, "confidence": f.confidence}
            for f in facts
        ],
        dedup_results={
            "merged": len(result.merged),
            "superseded": len(result.superseded),
            "new": len(result.new),
        },
        model_used="gpt-4o-mini",
    )
