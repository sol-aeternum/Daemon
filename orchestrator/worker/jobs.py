from __future__ import annotations

# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportArgumentType=false, reportMissingImports=false

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast, TypedDict
from zoneinfo import ZoneInfo

from arq.connections import ArqRedis
from arq.jobs import Job

from orchestrator.config import Settings
from orchestrator.memory.dreaming import run_dreaming
from orchestrator.memory.entities import (
    extract_and_resolve_entities,
    persist_extraction_result,
)
from orchestrator.memory.extraction import (
    process_extraction,
    messages_to_extraction_text,
)
from orchestrator.memory.store import MemoryStore
from orchestrator.memory.titles import ConversationMessage, generate_conversation_title
from orchestrator.skill_evaluator import (
    SkillEvaluator,
    SkillEvaluationRequest,
    build_skill_evaluation_debounce_key,
)

logger = logging.getLogger(__name__)


WorkerContext = dict[str, object]


class ConsolidationResults(TypedDict):
    """Typed results dict for consolidate_memories job."""

    status: str
    clusters_found: int
    clusters_processed: int
    memories_created: int
    memories_demoted: int
    errors: list[str]
    users_processed: int
    error_count: int


class DreamingResults(TypedDict):
    status: str
    users_processed: int
    dream_runs_completed: int
    dream_runs_skipped: int
    dream_runs_failed: int
    observations_created: int
    errors: list[str]
    error_count: int


class EntityResolutionResults(TypedDict):
    status: str
    memories_processed: int
    entities_created: int
    entities_updated: int
    errors: list[str]
    error_count: int


def _parse_raw_messages(messages_json: object) -> list[dict[str, Any]]:
    parsed: object
    if isinstance(messages_json, str):
        try:
            parsed = cast(object, json.loads(messages_json))
        except json.JSONDecodeError:
            return []
    else:
        parsed = messages_json

    if not isinstance(parsed, list):
        return []

    raw_messages: list[dict[str, Any]] = []
    for item in cast(list[object], parsed):
        if isinstance(item, dict):
            raw_messages.append(item)
    return raw_messages


