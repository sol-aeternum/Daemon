from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Mapping, Sequence

logger = logging.getLogger(__name__)
from dataclasses import dataclass
from typing import Any

import litellm

from orchestrator.memory.store import MemoryStore


MAX_EXTRACTION_INPUT_CHARS = 4000
EXTRACTION_TEMPERATURE = 0.0
EXTRACTION_TOP_P = 1.0
EXTRACTION_MAX_TOKENS = 2000
HEDGE_OVERRIDE_CONFIDENCE = 0.65
STRONG_OVERRIDE_CONFIDENCE = 0.92
CORRECTION_MIN_CONFIDENCE = 0.90
DEFAULT_EXTRACTED_CONFIDENCE = 0.8
ALLOWED_CATEGORIES = {"fact", "preference", "project", "summary", "correction"}
CATEGORY_NORMALIZATION = {
    "intent": "project",
    "goal": "project",
    "plan": "project",
    "todo": "project",
}

HEDGE_WORDS_PATTERN = re.compile(
    r"\b(might|maybe|considering|thinking about|possibly|probably|not sure|not confirmed|unconfirmed|suspects)\b",
    re.IGNORECASE,
)
STRONG_WORDS_PATTERN = re.compile(
    r"\b(definitely|always|never|confirmed|allergic|diagnosed)\b",
    re.IGNORECASE,
)
ASSISTANT_PREFIX_PATTERN = re.compile(r"^assistant\b", re.IGNORECASE)
GENERAL_KNOWLEDGE_PREFIX_PATTERN = re.compile(
    r"^the\s+[A-Z][A-Za-z0-9_\-]*",
    re.IGNORECASE,
)
USER_SUBJECT_PATTERN = re.compile(r"\buser\b|\buser's\b", re.IGNORECASE)
FILLER_PATTERNS = [
    re.compile(r"^user\s+said\s+hello\b", re.IGNORECASE),
    re.compile(r"^user\s+greeted\b", re.IGNORECASE),
    re.compile(r"^user\s+thanked\b", re.IGNORECASE),
]
EPHEMERAL_ACTION_PATTERNS = [
    re.compile(r"\bheading to bed\b", re.IGNORECASE),
    re.compile(r"\bgoing to sleep\b", re.IGNORECASE),
    re.compile(r"\bgoing to bed\b", re.IGNORECASE),
    re.compile(r"\blogging off\b", re.IGNORECASE),
    re.compile(r"\bsigning off\b", re.IGNORECASE),
    re.compile(r"\btalk tomorrow\b", re.IGNORECASE),
    re.compile(r"\btalk later\b", re.IGNORECASE),
    re.compile(r"\bgotta go\b", re.IGNORECASE),
    re.compile(r"\b(?:goodnight|good night)\b", re.IGNORECASE),
    re.compile(r"\b(?:said|say|saying)\s+brb\b|\bbrb\b[.!?]*$", re.IGNORECASE),
]


def messages_to_extraction_text(messages: Sequence[Mapping[str, object]]) -> str:
    """Convert message list into role-labeled extraction input."""
    lines: list[str] = []
    for msg in messages:
        role = str(msg.get("role") or "").strip().lower()
        content = msg.get("content")
        if content is None:
            continue
        if role == "user":
            label = "[User]"
        elif role == "assistant":
            label = "[Assistant]"
        else:
            label = f"[{role.title() or 'Unknown'}]"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


@dataclass
class ExtractedFact:
    content: str
    category: str  # 'fact', 'preference', 'project', 'summary'
    confidence: float
    slot: str | None = None


@dataclass
class ExtractionOutcome:
    facts: list[ExtractedFact]
    raw_count: int
    calibrated_count: int
    rejected_count: int
    slot_coverage: int


