"""Entity extraction and resolution primitives for Daemon memory system.

This module provides:
- Candidate mention extraction from memory content (baseline regex + optional spaCy)
- Entity resolution against canonical store
- Batch LLM confirmation for ambiguous merges via BACKGROUND_REASONING_MODEL

The baseline extraction works without spaCy. When spaCy is available, it may enrich
candidate extraction with NER, but the feature degrades cleanly when spaCy is absent.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import litellm

from orchestrator.config import get_settings
from orchestrator.memory.embedding import embed_query
from orchestrator.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# spaCy availability flag (set once at import time)
# ---------------------------------------------------------------------------
_spacy_available: bool | None = None


def _is_spacy_available() -> bool:
    global _spacy_available
    if _spacy_available is None:
        import importlib.util

        _spacy_available = importlib.util.find_spec("spacy") is not None
    return _spacy_available


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# High-confidence merge threshold (embedding similarity)
HIGH_CONFIDENCE_MERGE_SIMILARITY = 0.88

# Ambiguous threshold - below this requires LLM confirmation
AMBIGUOUS_MERGE_SIMILARITY = 0.75

# Rejection threshold - below this, never merge
REJECT_MERGE_SIMILARITY = 0.60

# Maximum candidates to send in one LLM confirmation batch
BATCH_CONFIRMATION_MAX = 10

# Entity type patterns for regex-based extraction
CAPITALIZED_PHRASE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b")
QUOTED_STRING = re.compile(r"'([^']+)'|\"([^\"]+)\"")
EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
URL_PATTERN = re.compile(r"https?://[^\s]+")
HASHTAG_PATTERN = re.compile(r"#[A-Za-z0-9_]+")
MENTION_PATTERN = re.compile(r"@[A-Za-z0-9_]+")

# Common stopwords to filter out capitalized phrases
STOPWORDS = {
    "the",
    "and",
    "but",
    "for",
    "nor",
    "or",
    "yet",
    "so",
    "at",
    "by",
    "in",
    "of",
    "on",
    "to",
    "up",
    "as",
    "is",
    "it",
    "he",
    "she",
    "they",
    "we",
    "you",
    "i",
    "my",
    "his",
    "her",
    "their",
    "our",
    "your",
    "this",
    "that",
    "these",
    "those",
    "a",
    "an",
    "if",
    "then",
    "else",
    "when",
    "where",
    "which",
    "who",
    "whom",
    "whose",
    "why",
    "how",
    "all",
    "each",
    "every",
    "both",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "only",
    "own",
    "same",
    "than",
    "too",
    "very",
    "just",
    "also",
    "now",
    "here",
    "there",
}

# Slot families that indicate specific entity types
ENTITY_SLOT_PREFIXES = {
    "person": "person",
    "location": "location",
    "organization": "organization",
    "vehicle": "vehicle",
    "pet": "pet",
    "food": "food",
    "drink": "drink",
    "movie": "movie",
    "book": "book",
    "song": "song",
    "game": "game",
    "software": "software",
    "language": "language",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CandidateMention:
    """A candidate entity mention extracted from memory content."""

    text: str
    normalized_key: str  # lowercase, stripped
    source_memory_id: uuid.UUID | None = None
    context: str | None = None  # surrounding text for disambiguation
    confidence: float = 0.5  # extraction confidence
    entity_type: str | None = None  # person, location, etc. (spaCy or slot-based)


@dataclass
class EntityResolution:
    """Result of resolving a candidate against canonical entities."""

    mention: CandidateMention
    resolved_entity_id: uuid.UUID | None = None
    canonical_name: str | None = None
    is_new: bool = True
    merge_decision: str = "new"  # "new" | "merge" | "ambiguous" | "reject"
    similarity: float = 0.0
    alias_added: bool = False


@dataclass
class ExtractionResult:
    """Result of extracting entities from a batch of memories."""

    candidates: list[CandidateMention]
    resolutions: list[EntityResolution]
    ambiguous_merges_needed: list[EntityResolution] = field(default_factory=list)
    spacy_enriched: bool = False


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _normalize_lookup_key(text: str) -> str:
    """Create a normalized lookup key from entity text.

    Lowercase, strip whitespace and common punctuation.
    """
    normalized = text.lower().strip()
    # Remove common punctuation
    normalized = re.sub(r"[^\w\s]", "", normalized)
    # Collapse whitespace
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _is_likely_entity(text: str, min_length: int = 2, max_length: int = 60) -> bool:
    if not text or len(text) < min_length or len(text) > max_length:
        return False

    words = text.lower().split()
    if all(w in STOPWORDS for w in words):
        return False

    if re.match(r"^\d+(?:\.\d+)?$", text):
        return False

    if text.lower().startswith("the ") and len(text) < 8:
        return False

    words_only = re.sub(r"[^\w\s]", "", text.lower()).split()
    if len(words_only) >= 2 and len(set(words_only)) == 1:
        return False

    return True


def _extract_from_slot(text: str) -> str | None:
    """Extract entity type hint from memory slot if present."""
    for prefix in ENTITY_SLOT_PREFIXES:
        if text.lower().startswith(prefix):
            return ENTITY_SLOT_PREFIXES[prefix]
    return None


def _get_provider_call_params(model: str) -> dict[str, Any]:
    """Get provider configuration for litellm.acompletion call."""
    settings = get_settings()
    provider_config = settings.get_provider_config("openrouter")

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


# ---------------------------------------------------------------------------
# Baseline extraction: regex patterns
# ---------------------------------------------------------------------------


def extract_candidates_baseline(
    content: str,
    memory_id: uuid.UUID | None = None,
    memory_slot: str | None = None,
) -> list[CandidateMention]:
    """Extract candidate entity mentions using regex patterns.

    This is the baseline extraction that works without spaCy.
    Uses capitalized phrases, quoted strings, and context-aware filtering.

    Args:
        content: Memory content text
        memory_id: Optional memory ID for source tracking
        memory_slot: Optional memory slot for entity type hints

    Returns:
        List of CandidateMention objects
    """
    candidates: list[CandidateMention] = []
    seen_keys: set[str] = set()

    # Extract capitalized phrases
    for match in CAPITALIZED_PHRASE.finditer(content):
        phrase = match.group(1).strip()
        if not _is_likely_entity(phrase):
            continue

        key = _normalize_lookup_key(phrase)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        # Get context (surrounding text)
        start = max(0, match.start() - 30)
        end = min(len(content), match.end() + 30)
        context = content[start:end]

        # Check slot for entity type hint
        entity_type = None
        if memory_slot:
            entity_type = _extract_from_slot(memory_slot)

        candidates.append(
            CandidateMention(
                text=phrase,
                normalized_key=key,
                source_memory_id=memory_id,
                context=context,
                confidence=0.5,
                entity_type=entity_type,
            )
        )

    # Extract quoted strings as potential aliases/nicknames
    for match in QUOTED_STRING.finditer(content):
        for group_idx in (1, 2):
            phrase = match.group(group_idx)
            if not phrase:
                continue
            phrase = phrase.strip()
            if len(phrase) < 2 or len(phrase) > 40:
                continue
            if not _is_likely_entity(phrase):
                continue

            key = _normalize_lookup_key(phrase)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            start = max(0, match.start() - 30)
            end = min(len(content), match.end() + 30)
            context = content[start:end]

            candidates.append(
                CandidateMention(
                    text=phrase,
                    normalized_key=key,
                    source_memory_id=memory_id,
                    context=context,
                    confidence=0.4,  # Quoted strings are less confident
                    entity_type=None,
                )
            )

    # Extract hashtags as potential entity references
    for match in HASHTAG_PATTERN.finditer(content):
        tag = match.group(0)[1:]
        key = f"#{_normalize_lookup_key(tag)}"
        if key in seen_keys:
            continue
        seen_keys.add(key)

        start = max(0, match.start() - 30)
        end = min(len(content), match.end() + 30)
        context = content[start:end]

        candidates.append(
            CandidateMention(
                text=f"#{tag}",
                normalized_key=key,
                source_memory_id=memory_id,
                context=context,
                confidence=0.35,
                entity_type=None,
            )
        )

    # Extract mentions
    for match in MENTION_PATTERN.finditer(content):
        mention = match.group(0)[1:]
        key = f"@{_normalize_lookup_key(mention)}"
        if key in seen_keys:
            continue
        seen_keys.add(key)

        start = max(0, match.start() - 30)
        end = min(len(content), match.end() + 30)
        context = content[start:end]

        candidates.append(
            CandidateMention(
                text=f"@{mention}",
                normalized_key=key,
                source_memory_id=memory_id,
                context=context,
                confidence=0.35,
                entity_type=None,
            )
        )

    return candidates


# ---------------------------------------------------------------------------
# spaCy enrichment (optional)
# ---------------------------------------------------------------------------


async def extract_candidates_spacy(
    content: str,
    memory_id: uuid.UUID | None = None,
    memory_slot: str | None = None,
) -> list[CandidateMention]:
    """Extract candidate entity mentions using spaCy NER.

    This is an optional enrichment layer on top of baseline extraction.
    Returns empty list if spaCy is not available.

    Args:
        content: Memory content text
        memory_id: Optional memory ID for source tracking
        memory_slot: Optional memory slot for entity type hints

    Returns:
        List of CandidateMention objects, or empty if spaCy unavailable
    """
    if not _is_spacy_available():
        return []

    try:
        import importlib

        spacy = importlib.import_module("spacy")

        # Try to load model, return baseline if not available
        try:
            nlp = spacy.load("en_core_web_sm")
        except OSError:
            logger.warning("spaCy en_core_web_sm model not found, skipping NER enrichment")
            return []

        doc = nlp(content[:10000])  # Limit to first 10k chars
        candidates: list[CandidateMention] = []
        seen_keys: set[str] = set()

        entity_type_map = {
            "PER": "person",
            "PERSON": "person",
            "GPE": "location",
            "LOC": "location",
            "ORG": "organization",
            "FAC": "location",
            "PRODUCT": "software",
            "EVENT": "event",
            "WORK_OF_ART": "book",
            "LANGUAGE": "language",
        }

        for ent in doc.ents:
            if ent.label_ not in entity_type_map:
                continue

            text = ent.text.strip()
            if not _is_likely_entity(text):
                continue

            key = _normalize_lookup_key(text)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            # Get context
            start = max(0, ent.start_char - 30)
            end = min(len(content), ent.end_char + 30)
            context = content[start:end]

            # Slot-based type hint takes precedence
            entity_type = _extract_from_slot(memory_slot) if memory_slot else None
            if not entity_type:
                entity_type = entity_type_map.get(ent.label_)

            candidates.append(
                CandidateMention(
                    text=text,
                    normalized_key=key,
                    source_memory_id=memory_id,
                    context=context,
                    confidence=0.7,  # spaCy NER is more confident
                    entity_type=entity_type,
                )
            )

        return candidates

    except Exception as e:
        logger.warning(f"spaCy NER extraction failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Candidate mention extraction (combines baseline + optional spaCy)
# ---------------------------------------------------------------------------


async def extract_entity_candidates(
    content: str,
    memory_id: uuid.UUID | None = None,
    memory_slot: str | None = None,
    use_spacy: bool = True,
) -> list[CandidateMention]:
    """Extract candidate entity mentions from memory content.

    Combines baseline regex extraction with optional spaCy NER enrichment.
    spaCy enrichment is additive only - baseline always runs.

    Args:
        content: Memory content text
        memory_id: Optional memory ID for source tracking
        memory_slot: Optional memory slot for entity type hints
        use_spacy: Whether to attempt spaCy enrichment (default True)

    Returns:
        List of CandidateMention objects
    """
    # Baseline extraction always runs
    candidates = extract_candidates_baseline(content, memory_id, memory_slot)
    seen_keys = {c.normalized_key for c in candidates}

    # Optional spaCy enrichment
    if use_spacy and _is_spacy_available():
        try:
            spacy_candidates = await extract_candidates_spacy(content, memory_id, memory_slot)
            for cand in spacy_candidates:
                if cand.normalized_key not in seen_keys:
                    candidates.append(cand)
                    seen_keys.add(cand.normalized_key)
        except Exception as e:
            logger.warning(f"spaCy enrichment failed, continuing with baseline: {e}")

    return candidates


# ---------------------------------------------------------------------------
# Entity resolution using embeddings
# ---------------------------------------------------------------------------


async def _compute_mention_embedding(mention: CandidateMention) -> list[float] | None:
    """Compute embedding for a candidate mention."""
    try:
        # Include entity type in embedding context for better matching
        text = mention.text
        if mention.entity_type:
            text = f"{mention.entity_type}: {text}"
        return await embed_query(text)
    except Exception as e:
        logger.warning(f"Failed to embed mention '{mention.text}': {e}")
        return None


async def _get_canonical_embeddings(
    user_id: uuid.UUID,
    store: MemoryStore,
) -> list[tuple[uuid.UUID, str, list[float]]]:
    """Get all canonical entity embeddings for a user."""
    entities = await store.get_entities_for_user(user_id, limit=1000)
    result: list[tuple[uuid.UUID, str, list[float]]] = []

    for entity in entities:
        entity_id = entity.get("id")
        if not entity_id:
            continue
        canonical_name = entity.get("canonical_name", "")
        # Get entity embedding by embedding the canonical name
        try:
            embedding = await embed_query(canonical_name)
            result.append((entity_id, canonical_name, embedding))
        except Exception:
            continue

    return result


def _cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    if len(vec1) != len(vec2):
        return 0.0

    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = sum(a * a for a in vec1) ** 0.5
    norm2 = sum(b * b for b in vec2) ** 0.5

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return dot_product / (norm1 * norm2)


async def resolve_candidate(
    mention: CandidateMention,
    user_id: uuid.UUID,
    store: MemoryStore,
    canonical_entities: list[tuple[uuid.UUID, str, list[float]]],
) -> EntityResolution:
    """Resolve a single candidate mention against canonical entities.

    Uses embedding similarity to determine if the mention matches an existing
    entity or should be created as new.

    Args:
        mention: Candidate mention to resolve
        user_id: User ID for context
        store: MemoryStore instance
        canonical_entities: List of (entity_id, canonical_name, embedding) tuples

    Returns:
        EntityResolution with merge decision
    """
    resolution = EntityResolution(
        mention=mention,
        resolved_entity_id=None,
        canonical_name=None,
        is_new=True,
        merge_decision="new",
        similarity=0.0,
        alias_added=False,
    )

    if not canonical_entities:
        return resolution

    # Compute embedding for this mention
    mention_embedding = await _compute_mention_embedding(mention)
    if not mention_embedding:
        return resolution

    best_match: tuple[uuid.UUID, str, list[float], float] | None = None
    best_similarity = 0.0

    for entity_id, canonical_name, entity_embedding in canonical_entities:
        sim = _cosine_similarity(mention_embedding, entity_embedding)
        if sim > best_similarity:
            best_similarity = sim
            best_match = (entity_id, canonical_name, entity_embedding, sim)

    if not best_match:
        return resolution

    entity_id, canonical_name, _, sim = best_match
    resolution.similarity = sim

    if sim >= HIGH_CONFIDENCE_MERGE_SIMILARITY:
        # High confidence merge
        resolution.resolved_entity_id = entity_id
        resolution.canonical_name = canonical_name
        resolution.is_new = False
        resolution.merge_decision = "merge"
    elif sim >= AMBIGUOUS_MERGE_SIMILARITY:
        # Ambiguous - needs LLM confirmation
        resolution.resolved_entity_id = entity_id
        resolution.canonical_name = canonical_name
        resolution.is_new = False
        resolution.merge_decision = "ambiguous"
    elif sim >= REJECT_MERGE_SIMILARITY:
        # Low similarity - reject but might be related
        resolution.merge_decision = "reject"
    # else: similarity too low, treat as new

    return resolution


# ---------------------------------------------------------------------------
# Batch LLM confirmation for ambiguous merges
# ---------------------------------------------------------------------------

ENTITY_CONFIRMATION_PROMPT = """
You are an entity resolution system. Given a candidate mention and an existing
canonical entity, determine if they refer to the SAME entity.

