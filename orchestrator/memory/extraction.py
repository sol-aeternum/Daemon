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

from orchestrator.config import get_settings
from orchestrator.memory.store import MemoryStore


def _get_provider_call_params(model: str) -> dict[str, Any]:
    """Get provider configuration for litellm.acompletion call.

    Returns call parameters including api_base, api_key, extra_headers.
    """
    settings = get_settings()
    provider_config = settings.get_provider_config("openrouter")

    # Normalize model for OpenRouter
    if model.startswith("openrouter/"):
        normalized_model = model
    else:
        normalized_model = f"openrouter/{model}"

    call_params: dict[str, Any] = {
        "model": normalized_model,
        "timeout": provider_config.timeout_s,
    }

    if provider_config.base_url:
        call_params["api_base"] = provider_config.base_url
    if provider_config.api_key:
        call_params["api_key"] = provider_config.api_key
    if provider_config.extra_headers:
        call_params["extra_headers"] = provider_config.extra_headers

    return call_params


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

# Patterns to reject meta-descriptions (not actual facts about user)
META_CONTENT_PATTERNS = [
    re.compile(r"^user\s+(said|says)\s+['\"]", re.IGNORECASE),  # "User says 'lol'"
    re.compile(r"^user\s+(used|uses)\s+the\s+(text|word|phrase)", re.IGNORECASE),
    re.compile(r"^user\s+is\s+(expressing|showing|displaying)", re.IGNORECASE),
    re.compile(r"^user\s+requested\s+", re.IGNORECASE),  # "User requested a joke"
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
You extract durable facts about the user from a role-labeled transcript.
Extract facts worth remembering across conversations. Prioritize quality and durability.
ALWAYS start facts with "User" (e.g., "User is 28", "User lives in Adelaide").
Facts not starting with "User" will be rejected by validation.

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

CRITICAL - Identity and basic facts (ALWAYS extract):
- User's name, age, location, occupation MUST be extracted
- Family relationships (brother, sister, spouse, children, pets) MUST be extracted
- Example: "My name is Julian, I'm 28" -> "User's name is Julian", "User is 28 years old"
- Example: "I have a brother named Callan and a dog named Koda" -> "User has a brother named Callan", "User has a dog named Koda"

CRITICAL - Corrections and current state (extract ONLY current):
- When user says "actually", "I sold", "I changed", "I no longer" -> extract ONLY the NEW state
- Example: "I sold my Corolla. I drive a Tesla Model 3 now." -> extract only "User drives a Tesla Model 3"

CRITICAL - Projects and goals (ALWAYS extract):
- What user is building, learning, or working on
- Example: "building an AI assistant called Daemon" -> "User is building an AI assistant called Daemon"
- Example: "learning Rust this year" -> "User is learning Rust this year"

CRITICAL - Explicit memory instructions (ALWAYS extract):
- When user says "remember this" or provides account/config details
- Example: "My AWS account is in us-east-1" -> "User's AWS account is in us-east-1"
- Preferences: "I hate YAML" -> "User hates YAML"

CRITICAL - Multi-turn durable facts (ALWAYS extract, even in tangential mentions):
- Birthdays, dates, and temporal markers mentioned anywhere in conversation
- Tools, services, and software user explicitly wants or uses (e.g., "want to try Tailscale", "planning to use")
- Primary purpose and intended use (e.g., "for LLM inference", "to run models")
- Setup preferences and configuration details (e.g., "I'll install Arch", "using Ubuntu for now")
- Example: "Oh by the way my birthday is March 15" -> "User's birthday is March 15"
- Example: "I want to try Tailscale for my homelab" -> "User wants to try Tailscale"
- Example: "Going to use it for LLM inference" -> "User intends to use it for LLM inference"
- Example: "I used Arch Linux before but now I'm on macOS" -> extract BOTH "User used Arch Linux before" AND "User is on macOS"

Delta context:
- Conversation context so far is for background only.
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

Implicit preferences:
- Users reveal preferences through choices and behavior, not just direct statements.
  Extract these as preferences even when the user doesn't say "prefer" or "like."
- Repeated choices imply preference: "I went with Python again" → "User prefers Python"
- Habitual behavior implies preference: "I always take the train" → "User prefers commuting by train"
- Positive reactions imply preference: "That Italian place was amazing" → "User enjoys Italian food"
- Negative reactions imply avoidance: "I can't stand meetings before 10am" → "User avoids early morning meetings"
- Selection from options: "I'll go with the blue one" → "User chose blue" (lower confidence: 0.60)
- Set confidence for inferred preferences at 0.60-0.75 unless the language is strong
  ("love", "always", "can't stand", "amazing", "hate" → 0.80-0.88).

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

Preference inference example:
Input: "Yeah I ended up taking the train to work again today, finished another chapter of my book on the way"
RIGHT (extract behavior as preference):
- "User commutes to work by train" (category: fact, confidence: 0.85, slot: commute.mode)
- "User reads books during their commute" (category: preference, confidence: 0.78, slot: hobby.reading)
- "User is currently reading a book" (category: fact, confidence: 0.80, slot: hobby.reading.current)

Input: "I tried that new Thai place on King Street, the pad see ew was incredible"
RIGHT (extract positive reaction as preference):
- "User visited a Thai restaurant on King Street" (category: fact, confidence: 0.88, slot: dining.recent)
- "User enjoys pad see ew" (category: preference, confidence: 0.80, slot: food.preference)

Input: "Ended up mass transiting again, at least I got to listen to my podcast"
RIGHT (extract habitual choice as preference):
- "User regularly commutes via public transit" (category: preference, confidence: 0.75, slot: commute.mode)
- "User listens to podcasts during their commute" (category: preference, confidence: 0.78, slot: hobby.podcast)

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

Conversation context so far:
{summary}

New messages to extract from:
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
    # Reject meta-descriptions of what user said (not facts about user)
    for pattern in META_CONTENT_PATTERNS:
        if pattern.search(content):
            logger.debug("Extraction validation rejected fact: meta content pattern")
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

        # Get provider call parameters
        call_params = _get_provider_call_params(model)
        call_params.update(
            {
                "messages": [
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
                "temperature": EXTRACTION_TEMPERATURE,
                "top_p": EXTRACTION_TOP_P,
                "max_tokens": EXTRACTION_MAX_TOKENS,
                "response_format": {"type": "json_object"},
            }
        )

        response = await litellm.acompletion(**call_params)

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

    # Trigger summary update after successful extraction (best-effort)
    try:
        import importlib

        summary_module = importlib.import_module("orchestrator.memory.summary")
        generate_or_update_summary = getattr(
            summary_module, "generate_or_update_summary"
        )
        await generate_or_update_summary(conversation_id, store)
    except Exception:
        # Summary generation is best-effort; don't fail extraction if it fails
        pass

    return True