EXTRACTION_PROMPT = """
You extract every concrete fact about the user from a role-labeled transcript.
Be exhaustive. Extract MORE rather than fewer facts. Retrieval handles relevance later.

Role awareness:
- Input contains [User] and [Assistant] markers.
- Extract ONLY facts about the user — their identity, preferences, hardware, software, tools, relationships, dates, plans, goals, opinions, and context.
- NEVER extract general knowledge, technical facts, or assistant-stated world knowledge.

What to extract (non-exhaustive):
- Identity: name, age, location, birthday, occupation, relationships
- Technical: programming languages, tools, editors, frameworks, OS, hardware specs, model numbers
- Preferences: likes, dislikes, choices, opinions, workflow preferences
- Projects: what they're building, planning, considering, waiting on
- Corrections: updated facts that replace previous ones
- Context: networking setup, deployment regions, accounts, configurations
- Tangential mentions: facts stated in passing ('oh by the way', 'I also want', 'back to the server') are equally important as primary topic facts

Delta context:
- Existing summary is context only.
- Do not re-extract facts that only appear in summary unless newly reaffirmed in this excerpt.

Output format:
Return JSON object with exactly one key:
{{
  "facts": [
    {{
      "content": "<single atomic fact>",
      "category": "fact|preference|project|correction|summary",
      "confidence": 0.0,
      "slot": "<optional canonical slot, e.g. vehicle, location.city, allergy.shellfish, project.daemon>"
    }}
  ]
}}

Atomic decomposition rule:
- Each memory object must contain ONE atomic fact.
- If one sentence implies multiple facts, split into multiple objects with different slots.

Decomposition example:
Input: "I'm thinking about getting a cat. My girlfriend wants one."
Output:
{{
  "facts": [
    {{"content": "User is considering getting a cat", "category": "project", "confidence": 0.60, "slot": "pet.cat.intent"}},
    {{"content": "User has a girlfriend", "category": "fact", "confidence": 0.88, "slot": "relationship.partner"}},
    {{"content": "User's girlfriend wants a cat", "category": "fact", "confidence": 0.82, "slot": "relationship.partner.pet_preference"}}
  ]
}}

Multi-value decomposition example:
Input: "I mainly code in Python and TypeScript."
Output: [
  {{"content": "User codes in Python", "category": "fact", "confidence": 0.85, "slot": "language.python"}},
  {{"content": "User codes in TypeScript", "category": "fact", "confidence": 0.85, "slot": "language.typescript"}}
]

Temporal detail preservation example:
Input: "We'll probably go to Japan in October"
Output: [
  {{"content": "User plans to travel to Japan in October", "category": "project", "confidence": 0.60, "slot": "travel.japan"}}
]

Confidence calibration:
- "definitely allergic to shellfish" -> around 0.92
- "might be lactose intolerant" -> around 0.65
- "thinking about moving" -> around 0.60
- "Oh by the way, my birthday is March 15th" -> confidence around 0.92, slot personal.birthday
- Direct factual statements ("My name is Julian", "I live in Adelaide") -> around 0.90

Do NOT extract:
- [Assistant]: "The NVIDIA RTX 5090 can draw up to 600W." (general knowledge stated by assistant)
- [Assistant]: "PostgreSQL uses MVCC." (assistant/domain knowledge)
- [User]: "Hi" / "Thanks" / "What's the weather today?" (filler/ephemeral)
- [User]: "The Eiffel Tower is in Paris." (general knowledge, not about user)

Existing summary:
{summary}

Conversation excerpt:
{text}
"""


def calibrate_confidence(fact: ExtractedFact) -> ExtractedFact:
    """Calibrate model confidence into reliable operational bands."""
    calibrated = ExtractedFact(
        content=fact.content,
        category=fact.category,
        confidence=fact.confidence,
        slot=fact.slot,
    )

    if calibrated.category == "correction":
        calibrated.confidence = max(calibrated.confidence, CORRECTION_MIN_CONFIDENCE)

    if HEDGE_WORDS_PATTERN.search(calibrated.content) and calibrated.confidence >= 0.75:
        calibrated.confidence = HEDGE_OVERRIDE_CONFIDENCE
        return calibrated

    if (
        STRONG_WORDS_PATTERN.search(calibrated.content)
        and calibrated.confidence <= 0.85
    ):
        calibrated.confidence = STRONG_OVERRIDE_CONFIDENCE

    return calibrated