Candidate mention: "{mention_text}"
Context: "{context}"
Existing canonical entity: "{canonical_name}"
Similarity score: {similarity:.2f}

Reply with EXACTLY one of these formats:
YES: <one sentence explanation>
NO: <one sentence explanation>
UNSURE: <one sentence explanation>

Consider:
- Names that are obvious aliases or nicknames (e.g., "Mike" vs "Michael")
- Partial name matches (e.g., "John Smith" vs "Smith")
- Same person/entity referred to in different contexts
- Different entities that happen to share a name
"""


async def confirm_merge_llm(
    resolution: EntityResolution,
) -> tuple[bool, str]:
    """Confirm or reject an ambiguous merge using LLM.

    Args:
        resolution: EntityResolution with ambiguous decision

    Returns:
        (confirmed, explanation) tuple
    """
    try:
        model = get_settings().background_reasoning_model

        call_params = _get_provider_call_params(model)
        call_params.update(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": ENTITY_CONFIRMATION_PROMPT.format(
                            mention_text=resolution.mention.text,
                            context=resolution.mention.context or "",
                            canonical_name=resolution.canonical_name or "",
                            similarity=resolution.similarity,
                        ),
                    }
                ],
                "temperature": 0.1,
                "max_tokens": 100,
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
                message = choices[0].get("message") if isinstance(choices[0], dict) else None
                if isinstance(message, dict):
                    content = message.get("content")

        if not isinstance(content, str) or not content:
            return False, "LLM returned empty response"

        content_lower = content.lower().strip()
        if content_lower.startswith("yes"):
            return True, content
        elif content_lower.startswith("no"):
            return False, content
        else:
            return False, content

    except Exception as e:
        logger.warning(f"LLM confirmation failed: {e}")
        return False, f"Error: {e}"


async def batch_confirm_merges(
    resolutions: list[EntityResolution],
) -> list[EntityResolution]:
    """Confirm ambiguous merges in batch using BACKGROUND_REASONING_MODEL.

    Processes up to BATCH_CONFIRMATION_MAX ambiguous candidates per batch.
    Updates merge_decision based on LLM response.

    Args:
        resolutions: List of EntityResolution objects with ambiguous decisions

    Returns:
        Updated list of EntityResolution with confirmed/rejected merges
    """
    if not resolutions:
        return resolutions

    # Filter to ambiguous ones only
    ambiguous = [r for r in resolutions if r.merge_decision == "ambiguous"]
    if not ambiguous:
        return resolutions

    # Process in batches
    results: list[EntityResolution] = []

    for i in range(0, len(ambiguous), BATCH_CONFIRMATION_MAX):
        batch = ambiguous[i : i + BATCH_CONFIRMATION_MAX]

        # Process batch concurrently
        import asyncio

        tasks = [confirm_merge_llm(r) for r in batch]
        confirmations = await asyncio.gather(*tasks, return_exceptions=True)

        for resolution, confirmation in zip(batch, confirmations):
            if isinstance(confirmation, Exception):
                resolution.merge_decision = "reject"
            else:
                assert isinstance(confirmation, tuple) and len(confirmation) == 2
                confirmed = confirmation[0]
                if confirmed:
                    resolution.merge_decision = "merge"
                else:
                    resolution.merge_decision = "reject"
            results.append(resolution)

    # Update original resolutions with results
    for r in resolutions:
        if r.merge_decision == "ambiguous":
            # Find corresponding result
            for result in results:
                if result.mention.text == r.mention.text:
                    r.merge_decision = result.merge_decision
                    break

    return resolutions


# ---------------------------------------------------------------------------
# Full extraction and resolution pipeline
# ---------------------------------------------------------------------------


async def extract_and_resolve_entities(
    user_id: uuid.UUID,
    store: MemoryStore,
    memory_contents: list[tuple[str, uuid.UUID | None, str | None]],
    use_spacy: bool = True,
) -> ExtractionResult:
    """Extract and resolve entities from memory contents.

    This is the main entry point for entity extraction and resolution.
    It combines:
    1. Candidate extraction (baseline + optional spaCy)
    2. Resolution against canonical entities using embeddings
    3. Batch LLM confirmation for ambiguous merges

    Args:
        user_id: User ID
        store: MemoryStore instance
        memory_contents: List of (content, memory_id, memory_slot) tuples
        use_spacy: Whether to use spaCy enrichment (default True)

    Returns:
        ExtractionResult with candidates, resolutions, and ambiguous merges
    """
    # Step 1: Extract candidate mentions from all memories
    all_candidates: list[CandidateMention] = []
    for content, memory_id, memory_slot in memory_contents:
        candidates = await extract_entity_candidates(
            content, memory_id, memory_slot, use_spacy=use_spacy
        )
        all_candidates.extend(candidates)

    if not all_candidates:
        return ExtractionResult(
            candidates=[],
            resolutions=[],
            ambiguous_merges_needed=[],
            spacy_enriched=use_spacy and _is_spacy_available(),
        )

    # Step 2: Get canonical entities for this user
    canonical_entities = await _get_canonical_embeddings(user_id, store)

    # Step 3: Resolve each candidate
    resolutions: list[EntityResolution] = []
    for candidate in all_candidates:
        resolution = await resolve_candidate(candidate, user_id, store, canonical_entities)
        resolutions.append(resolution)

    # Step 4: Separate ambiguous merges for batch confirmation
    ambiguous_merges = [r for r in resolutions if r.merge_decision == "ambiguous"]

    # Step 5: Batch confirm ambiguous merges
    if ambiguous_merges:
        resolutions = await batch_confirm_merges(resolutions)
        # Update ambiguous_merges list
        ambiguous_merges = [r for r in resolutions if r.merge_decision == "ambiguous"]

    return ExtractionResult(
        candidates=all_candidates,
        resolutions=resolutions,
        ambiguous_merges_needed=ambiguous_merges,
        spacy_enriched=use_spacy and _is_spacy_available(),
    )


# ---------------------------------------------------------------------------
# Entity persistence helpers
# ---------------------------------------------------------------------------


async def persist_extraction_result(
    user_id: uuid.UUID,
    store: MemoryStore,
    extraction_result: ExtractionResult,
) -> list[uuid.UUID]:
    """Persist extraction results to the canonical entity store.

    Creates new entities for new candidates, adds aliases to merged entities,
    and links entities to source memories.

    Args:
        user_id: User ID
        store: MemoryStore instance
        extraction_result: Result from extract_and_resolve_entities

    Returns:
        List of created/updated entity IDs
    """
    entity_ids: list[uuid.UUID] = []

    new_resolutions: list[EntityResolution] = []
    merged_resolutions: list[EntityResolution] = []

    for resolution in extraction_result.resolutions:
        if resolution.merge_decision == "reject":
            continue
        if resolution.merge_decision == "new":
            new_resolutions.append(resolution)
        else:
            merged_resolutions.append(resolution)

    # Handle merged resolutions: group by canonical entity for alias consolidation
    entities_by_canonical: dict[uuid.UUID, list[EntityResolution]] = {}
    for resolution in merged_resolutions:
        canonical_id = resolution.resolved_entity_id
        if canonical_id is None:
            continue
        if canonical_id not in entities_by_canonical:
            entities_by_canonical[canonical_id] = []
        entities_by_canonical[canonical_id].append(resolution)

    for canonical_id, resolutions in entities_by_canonical.items():
        canonical_name = None
        alias = None
        linked_memory_id = None

        for r in resolutions:
            if r.canonical_name:
                canonical_name = r.canonical_name
                alias = r.mention.text
                linked_memory_id = r.mention.source_memory_id
                break

        if not canonical_name:
            continue

        lookup_key = _normalize_lookup_key(canonical_name)

        entity = await store.get_entity(canonical_id)
        if entity:
            existing_aliases = entity.get("aliases", [])
            existing_lookup_keys = list(entity.get("alias_lookup_keys", []))

            if alias and alias != canonical_name:
                if alias not in existing_aliases:
                    existing_aliases.append(alias)
                alias_lookup_key = _normalize_lookup_key(alias)
                if alias_lookup_key not in existing_lookup_keys:
                    existing_lookup_keys.append(alias_lookup_key)

            await store.update_entity_aliases(canonical_id, existing_aliases, existing_lookup_keys)

            if linked_memory_id:
                await store.link_entity_to_memory(canonical_id, linked_memory_id)

            entity_ids.append(canonical_id)

    # Handle new resolutions: each gets its own independent entity
    for resolution in new_resolutions:
        canonical_name = resolution.mention.text
        linked_memory_id = resolution.mention.source_memory_id
        lookup_key = _normalize_lookup_key(canonical_name)

        entity = await store.insert_entity(
            user_id=user_id,
            canonical_name=canonical_name,
            lookup_key=lookup_key,
            aliases=None,
            alias_lookup_keys=None,
            source_memory_id=linked_memory_id,
        )

        new_id = entity.get("id")
        if new_id:
            if linked_memory_id:
                await store.link_entity_to_memory(new_id, linked_memory_id)
            entity_ids.append(new_id)

    return entity_ids
