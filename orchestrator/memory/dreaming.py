from __future__ import annotations

# pyright: reportMissingImports=false, reportAny=false, reportExplicitAny=false

import logging
import json
import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

import asyncpg
import litellm

from orchestrator.config import get_settings
from orchestrator.memory.embedding import embed_documents
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore

logger = logging.getLogger(__name__)

MAX_DREAM_OBSERVATIONS_PER_FAMILY = 2

DREAM_SYNTHESIS_PROMPT = """You are synthesizing higher-level observations from related user memories.

You will receive a cluster of active L1 memories from the same slot family.

Return JSON with this exact shape:
{"observations": [{"content": "...", "confidence": 0.0, "source_memory_ids": ["..."]}]}

Write up to 2 concise observations that:
- are grounded only in the provided memories
- identify stable patterns, themes, or higher-order takeaways
- preserve important specifics when they matter
- do not invent facts or speculate beyond the evidence
- do not mention source memories, clustering, databases, or encryption
- must start with the word "User"

Output rules:
- Output valid JSON only
- Each `confidence` must be a float from 0.0 to 1.0
- Calibrate confidence conservatively:
  - 0.4-0.6 for plausible but lightly supported synthesis
  - 0.7-0.85 for strong patterns supported by multiple specific memories
  - 0.9+ only when the observation is nearly a direct restatement of a repeated, explicit pattern with little ambiguity
  - Avoid 1.0 unless the observation is overwhelmingly and redundantly supported by the source memories
- Each `source_memory_ids` list must only contain IDs from the provided source memories
- If the memories do not support a meaningful observation, return {"observations": []}
"""


def _extract_json_object(raw_text: str) -> str:
    cleaned = raw_text.strip()
    if not cleaned:
        return ""
    if cleaned.startswith("```"):
        lines = [
            line for line in cleaned.splitlines() if not line.strip().startswith("```")
        ]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return ""
    return cleaned[start : end + 1]


def _normalize_user_observation(text: str) -> str:
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return ""
    if cleaned.lower().startswith("user "):
        return f"User {cleaned[5:].strip()}"
    if cleaned.lower().startswith("the user "):
        return f"User {cleaned[9:].strip()}"
    return f"User {cleaned[0].lower() + cleaned[1:] if len(cleaned) > 1 else cleaned.lower()}"