def _coerce_confidence(value: Any) -> float:
    """Normalize model confidence values into [0.0, 1.0]."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return DEFAULT_EXTRACTED_CONFIDENCE
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return parsed


def _normalize_category(value: Any) -> str:
    raw = str(value or "fact").strip().lower() or "fact"
    normalized = CATEGORY_NORMALIZATION.get(raw, raw)
    if normalized not in ALLOWED_CATEGORIES:
        return "fact"
    return normalized


def validate_fact(fact: ExtractedFact) -> bool:
    """Validate extracted facts while allowing useful low-confidence user facts."""
    content = fact.content.strip()
    if not content:
        logger.debug("Extraction validation rejected fact: empty content")
        return False
    if len(content) < 10:
        logger.debug("Extraction validation rejected fact: too short")
        return False
    if ASSISTANT_PREFIX_PATTERN.search(content):
        logger.debug("Extraction validation rejected fact: assistant-prefixed")
        return False
    if not USER_SUBJECT_PATTERN.search(content):
        logger.debug("Extraction validation rejected fact: missing user subject")
        return False
    if GENERAL_KNOWLEDGE_PREFIX_PATTERN.search(
        content
    ) and not USER_SUBJECT_PATTERN.search(content):
        logger.debug("Extraction validation rejected fact: general-knowledge prefix")
        return False
    for pattern in FILLER_PATTERNS:
        if pattern.search(content):
            logger.debug("Extraction validation rejected fact: filler pattern")
            return False
    for pattern in EPHEMERAL_ACTION_PATTERNS:
        if pattern.search(content):
            logger.debug("Extraction validation rejected fact: ephemeral_action")
            return False
    return True


async def extract_facts_from_text(
    text: str,
    model: str = "openrouter/openai/gpt-4o-mini",
    *,
    summary: str | None = None,
    retry_hint: str | None = None,
) -> ExtractionOutcome:
    """Extract, calibrate, and validate memory facts from role-labeled text."""
    try:
        bounded_text = text[-MAX_EXTRACTION_INPUT_CHARS:]
        if retry_hint:
            bounded_text = f"{bounded_text}\n\n[Retry hint]\n{retry_hint}"

        response = await litellm.acompletion(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You extract structured facts from text. "
                        "Output must be valid JSON with top-level key 'facts'."
                    ),
                },
                {
                    "role": "user",
                    "content": EXTRACTION_PROMPT.format(
                        summary=(summary or "None"),
                        text=bounded_text,
                    ),
                },
            ],
            temperature=EXTRACTION_TEMPERATURE,
            top_p=EXTRACTION_TOP_P,
            max_tokens=EXTRACTION_MAX_TOKENS,
            response_format={"type": "json_object"},
        )

        response_data: Any = response
        model_dump = getattr(response, "model_dump", None)
        if callable(model_dump):
            response_data = model_dump()
        else:
            dict_method = getattr(response, "dict", None)
            if callable(dict_method):
                response_data = dict_method()

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
            return ExtractionOutcome(
                facts=[],
                raw_count=0,
                calibrated_count=0,
                rejected_count=0,
                slot_coverage=0,
            )

        data = json.loads(content)

        raw_facts: list[ExtractedFact] = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict) and "facts" in data:
            items = data["facts"]
        else:
            items = [data] if data else []

        for item in items:
            if isinstance(item, dict) and "content" in item:
                content = str(item.get("content") or "").strip()
                category = _normalize_category(item.get("category"))
                raw_facts.append(
                    ExtractedFact(
                        content=content,
                        category=category,
                        confidence=_coerce_confidence(
                            item.get("confidence", DEFAULT_EXTRACTED_CONFIDENCE)
                        ),
                        slot=item.get("slot"),
                    )
                )

        calibrated_facts = [calibrate_confidence(fact) for fact in raw_facts]
        validated_facts = [fact for fact in calibrated_facts if validate_fact(fact)]
        rejected_count = len(calibrated_facts) - len(validated_facts)
        slot_coverage = sum(1 for fact in validated_facts if fact.slot)
        logger.info(
            "Extraction: %s raw -> %s calibrated -> %s validated (%s rejected)",
            len(raw_facts),
            len(calibrated_facts),
            len(validated_facts),
            rejected_count,
        )

        return ExtractionOutcome(
            facts=validated_facts,
            raw_count=len(raw_facts),
            calibrated_count=len(calibrated_facts),
            rejected_count=rejected_count,
            slot_coverage=slot_coverage,
        )
    except Exception as e:
        logger.error("Extraction error", exc_info=True)
        return ExtractionOutcome(
            facts=[],
            raw_count=0,
            calibrated_count=0,
            rejected_count=0,
            slot_coverage=0,
        )


async def process_extraction(
    store: MemoryStore, user_id: uuid.UUID, conversation_id: uuid.UUID, text: str
) -> bool:
    """Orchestrate extraction -> dedup -> insert."""
    from orchestrator.memory.dedup import deduplicate_facts

    conversation = await store.get_conversation(conversation_id)
    summary = None
    if conversation:
        summary = conversation.get("summary")

    model = "openrouter/openai/gpt-4o-mini"
    outcome = await extract_facts_from_text(text, model=model, summary=summary)
    retry_used = False

    should_retry = len(text.strip()) >= 80 and (
        not outcome.facts
        or (
            outcome.calibrated_count > 0
            and outcome.rejected_count >= outcome.calibrated_count
        )
    )
    if should_retry:
        retry_used = True
        retry_outcome = await extract_facts_from_text(
            text,
            model=model,
            summary=summary,
            retry_hint=(
                "Retry with exhaustive coverage: scan the entire conversation excerpt, "
                "split multi-value statements into atomic facts, and include late/tangential "
                "mentions. Keep facts user-specific and durable."
            ),
        )
        if retry_outcome.facts:
            outcome = retry_outcome

    if not outcome.facts:
        return True

    result = await deduplicate_facts(
        store,
        user_id,
        outcome.facts,
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
                "slot": f.slot,
            }
            for f in outcome.facts
        ],
        dedup_results={
            "merged": len(result.merged),
            "superseded": len(result.superseded),
            "new": len(result.new),
            "raw_count": outcome.raw_count,
            "calibrated_count": outcome.calibrated_count,
            "rejected_count": outcome.rejected_count,
            "slot_coverage": outcome.slot_coverage,
            "retry_used": retry_used,
        },
        model_used=model,
    )
    return True