def _contains_memory_write_marker(value: object) -> bool:
    if isinstance(value, str):
        return "memory_write" in value.lower()
    if isinstance(value, dict):
        return any(_contains_memory_write_marker(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_memory_write_marker(v) for v in value)
    return False


def _is_memory_write_artifact(message: dict[str, Any]) -> bool:
    tool_calls = message.get("tool_calls")
    if _contains_memory_write_marker(tool_calls):
        return True

    tool_results = message.get("tool_results")
    if _contains_memory_write_marker(tool_results):
        return True

    role = str(message.get("role") or "").lower()
    if role == "tool" and _contains_memory_write_marker(message.get("content")):
        return True

    return False


def _parse_messages(messages_json: object) -> list[ConversationMessage]:
    messages: list[ConversationMessage] = []
    for item in _parse_raw_messages(messages_json):
        role = item.get("role")
        content = item.get("content")
        if role is None or content is None:
            continue
        messages.append({"role": str(role), "content": str(content)})
    return messages


async def enqueue_with_debounce(
    queue: ArqRedis,
    job_name: str,
    job_id: str,
    defer_by: timedelta | None = None,
    args: Sequence[object] = (),
    kwargs: Mapping[str, object] | None = None,
) -> Job | None:
    delay = defer_by or timedelta(seconds=30)
    return await queue.enqueue_job(
        job_name,
        *args,
        _job_id=job_id,
        _defer_by=delay,
        **dict(kwargs or {}),
    )


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(value)


def _extract_timezone_name(user_settings: dict[str, Any]) -> str | None:
    candidates = [
        user_settings.get("timezone"),
        user_settings.get("time_zone"),
    ]

    preferences = user_settings.get("preferences")
    if isinstance(preferences, dict):
        candidates.extend(
            [
                preferences.get("timezone"),
                preferences.get("time_zone"),
            ]
        )

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


async def _user_matches_dream_schedule_hour(
    store: MemoryStore,
    user_id: uuid.UUID,
    target_hour: int,
    now_utc: datetime | None = None,
) -> bool:
    current_utc = now_utc or datetime.now(timezone.utc)
    settings = await store.get_user_settings(user_id)
    timezone_name = _extract_timezone_name(settings)
    if not timezone_name:
        return True

    try:
        user_now = current_utc.astimezone(ZoneInfo(timezone_name))
    except Exception:
        logger.warning(
            "Invalid user timezone for dreaming schedule; falling back to server schedule",
            extra={"user_id": str(user_id), "timezone": timezone_name},
        )
        return True

    return user_now.hour == target_hour


async def extract_memories(
    ctx: WorkerContext,
    user_id: str | uuid.UUID,
    conversation_id: str | uuid.UUID,
    messages_json: object | None = None,
) -> dict[str, object]:
    store_obj = ctx.get("store")
    if not isinstance(store_obj, MemoryStore):
        return {"status": "skipped", "reason": "store_unavailable"}

    raw_messages: list[dict[str, Any]]
    if messages_json is None:
        # Get the last extraction time for this conversation
        last_extraction_time = await store_obj.get_last_extraction_time(
            _as_uuid(conversation_id)
        )

        # If there was a previous extraction, only fetch messages newer than that
        if last_extraction_time is not None:
            # We need to fetch messages with created_at > last_extraction_time
            # Since get_messages doesn't support this filter, we'll need to modify the approach
            # For now, we'll fetch the last 250 messages and filter them
            all_messages = await store_obj.get_messages(
                _as_uuid(conversation_id), limit=250
            )
            messages = [
                msg
                for msg in all_messages
                if msg.get("created_at") and msg["created_at"] > last_extraction_time
            ]
        else:
            # No previous extraction, fetch all messages
            messages = await store_obj.get_messages(
                _as_uuid(conversation_id), limit=250
            )
        raw_messages = [dict(message) for message in messages]
    else:
        raw_messages = _parse_raw_messages(messages_json)
    filtered_raw_messages = [
        message for message in raw_messages if not _is_memory_write_artifact(message)
    ]
    parsed_messages = _parse_messages(filtered_raw_messages)
    messages = parsed_messages
    text = messages_to_extraction_text(messages)
    if not text:
        return {"status": "skipped", "reason": "no_messages"}

    extraction_success, new_memories = await process_extraction(
        store=store_obj,
        user_id=_as_uuid(user_id),
        conversation_id=_as_uuid(conversation_id),
        text=text,
    )

    if extraction_success and new_memories:
        memory_ids = [str(m.get("id")) for m in new_memories if m.get("id")]
        if memory_ids:
            try:
                queue = ctx.get("redis")
                if isinstance(queue, ArqRedis):
                    await enqueue_with_debounce(
                        queue,
                        "resolve_entities_job",
                        job_id=f"resolve_entities_{_as_uuid(user_id)}_{_as_uuid(conversation_id)}",
                        args=(),
                        kwargs={
                            "user_id": str(_as_uuid(user_id)),
                            "memory_ids_json": json.dumps(memory_ids),
                        },
                    )
            except Exception:
                logger.warning(
                    "Failed to enqueue entity resolution job for user %s conversation %s",
                    user_id,
                    conversation_id,
                )

    return {"status": "ok", "processed_messages": len(messages)}


async def generate_title(
    ctx: WorkerContext,
    conversation_id: str | uuid.UUID,
    user_message_text: str,
) -> str | None:
    if not user_message_text:
        return None

    messages = [{"role": "user", "content": user_message_text}]

    store_obj = ctx.get("store")
    if isinstance(store_obj, MemoryStore):
        try:
            existing = await store_obj.get_conversation(_as_uuid(conversation_id))
            if existing and bool(existing.get("title_locked")):
                return None
        except Exception:
            logger.warning("Failed to check title lock", exc_info=True)

    settings_obj = ctx.get("settings")
    settings = settings_obj if isinstance(settings_obj, Settings) else None
    title_model = (
        settings.title_model if settings else None
    ) or "openrouter/openai/gpt-4o-mini"

    title = await generate_conversation_title(messages, model=title_model)
    if isinstance(store_obj, MemoryStore):
        try:
            _ = await store_obj.update_conversation(
                _as_uuid(conversation_id), title=title
            )
        except Exception:
            logger.warning("Failed to persist conversation title", exc_info=True)

    return title


async def generate_conversation_title_job(
    ctx: WorkerContext,
    conversation_id: str | uuid.UUID,
) -> dict[str, object]:
    store_obj = ctx.get("store")
    if not isinstance(store_obj, MemoryStore):
        return {"status": "skipped", "reason": "store_unavailable"}

    conv_id = _as_uuid(conversation_id)
    conversation = await store_obj.get_conversation(conv_id)
    if not conversation:
        return {"status": "not_found"}
    if bool(conversation.get("title_locked")):
        return {"status": "skipped", "reason": "title_locked"}

    messages_raw = await store_obj.get_messages(conv_id, limit=50)
    messages: list[ConversationMessage] = []
    for msg in messages_raw:
        role = msg.get("role")
        content = msg.get("content")
        if role not in {"user", "assistant"}:
            continue
        if content is None:
            continue
        content_str = str(content).strip()
        if not content_str:
            continue
        messages.append({"role": str(role), "content": content_str})

    if not messages:
        return {"status": "skipped", "reason": "no_messages"}

    settings_obj = ctx.get("settings")
    settings = settings_obj if isinstance(settings_obj, Settings) else None
    title_model = (
        settings.title_model if settings else None
    ) or "openrouter/openai/gpt-4o-mini"
    title = await generate_conversation_title(messages, model=title_model)

    try:
        _ = await store_obj.update_conversation(conv_id, title=title)
    except Exception:
        logger.warning("Failed to persist conversation title", exc_info=True)
        return {"status": "error", "reason": "persist_failed"}

    return {"status": "ok", "title": title}


async def generate_summary_job(
    ctx: WorkerContext,
    conversation_id: str,
) -> dict[str, Any]:
    """Generate and store conversation summary."""
    from orchestrator.memory.summarization import should_summarize, generate_summary

    store_obj = ctx.get("store")
    if not isinstance(store_obj, MemoryStore):
        return {"status": "skipped", "reason": "store_unavailable"}

    store = store_obj
    conv_id = uuid.UUID(conversation_id)

    conversation = await store.get_conversation(conv_id)
    if not conversation:
        return {"status": "not_found"}

    last_summary_time = conversation.get("summary_updated_at")
    settings = {}

    if not await should_summarize(conv_id, last_summary_time, store, settings):
        return {"status": "skipped", "reason": "thresholds_not_met"}

    messages = await store.get_messages(conv_id, limit=100)
    previous_summary = conversation.get("summary")

    summary = await generate_summary(messages, previous_summary, settings)
    await store.update_conversation(conv_id, summary=summary)

    return {"status": "success", "summary_length": len(summary)}


async def garbage_collect(ctx: WorkerContext) -> dict[str, int]:
    store_obj = ctx.get("store")
    if not isinstance(store_obj, MemoryStore):
        return {"scanned": 0, "deleted": 0}

    async with store_obj._pool.acquire() as conn:
        scanned = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM memories
            WHERE (status = 'inactive' AND updated_at < NOW() - INTERVAL '90 days')
               OR (status = 'rejected' AND updated_at < NOW() - INTERVAL '30 days')
               OR (status = 'pending' AND updated_at < NOW() - INTERVAL '30 days')
               OR (status = 'deleted' AND updated_at < NOW() - INTERVAL '30 days')
            """
        )

        result = await conn.execute(
            """
            DELETE FROM memories
            WHERE (status = 'inactive' AND updated_at < NOW() - INTERVAL '90 days')
               OR (status = 'rejected' AND updated_at < NOW() - INTERVAL '30 days')
               OR (status = 'pending' AND updated_at < NOW() - INTERVAL '30 days')
               OR (status = 'deleted' AND updated_at < NOW() - INTERVAL '30 days')
            """
        )

    deleted = int(result.split()[-1]) if result else 0
    return {"scanned": int(scanned or 0), "deleted": deleted}


async def cleanup_generated_files(ctx: WorkerContext) -> dict[str, int]:
    """Delete generated files older than 24 hours."""
    from orchestrator.config import get_settings

    settings = get_settings()
    generated_files_dir = (
        Path(__file__).resolve().parent.parent / "data" / "generated_files"
    )

    if not generated_files_dir.exists():
        return {"scanned": 0, "deleted": 0}

    cutoff = datetime.now() - timedelta(hours=24)
    deleted = 0
    scanned = 0

    for item in generated_files_dir.iterdir():
        scanned += 1
        # Only delete files, not directories
        if item.is_file():
            try:
                mtime = datetime.fromtimestamp(item.stat().st_mtime)
                if mtime < cutoff:
                    item.unlink()
                    deleted += 1
                    logger.info(f"Deleted old generated file: {item.name}")
            except Exception as e:
                logger.warning(f"Failed to process {item.name}: {e}")

    return {"scanned": scanned, "deleted": deleted}


async def cleanup_generated_images(ctx: WorkerContext) -> dict[str, int]:
    _ = ctx
    generated_images_dir = (
        Path(__file__).resolve().parent.parent.parent / "data" / "generated_images"
    )

    if not generated_images_dir.exists():
        return {"scanned": 0, "deleted": 0}

    cutoff = datetime.now() - timedelta(hours=24)
    deleted = 0
    scanned = 0

    for item in generated_images_dir.iterdir():
        scanned += 1
        if item.is_file():
            try:
                mtime = datetime.fromtimestamp(item.stat().st_mtime)
                if mtime < cutoff:
                    item.unlink()
                    deleted += 1
                    logger.info(f"Deleted old generated image artifact: {item.name}")
            except Exception as e:
                logger.warning(
                    f"Failed to process generated image artifact {item.name}: {e}"
                )

    return {"scanned": scanned, "deleted": deleted}


async def run_dreaming_job(
    ctx: WorkerContext,
    user_id: str | uuid.UUID | None = None,
    *,
    scheduled: bool = False,
    now_utc: datetime | None = None,
) -> DreamingResults:
    store_obj = ctx.get("store")
    settings_obj = ctx.get("settings")

    if not isinstance(store_obj, MemoryStore):
        return DreamingResults(
            status="skipped",
            users_processed=0,
            dream_runs_completed=0,
            dream_runs_skipped=0,
            dream_runs_failed=0,
            observations_created=0,
            errors=["store_unavailable"],
            error_count=1,
        )

    if not isinstance(settings_obj, Settings):
        return DreamingResults(
            status="skipped",
            users_processed=0,
            dream_runs_completed=0,
            dream_runs_skipped=0,
            dream_runs_failed=0,
            observations_created=0,
            errors=["settings_unavailable"],
            error_count=1,
        )

    if not settings_obj.dreaming_enabled:
        return DreamingResults(
            status="skipped",
            users_processed=0,
            dream_runs_completed=0,
            dream_runs_skipped=0,
            dream_runs_failed=0,
            observations_created=0,
            errors=["dreaming_disabled"],
            error_count=1,
        )

    if user_id is not None:
        try:
            user_ids = [_as_uuid(user_id)]
        except ValueError as error:
            return DreamingResults(
                status="error",
                users_processed=0,
                dream_runs_completed=0,
                dream_runs_skipped=0,
                dream_runs_failed=0,
                observations_created=0,
                errors=[f"invalid_user_id: {error}"],
                error_count=1,
            )
    else:
        user_ids = await store_obj.get_users_with_dream_candidates()

    results: DreamingResults = {
        "status": "ok",
        "users_processed": 0,
        "dream_runs_completed": 0,
        "dream_runs_skipped": 0,
        "dream_runs_failed": 0,
        "observations_created": 0,
        "errors": [],
        "error_count": 0,
    }

    for uid in user_ids:
        try:
            if scheduled and not await _user_matches_dream_schedule_hour(
                store_obj,
                uid,
                settings_obj.dream_schedule_hour,
                now_utc,
            ):
                continue

            dream_result = await run_dreaming(uid, store=store_obj)
            results["users_processed"] += 1
            results["observations_created"] += int(
                dream_result.get("observations_created", 0) or 0
            )

            status = str(dream_result.get("status") or "").lower()
            if status == "completed":
                results["dream_runs_completed"] += 1
            elif status == "failed":
                results["dream_runs_failed"] += 1
                error_text = str(dream_result.get("error") or "dream_run_failed")
                results["errors"].append(f"{uid}: {error_text}")
            else:
                results["dream_runs_skipped"] += 1
        except Exception as error:
            logger.warning(
                "Dreaming job failed for user %s: %s", uid, error, exc_info=True
            )
            results["users_processed"] += 1
            results["dream_runs_failed"] += 1
            results["errors"].append(f"{uid}: {error}")

    if results["dream_runs_failed"] and not results["dream_runs_completed"]:
        results["status"] = "error"
    elif not user_ids:
        results["status"] = "skipped"

    results["error_count"] = len(results["errors"])
    return results


async def run_scheduled_dreaming_job(ctx: WorkerContext) -> DreamingResults:
    return await run_dreaming_job(ctx, scheduled=True)


async def consolidate_memories(
    ctx: WorkerContext,
    user_id: str | uuid.UUID | None = None,
) -> ConsolidationResults:
    """Run memory consolidation for a user or all users.

    Finds clusters of related L1 memories and consolidates them into
    summary memories. Source memories are demoted to L2 (not deleted).

    This job is interruptible - each cluster is processed independently,
    so partial failures don't lose progress on other clusters.

    Args:
        ctx: Worker context with store and settings
        user_id: Optional specific user to consolidate. If None, processes
                 all users with eligible memories.

    Returns:
        ConsolidationResults with counts and status
    """
    from orchestrator.memory.consolidation import (
        find_memory_clusters,
        consolidate_cluster,
        MemoryCluster,
    )

    store_obj = ctx.get("store")
    settings_obj = ctx.get("settings")

    if not isinstance(store_obj, MemoryStore):
        return ConsolidationResults(
            status="skipped",
            clusters_found=0,
            clusters_processed=0,
            memories_created=0,
            memories_demoted=0,
            errors=["store_unavailable"],
            users_processed=0,
            error_count=1,
        )

    if not isinstance(settings_obj, Settings):
        return ConsolidationResults(
            status="skipped",
            clusters_found=0,
            clusters_processed=0,
            memories_created=0,
            memories_demoted=0,
            errors=["settings_unavailable"],
            users_processed=0,
            error_count=1,
        )

    # Check if consolidation is enabled
    if not settings_obj.consolidation_enabled:
        logger.info("Memory consolidation is disabled via config")
        return ConsolidationResults(
            status="skipped",
            clusters_found=0,
            clusters_processed=0,
            memories_created=0,
            memories_demoted=0,
            errors=["consolidation_disabled"],
            users_processed=0,
            error_count=1,
        )

    store = store_obj
    results: ConsolidationResults = {
        "status": "ok",
        "clusters_found": 0,
        "clusters_processed": 0,
        "memories_created": 0,
        "memories_demoted": 0,
        "errors": [],
        "users_processed": 0,
        "error_count": 0,
    }

    # Get list of users to process
    user_ids: list[uuid.UUID] = []

    if user_id is not None:
        # Single user mode (manual trigger)
        try:
            user_ids = [_as_uuid(user_id)]
        except ValueError as e:
            results["status"] = "error"
            results["errors"].append(f"invalid_user_id: {e}")
            results["error_count"] = 1
    else:
        # Periodic job - find all users with eligible L1 memories
        rows = await store._pool.fetch(
            """
            SELECT DISTINCT user_id
            FROM memories
            WHERE status = 'active'
              AND tier = 'l1'
              AND embedding IS NOT NULL
            """
        )
        user_ids = [row["user_id"] for row in rows]
        logger.info(
            f"Found {len(user_ids)} users with eligible memories for consolidation"
        )

    # Process each user
    for uid in user_ids:
        try:
            clusters = await find_memory_clusters(uid, store)
            results["clusters_found"] += len(clusters)

            for cluster in clusters:
                try:
                    if not isinstance(cluster, MemoryCluster):
                        logger.warning(
                            f"Invalid cluster type for user {uid}: {type(cluster)}"
                        )
                        continue

                    if len(cluster) < 3:
                        logger.debug(
                            f"Cluster too small, skipping: {len(cluster)} members"
                        )
                        continue

                    created = await consolidate_cluster(cluster, store, uid)

                    if created:
                        results["clusters_processed"] += 1
                        results["memories_created"] += len(created)
                        results["memories_demoted"] += len(cluster.members)
                        logger.info(
                            f"Consolidated cluster for user {uid}: "
                            f"created {len(created)} summaries from {len(cluster.members)} sources"
                        )
                    else:
                        logger.warning(
                            f"Consolidation produced no memories for cluster in user {uid}"
                        )

                except Exception as e:
                    error_msg = f"Cluster consolidation failed for user {uid}: {e}"
                    logger.warning(error_msg, exc_info=True)
                    results["errors"].append(error_msg)

            results["users_processed"] += 1

        except Exception as e:
            error_msg = f"User consolidation failed for {uid}: {e}"
            logger.warning(error_msg, exc_info=True)
            results["errors"].append(error_msg)

    # Summary logging
    logger.info(
        f"Consolidation complete: {results['clusters_processed']}/{results['clusters_found']} "
        f"clusters processed, {results['memories_created']} memories created, "
        f"{results['memories_demoted']} sources demoted, {len(results['errors'])} errors"
    )

    results["error_count"] = len(results["errors"])

    return results


class SkillEvaluationJobResult(TypedDict):
    """Typed results dict for run_skill_evaluation_job."""

    status: str
    classification: str
    tool_call_count: int
    created_skill_id: str | None
    patched_skill_id: str | None
    matched_skill_id: str | None
    matched_similarity: float | None
    matched_source_type: str | None
    protected: bool
    trigger_conditions: str | None
    complexity_origin: int | None
    reason: str | None
    errors: list[str]
    error_count: int


async def run_skill_evaluation_job(
    ctx: WorkerContext,
    user_id: str | uuid.UUID,
    conversation_id: str | uuid.UUID,
    assistant_message_id: str | uuid.UUID,
    tool_call_count: int,
) -> SkillEvaluationJobResult:
    store_obj = ctx.get("store")
    db_pool = ctx.get("db_pool")

    if not isinstance(store_obj, MemoryStore):
        return SkillEvaluationJobResult(
            status="skipped",
            classification="skipped_store_unavailable",
            tool_call_count=tool_call_count,
            created_skill_id=None,
            patched_skill_id=None,
            matched_skill_id=None,
            matched_similarity=None,
            matched_source_type=None,
            protected=False,
            trigger_conditions=None,
            complexity_origin=None,
            reason="memory store unavailable",
            errors=["store_unavailable"],
            error_count=1,
        )

    evaluator = SkillEvaluator(store=store_obj, db_pool=db_pool)

    request = SkillEvaluationRequest(
        user_id=_as_uuid(user_id),
        conversation_id=_as_uuid(conversation_id),
        assistant_message_id=_as_uuid(assistant_message_id),
        tool_call_count=tool_call_count,
    )

    try:
        result = await evaluator.evaluate_completed_turn(request)

        return SkillEvaluationJobResult(
            status="ok",
            classification=result.classification,
            tool_call_count=result.tool_call_count,
            created_skill_id=result.created_skill_id,
            patched_skill_id=result.patched_skill_id,
            matched_skill_id=result.matched_skill_id,
            matched_similarity=result.matched_similarity,
            matched_source_type=result.matched_source_type,
            protected=result.protected,
            trigger_conditions=result.trigger_conditions,
            complexity_origin=result.complexity_origin,
            reason=result.reason,
            errors=[],
            error_count=0,
        )
    except Exception as e:
        logger.warning(
            "Skill evaluation job failed for user %s conversation %s: %s",
            user_id,
            conversation_id,
            e,
            exc_info=True,
        )
        return SkillEvaluationJobResult(
            status="error",
            classification="error",
            tool_call_count=tool_call_count,
            created_skill_id=None,
            patched_skill_id=None,
            matched_skill_id=None,
            matched_similarity=None,
            matched_source_type=None,
            protected=False,
            trigger_conditions=None,
            complexity_origin=None,
            reason=str(e),
            errors=[str(e)],
            error_count=1,
        )


async def resolve_entities_job(
    ctx: WorkerContext,
    user_id: str | uuid.UUID,
    memory_ids_json: str,
) -> EntityResolutionResults:
    """Run entity resolution for newly created memories.

    This job is best-effort - failures are logged but don't block
    memory creation or extraction completion.

    Args:
        ctx: Worker context with store
        user_id: User ID
        memory_ids_json: JSON-serialized list of memory ID strings

    Returns:
        EntityResolutionResults with counts and status
    """
    store_obj = ctx.get("store")
    if not isinstance(store_obj, MemoryStore):
        return EntityResolutionResults(
            status="skipped",
            memories_processed=0,
            entities_created=0,
            entities_updated=0,
            errors=["store_unavailable"],
            error_count=1,
        )

    try:
        memory_ids = json.loads(memory_ids_json)
        if not isinstance(memory_ids, list):
            memory_ids = []
    except (json.JSONDecodeError, TypeError):
        return EntityResolutionResults(
            status="error",
            memories_processed=0,
            entities_created=0,
            entities_updated=0,
            errors=["invalid_memory_ids_json"],
            error_count=1,
        )

    uid = _as_uuid(user_id)
    results: EntityResolutionResults = {
        "status": "ok",
        "memories_processed": 0,
        "entities_created": 0,
        "entities_updated": 0,
        "errors": [],
        "error_count": 0,
    }

    memory_contents: list[tuple[str, uuid.UUID | None, str | None]] = []
    for mid in memory_ids:
        try:
            memory_uuid = uuid.UUID(mid) if isinstance(mid, str) else mid
        except (ValueError, TypeError):
            continue

        try:
            memory = await store_obj.get_memory(memory_uuid)
            if memory and memory.get("content"):
                content = memory.get("content", "")
                slot = memory.get("memory_slot")
                memory_contents.append((content, memory_uuid, slot))
        except Exception as e:
            logger.warning(
                "Failed to fetch memory %s for entity resolution: %s", memory_uuid, e
            )
            continue

    if not memory_contents:
        return EntityResolutionResults(
            status="ok",
            memories_processed=0,
            entities_created=0,
            entities_updated=0,
            errors=[],
            error_count=0,
        )

    try:
        extraction_result = await extract_and_resolve_entities(
            uid, store_obj, memory_contents, use_spacy=False
        )

        if extraction_result.resolutions:
            entity_ids = await persist_extraction_result(
                uid, store_obj, extraction_result
            )
            results["entities_created"] = len(entity_ids)
            results["entities_updated"] = len([e for e in entity_ids if e is not None])

        results["memories_processed"] = len(memory_contents)

    except Exception as e:
        logger.warning(
            "Entity resolution failed for user %s: %s", uid, e, exc_info=True
        )
        results["status"] = "error"
        results["errors"].append(f"entity_resolution_failed: {e}")
        results["error_count"] = len(results["errors"])

    return results


class ConsolidationNudgeAction(TypedDict):
    action_type: str
    skill_id: str | None
    target_skill_id: str | None
    reason: str
    similarity: float | None
    status: str


class ConsolidationNudgeResults(TypedDict):
    status: str
    user_id: str | None
    actions: list[ConsolidationNudgeAction]
    skills_reviewed: int
    duplicates_found: int
    duplicates_merged: int
    stale_flagged: int
    errors: list[str]
    error_count: int


def _build_consolidation_nudge_debounce_key(user_id: str | uuid.UUID) -> str:
    return f"consolidation_nudge:{user_id}"


async def run_consolidation_nudge_job(
    ctx: WorkerContext,
    user_id: str | uuid.UUID | None = None,
) -> ConsolidationNudgeResults:
    from orchestrator.memory.embedding import embed_query

    store_obj = ctx.get("store")
    settings_obj = ctx.get("settings")
    db_pool = ctx.get("db_pool")

    if not isinstance(store_obj, MemoryStore):
        return ConsolidationNudgeResults(
            status="skipped",
            user_id=str(user_id) if user_id else None,
            actions=[],
            skills_reviewed=0,
            duplicates_found=0,
            duplicates_merged=0,
            stale_flagged=0,
            errors=["store_unavailable"],
            error_count=1,
        )

    if not isinstance(settings_obj, Settings):
        return ConsolidationNudgeResults(
            status="skipped",
            user_id=str(user_id) if user_id else None,
            actions=[],
            skills_reviewed=0,
            duplicates_found=0,
            duplicates_merged=0,
            stale_flagged=0,
            errors=["settings_unavailable"],
            error_count=1,
        )

    interval = getattr(settings_obj, "consolidation_nudge_conversation_interval", 15)
    stale_days = getattr(settings_obj, "consolidation_nudge_stale_days", 30)
    min_skills = getattr(settings_obj, "consolidation_nudge_min_skills", 3)

    if user_id is not None:
        user_ids = [_as_uuid(user_id)]
    else:
        user_ids = await store_obj.get_users_with_skill_candidates(interval)

    results: ConsolidationNudgeResults = {
        "status": "ok",
        "user_id": None,
        "actions": [],
        "skills_reviewed": 0,
        "duplicates_found": 0,
        "duplicates_merged": 0,
        "stale_flagged": 0,
        "errors": [],
        "error_count": 0,
    }

    for uid in user_ids:
        try:
            user_result = await _process_user_consolidation_nudge(
                uid, store_obj, interval, stale_days, min_skills
            )
            results["user_id"] = str(uid)
            results["skills_reviewed"] += user_result["skills_reviewed"]
            results["duplicates_found"] += user_result["duplicates_found"]
            results["duplicates_merged"] += user_result["duplicates_merged"]
            results["stale_flagged"] += user_result["stale_flagged"]
            results["actions"].extend(user_result["actions"])
            results["errors"].extend(user_result["errors"])
        except Exception as e:
            error_msg = f"User {uid} consolidation nudge failed: {e}"
            logger.warning(error_msg, exc_info=True)
            results["errors"].append(error_msg)

    results["error_count"] = len(results["errors"])
    if results["errors"]:
        results["status"] = "error"

    return results


async def _process_user_consolidation_nudge(
    user_id: uuid.UUID,
    store: MemoryStore,
    interval: int,
    stale_days: int,
    min_skills: int,
) -> dict[str, Any]:
    from orchestrator.consolidation_nudge_prompts import (
        build_consolidation_nudge_prompt,
        parse_consolidation_actions,
    )

    actions: list[ConsolidationNudgeAction] = []
    errors: list[str] = []
    skills_reviewed = 0
    duplicates_found = 0
    duplicates_merged = 0
    stale_flagged = 0

    conversation_delta = await store.get_user_conversation_count_since_last_nudge(
        user_id
    )
    if conversation_delta < interval:
        return {
            "skills_reviewed": 0,
            "duplicates_found": 0,
            "duplicates_merged": 0,
            "stale_flagged": 0,
            "actions": [],
            "errors": [],
        }

    total_conversations = await store.get_total_conversation_count(user_id)

    autonomous_skills = await store.get_autonomous_skill_candidates(min_skills)
    if not autonomous_skills:
        await store.record_consolidation_nudge_run(user_id, total_conversations)
        return {
            "skills_reviewed": 0,
            "duplicates_found": 0,
            "duplicates_merged": 0,
            "stale_flagged": 0,
            "actions": [],
            "errors": [],
        }

    skills_reviewed = len(autonomous_skills)

    recent_memories = await store.get_recent_memories_for_user(user_id, limit=20)

    prompt = build_consolidation_nudge_prompt(
        autonomous_skills=autonomous_skills,
        recent_memories=recent_memories,
        user_context=None,
    )

    model_actions = await _call_consolidation_model(prompt)

    autonomous_skill_ids = {s["skill_id"] for s in autonomous_skills}

    for action in model_actions:
        action_type = action.get("type", "")
        skill_id = action.get("skill_id")

        if not skill_id:
            continue

        if action_type == "merge":
            duplicates_found += 1

        if action_type in {"merge", "delete"}:
            if skill_id not in autonomous_skill_ids:
                actions.append(
                    ConsolidationNudgeAction(
                        action_type=action_type,
                        skill_id=skill_id,
                        target_skill_id=action.get("target_skill_id"),
                        reason=f"skip: {action.get('reason', 'skill is not autonomous')}",
                        similarity=action.get("similarity"),
                        status="skipped",
                    )
                )
                continue

        if action_type == "merge":
            target_id = action.get("target_skill_id")
            if target_id and target_id in autonomous_skill_ids:
                merge_result = await _apply_merge_action(
                    skill_id, target_id, store, user_id
                )
                if merge_result["merged"]:
                    duplicates_merged += 1
                    actions.append(
                        ConsolidationNudgeAction(
                            action_type="merge",
                            skill_id=skill_id,
                            target_skill_id=target_id,
                            reason=action.get("reason", "model-driven merge"),
                            similarity=action.get("similarity"),
                            status="applied",
                        )
                    )
                    actions.append(
                        ConsolidationNudgeAction(
                            action_type="delete",
                            skill_id=target_id,
                            target_skill_id=None,
                            reason=f"merged_into {skill_id}",
                            similarity=None,
                            status="applied",
                        )
                    )
                else:
                    actions.append(
                        ConsolidationNudgeAction(
                            action_type="merge",
                            skill_id=skill_id,
                            target_skill_id=target_id,
                            reason=merge_result.get("reason", "merge failed"),
                            similarity=action.get("similarity"),
                            status="skipped",
                        )
                    )
            else:
                actions.append(
                    ConsolidationNudgeAction(
                        action_type="merge",
                        skill_id=skill_id,
                        target_skill_id=target_id,
                        reason="skip: target not autonomous or not found",
                        similarity=action.get("similarity"),
                        status="skipped",
                    )
                )

        elif action_type == "delete":
            if skill_id in autonomous_skill_ids:
                delete_result = await _apply_delete_action(skill_id, store, user_id)
                if delete_result["deleted"]:
                    actions.append(
                        ConsolidationNudgeAction(
                            action_type="delete",
                            skill_id=skill_id,
                            target_skill_id=None,
                            reason=action.get("reason", "model-driven delete"),
                            similarity=None,
                            status="applied",
                        )
                    )
                else:
                    actions.append(
                        ConsolidationNudgeAction(
                            action_type="delete",
                            skill_id=skill_id,
                            target_skill_id=None,
                            reason=delete_result.get("reason", "delete failed"),
                            similarity=None,
                            status="skipped",
                        )
                    )

        elif action_type == "flag_stale":
            if skill_id in autonomous_skill_ids:
                stale_flagged += 1
                actions.append(
                    ConsolidationNudgeAction(
                        action_type="flag_stale",
                        skill_id=skill_id,
                        target_skill_id=None,
                        reason=action.get("reason", f"stale by model assessment"),
                        similarity=None,
                        status="recorded",
                    )
                )

    run_id = uuid.uuid4()
    await store.record_consolidation_nudge_run(user_id, total_conversations)

    for action in actions:
        skill_name = None
        skill_desc = None
        skill_use_count = None
        skill_last_used = None
        for s in autonomous_skills:
            if s["skill_id"] == action["skill_id"]:
                skill_name = s.get("name")
                skill_desc = s.get("description")
                skill_use_count = s.get("use_count")
                skill_last_used = s.get("last_used_at")
                break
        await store.log_consolidation_nudge_action(
            user_id=user_id,
            run_id=run_id,
            action_type=action["action_type"],
            skill_id=action["skill_id"],
            target_skill_id=action.get("target_skill_id"),
            reason=action.get("reason", ""),
            similarity=action.get("similarity"),
            status=action["status"],
            skill_name=skill_name,
            skill_description=skill_desc,
            skill_use_count=skill_use_count,
            skill_last_used_at=skill_last_used,
        )

    return {
        "skills_reviewed": skills_reviewed,
        "duplicates_found": duplicates_found,
        "duplicates_merged": duplicates_merged,
        "stale_flagged": stale_flagged,
        "actions": actions,
        "errors": errors,
    }


async def _call_consolidation_model(prompt: str) -> list[dict[str, Any]]:
    from orchestrator.config import get_settings
    import litellm

    settings = get_settings()
    provider_config = settings.get_provider_config("openrouter")
    model = settings.auto_fast_model
    if not model.startswith("openrouter/"):
        model = f"openrouter/{model}"

    call_params: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a skill consolidation analyst. Return valid JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 2000,
        "timeout": provider_config.timeout_s,
        "response_format": {"type": "json_object"},
    }
    if provider_config.base_url:
        call_params["api_base"] = provider_config.base_url
    if provider_config.api_key:
        call_params["api_key"] = provider_config.api_key
    if provider_config.extra_headers:
        call_params["extra_headers"] = provider_config.extra_headers

    try:
        response = await litellm.acompletion(**call_params)
        content = _extract_response_content(response)
        if content:
            from orchestrator.consolidation_nudge_prompts import (
                parse_consolidation_actions,
            )

            return parse_consolidation_actions(content)
    except Exception:
        pass
    return []


def _extract_response_content(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        choice0 = choices[0]
        if isinstance(choice0, dict):
            message = choice0.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
        else:
            message = getattr(choice0, "message", None)
            content = getattr(message, "content", None)
            if isinstance(content, str):
                return content
    return ""


async def _apply_merge_action(
    skill_id: str,
    target_skill_id: str,
    store: MemoryStore,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    try:
        await store.merge_autonomous_skills(
            kept_skill_id=skill_id,
            absorbed_skill_ids=[target_skill_id],
            user_id=user_id,
        )
        return {"merged": True, "reason": "ok"}
    except Exception as e:
        return {"merged": False, "reason": str(e)}


async def _apply_delete_action(
    skill_id: str,
    store: MemoryStore,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    try:
        from orchestrator.skills_store import delete_skill
        from orchestrator.skills_projection import SkillProjectionStore

        delete_skill(skill_id)
        if store._pool:
            projection_store = SkillProjectionStore(store._pool)
            await projection_store.delete_projection(skill_id)
        await store.log_consolidation_nudge_action(
            user_id=user_id,
            run_id=uuid.uuid4(),
            action_type="delete",
            skill_id=skill_id,
            target_skill_id=None,
            reason="model-driven delete",
            similarity=None,
            status="applied",
        )
        return {"deleted": True, "reason": "ok"}
    except Exception as e:
        return {"deleted": False, "reason": str(e)}


async def _find_duplicate_autonomous_skills(
    skills: list[dict[str, Any]],
    store: MemoryStore,
) -> list[list[dict[str, Any]]]:
    if len(skills) < 2:
        return []

    from orchestrator.memory.embedding import embed_query

    groups: list[list[dict[str, Any]]] = []
    processed: set[str] = set()

    for i, skill in enumerate(skills):
        if skill["skill_id"] in processed:
            continue

        group = [skill]
        query_text = f"{skill['name']}\n{skill['description']}"
        try:
            query_emb = await embed_query(query_text)
        except Exception:
            continue

        for j, other_skill in enumerate(skills[i + 1 :], start=i + 1):
            if other_skill["skill_id"] in processed:
                continue

            if other_skill.get("embedding") is None:
                continue

            norm_a = sum(a * a for a in query_emb) ** 0.5
            norm_b = sum(b * b for b in other_skill["embedding"]) ** 0.5
            dot = sum(a * b for a, b in zip(query_emb, other_skill["embedding"]))
            similarity = dot / (norm_a * norm_b + 1e-8)
            cosine_distance = 1 - similarity

            if similarity >= 0.90:
                group.append(other_skill)
                processed.add(other_skill["skill_id"])

        if len(group) > 1:
            groups.append(group)
            processed.add(skill["skill_id"])

    return groups


async def _try_merge_duplicate_skills(
    group: list[dict[str, Any]],
    store: MemoryStore,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    if len(group) < 2:
        return {
            "merged": False,
            "reason": "group has fewer than 2 skills",
            "similarity": None,
            "kept_skill_id": None,
            "absorbed_skill_ids": [],
        }

    primary = max(group, key=lambda s: s.get("use_count") or 0)
    absorbed = [s for s in group if s["skill_id"] != primary["skill_id"]]

    try:
        await store.merge_autonomous_skills(
            kept_skill_id=primary["skill_id"],
            absorbed_skill_ids=[s["skill_id"] for s in absorbed],
            user_id=user_id,
        )
        return {
            "merged": True,
            "reason": f"merged {len(absorbed)} duplicate(s)",
            "similarity": 0.90,
            "kept_skill_id": primary["skill_id"],
            "absorbed_skill_ids": [s["skill_id"] for s in absorbed],
        }
    except Exception as e:
        return {
            "merged": False,
            "reason": f"merge failed: {e}",
            "similarity": 0.90,
            "kept_skill_id": primary["skill_id"],
            "absorbed_skill_ids": [],
        }


async def _find_stale_autonomous_skills(
    skills: list[dict[str, Any]],
    store: MemoryStore,
    stale_days: int,
) -> list[dict[str, Any]]:
    stale: list[dict[str, Any]] = []
    for skill in skills:
        last_used = skill.get("last_used_at")
        if last_used is None:
            continue
        try:
            from datetime import timedelta

            cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
            if last_used < cutoff:
                stale.append(skill)
        except Exception:
            continue
    return stale