def _clamp_confidence(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        confidence = float(value)
    elif isinstance(value, str):
        try:
            confidence = float(value)
        except ValueError:
            return None
    else:
        return None
    return max(0.0, min(1.0, confidence))


def _slot_family(memory_slot: object) -> str | None:
    if not isinstance(memory_slot, str):
        return None
    cleaned = memory_slot.strip().lower()
    if not cleaned:
        return None
    parts = cleaned.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return parts[0]


def _extract_content(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if choices:
        first_choice = choices[0]
        message = getattr(first_choice, "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(first_choice, dict):
            message_dict = first_choice.get("message")
            if isinstance(message_dict, dict) and isinstance(
                message_dict.get("content"), str
            ):
                return message_dict["content"]

    for method_name in ("model_dump", "dict"):
        method = getattr(response, method_name, None)
        if not callable(method):
            continue
        try:
            data = method()
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        choices_data = data.get("choices")
        if not isinstance(choices_data, list) or not choices_data:
            continue
        first_choice = choices_data[0]
        if not isinstance(first_choice, dict):
            continue
        message = first_choice.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content

    return ""


def _normalize_observations(
    raw_text: str,
    valid_source_memory_ids: set[str],
) -> list[dict[str, Any]]:
    payload_text = _extract_json_object(raw_text)
    if not payload_text:
        return []
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []

    raw_observations = payload.get("observations")
    if not isinstance(raw_observations, list):
        return []

    normalized: list[dict[str, Any]] = []
    seen_contents: set[str] = set()
    for item in raw_observations:
        if not isinstance(item, dict):
            continue
        content = _normalize_user_observation(str(item.get("content") or ""))
        if len(content) < 10:
            continue
        normalized_key = content.lower()
        if normalized_key in seen_contents:
            continue

        confidence = _clamp_confidence(item.get("confidence"))
        if confidence is None:
            continue

        source_memory_ids_raw = item.get("source_memory_ids")
        if not isinstance(source_memory_ids_raw, list):
            continue
        source_memory_ids = [
            str(memory_id)
            for memory_id in source_memory_ids_raw
            if isinstance(memory_id, str) and memory_id in valid_source_memory_ids
        ]
        if not source_memory_ids:
            continue

        normalized.append(
            {
                "content": content,
                "confidence": confidence,
                "source_memory_ids": source_memory_ids,
            }
        )
        seen_contents.add(normalized_key)
        if len(normalized) >= MAX_DREAM_OBSERVATIONS_PER_FAMILY:
            break

    return normalized


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return None


async def _build_local_store() -> tuple[MemoryStore | None, asyncpg.Pool | None]:
    settings = get_settings()
    if not settings.database_url or not settings.daemon_encryption_key:
        return None, None
    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=1,
        max_size=2,
    )
    store = MemoryStore(pool, ContentEncryption(settings.daemon_encryption_key))
    return store, pool


def _format_cluster_memories(memories: Iterable[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, memory in enumerate(memories, start=1):
        category = str(memory.get("category") or "fact").strip().lower() or "fact"
        slot = str(memory.get("memory_slot") or "")
        content = str(memory.get("content") or "").strip()
        memory_id = str(memory.get("id") or "")
        if not content:
            continue
        slot_suffix = f" (slot: {slot})" if slot else ""
        lines.append(f"{index}. [id={memory_id}] [{category}] {content}{slot_suffix}")
    return "\n".join(lines)


async def dream_on_cluster(memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not memories:
        return []

    settings = get_settings()
    provider_config = settings.get_provider_config("openrouter")
    prompt = (
        f"{DREAM_SYNTHESIS_PROMPT}\n\n"
        f"Source memories:\n{_format_cluster_memories(memories)}"
    )

    call_params: dict[str, Any] = {
        "model": settings.background_reasoning_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 300,
        "timeout": provider_config.timeout_s,
    }
    if provider_config.base_url:
        call_params["api_base"] = provider_config.base_url
    if provider_config.api_key:
        call_params["api_key"] = provider_config.api_key
    if provider_config.extra_headers:
        call_params["extra_headers"] = provider_config.extra_headers

    response = await litellm.acompletion(**call_params)
    valid_source_memory_ids = {
        str(memory["id"]) for memory in memories if memory.get("id") is not None
    }
    return _normalize_observations(_extract_content(response), valid_source_memory_ids)


async def run_dreaming(
    user_id: uuid.UUID,
    store: MemoryStore | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.dreaming_enabled:
        return {
            "status": "skipped",
            "reason": "dreaming_disabled",
            "user_id": str(user_id),
            "families_processed": 0,
            "observations_created": 0,
            "eligible_families": [],
            "skipped_families": [],
            "observation_memory_ids": [],
        }

    owned_pool: asyncpg.Pool | None = None
    active_store = store
    if active_store is None:
        active_store, owned_pool = await _build_local_store()
    if active_store is None:
        return {
            "status": "skipped",
            "reason": "store_unavailable",
            "user_id": str(user_id),
            "families_processed": 0,
            "observations_created": 0,
            "eligible_families": [],
            "skipped_families": [],
            "observation_memory_ids": [],
        }

    try:
        min_cluster_size = max(1, settings.dream_min_cluster_size)
        candidate_memories = await active_store.get_dream_candidate_memories(user_id)
        families: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for memory in candidate_memories:
            family = _slot_family(memory.get("memory_slot"))
            if family is None:
                continue
            families[family].append(memory)

        sized_families = {
            family: members
            for family, members in families.items()
            if len(members) >= min_cluster_size
        }

        latest_completed_run_at: datetime | None = None
        recent_runs = await active_store.get_dream_runs(user_id, limit=10)
        for run in recent_runs:
            if run.get("status") not in {"completed", "skipped"}:
                continue
            latest_completed_run_at = _coerce_datetime(run.get("run_completed_at"))
            if latest_completed_run_at is not None:
                break

        eligible_families: list[str] = []
        skipped_families: list[str] = []
        families_to_process: dict[str, list[dict[str, Any]]] = {}

        for family, members in sorted(sized_families.items()):
            latest_memory_at = max(
                (
                    timestamp
                    for timestamp in (
                        _coerce_datetime(m.get("created_at")) for m in members
                    )
                    if timestamp is not None
                ),
                default=None,
            )
            if (
                latest_completed_run_at is not None
                and latest_memory_at is not None
                and latest_memory_at <= latest_completed_run_at
            ):
                skipped_families.append(family)
                continue
            eligible_families.append(family)
            families_to_process[family] = members

        if not families_to_process:
            dream_run = await active_store.log_dream_run(
                user_id=user_id,
                status="skipped",
                eligible_families=eligible_families,
                skipped_families=skipped_families,
                families_processed=0,
                observations_created=0,
                observation_memory_ids=[],
                run_completed_at=datetime.now(timezone.utc),
                model_used=settings.background_reasoning_model,
            )
            return {
                "status": "skipped",
                "reason": "no_eligible_families",
                "user_id": str(user_id),
                "families_processed": 0,
                "observations_created": 0,
                "eligible_families": eligible_families,
                "skipped_families": skipped_families,
                "observation_memory_ids": [],
                "dream_run_id": str(dream_run["id"]),
            }

        created_memory_ids: list[uuid.UUID] = []
        family_errors: list[str] = []
        families_processed = 0

        for family, members in families_to_process.items():
            try:
                observations = await dream_on_cluster(members)
                if not observations:
                    skipped_families.append(family)
                    continue

                observation_texts = [
                    str(observation["content"]) for observation in observations
                ]
                embeddings = await embed_documents(observation_texts)

                for observation_payload, embedding in zip(observations, embeddings):
                    observation_text = str(observation_payload["content"])
                    source_memory_ids = [
                        uuid.UUID(source_memory_id)
                        for source_memory_id in observation_payload["source_memory_ids"]
                    ]
                    observation = await active_store.insert_memory(
                        user_id=user_id,
                        content=observation_text,
                        category="observation",
                        source_type="dream",
                        embedding=embedding,
                        embedding_model=settings.embedding_document_model,
                        confidence=float(observation_payload["confidence"]),
                        memory_slot=f"{family}.observation",
                    )
                    observation_id = uuid.UUID(str(observation["id"]))
                    _ = await active_store.update_memory_tier(observation_id, "l1")
                    _ = await active_store.update_memory_metadata(
                        observation_id,
                        {
                            "dream_family": family,
                            "dream_cluster_size": len(members),
                            "source_memory_ids": [
                                str(memory_id) for memory_id in source_memory_ids
                            ],
                            "source_memory_count": len(source_memory_ids),
                        },
                    )
                    created_memory_ids.append(observation_id)

                families_processed += 1
            except Exception as error:
                logger.warning(
                    "Dream synthesis failed for user %s family %s: %s",
                    user_id,
                    family,
                    error,
                    exc_info=True,
                )
                skipped_families.append(family)
                family_errors.append(f"{family}: {error}")

        final_status = "completed" if families_processed > 0 else "skipped"
        dream_run = await active_store.log_dream_run(
            user_id=user_id,
            status=final_status,
            eligible_families=eligible_families,
            skipped_families=skipped_families,
            families_processed=families_processed,
            observations_created=len(created_memory_ids),
            observation_memory_ids=created_memory_ids,
            error_message="; ".join(family_errors) if family_errors else None,
            run_completed_at=datetime.now(timezone.utc),
            model_used=settings.background_reasoning_model,
        )
        return {
            "status": final_status,
            "user_id": str(user_id),
            "families_processed": families_processed,
            "observations_created": len(created_memory_ids),
            "eligible_families": eligible_families,
            "skipped_families": skipped_families,
            "observation_memory_ids": [
                str(memory_id) for memory_id in created_memory_ids
            ],
            "errors": family_errors,
            "dream_run_id": str(dream_run["id"]),
        }
    except Exception as error:
        logger.warning(
            "Dream run failed for user %s: %s", user_id, error, exc_info=True
        )
        try:
            dream_run = await active_store.log_dream_run(
                user_id=user_id,
                status="failed",
                eligible_families=[],
                skipped_families=[],
                families_processed=0,
                observations_created=0,
                observation_memory_ids=[],
                error_message=str(error),
                run_completed_at=datetime.now(timezone.utc),
                model_used=settings.background_reasoning_model,
            )
            dream_run_id = str(dream_run["id"])
        except Exception:
            dream_run_id = None
        return {
            "status": "failed",
            "user_id": str(user_id),
            "families_processed": 0,
            "observations_created": 0,
            "eligible_families": [],
            "skipped_families": [],
            "observation_memory_ids": [],
            "error": str(error),
            "dream_run_id": dream_run_id,
        }
    finally:
        if owned_pool is not None:
            await owned_pool.close()
