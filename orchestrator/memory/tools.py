"""Memory tools for Daemon tool system."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from orchestrator.memory.store import MemoryStore
from orchestrator.memory.retrieval import retrieve_memories
from orchestrator.memory.dedup import dedup_and_store
from orchestrator.memory.embedding import embed_text
from orchestrator.tools.registry import Tool


class MemoryReadTool(Tool):
    name = "memory_read"
    description = "Retrieve memories using semantic search"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "mode": {
                "type": "string",
                "enum": ["semantic", "temporal"],
                "default": "semantic",
            },
            "after": {
                "type": "string",
                "description": "ISO8601 timestamp lower bound",
            },
            "before": {
                "type": "string",
                "description": "ISO8601 timestamp upper bound",
            },
            "limit": {"type": "integer", "default": 5},
        },
        "required": [],
    }

    def __init__(self, store: MemoryStore, user_id: uuid.UUID) -> None:
        self.store = store
        self.user_id = user_id

    async def execute(self, **kwargs: Any) -> str:
        mode = kwargs.get("mode", "semantic")
        query = kwargs.get("query", "")
        limit = kwargs.get("limit", 5)
        after_raw = kwargs.get("after")
        before_raw = kwargs.get("before")

        def parse_dt(value: str | None) -> datetime | None:
            if not value:
                return None
            normalized = value.strip()
            if normalized.endswith("Z"):
                normalized = f"{normalized[:-1]}+00:00"
            return datetime.fromisoformat(normalized)

        if mode == "semantic":
            query_embedding = await embed_text(query)
            memories = await self.store.search_memories(
                user_id=self.user_id,
                query_embedding=query_embedding,
                limit=limit,
                include_local=True,
            )
        else:
            try:
                created_after = parse_dt(after_raw)
                created_before = parse_dt(before_raw)
            except ValueError:
                return "Invalid 'after' or 'before' timestamp. Use ISO8601."

            memories = await self.store.list_memories(
                user_id=self.user_id,
                status="active",
                include_local=True,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
            )

        if not memories:
            return "No relevant memories found."

        formatted = []
        for mem in memories:
            content = mem.get("content", "")
            category = str(mem.get("category") or "unknown")
            formatted.append(f"- [{category.upper()}] {content}")

        return "\n".join(formatted)


class MemoryWriteTool(Tool):
    name = "memory_write"
    description = "Create, update, or delete memories"
    allowed_categories = {"fact", "preference", "project", "summary", "correction"}
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "update", "delete"]},
            "content": {"type": "string"},
            "category": {"type": "string", "default": "fact"},
            "memory_id": {"type": "string"},
        },
        "required": ["action"],
    }

    def __init__(self, store: MemoryStore, user_id: uuid.UUID) -> None:
        self.store = store
        self.user_id = user_id

    async def execute(self, **kwargs: Any) -> str:
        action = kwargs.get("action")

        if action == "create":
            content = kwargs.get("content", "")
            category = kwargs.get("category", "fact")
            if category not in self.allowed_categories:
                allowed = ", ".join(sorted(self.allowed_categories))
                return f"Invalid category '{category}'. Use one of: {allowed}."
            memory_id = await dedup_and_store(
                store=self.store,
                user_id=self.user_id,
                content=content,
                source_type="user_created",
                category=category,
                conversation_id=None,
            )
            return f"Memory created (ID: {memory_id})."

        elif action == "update":
            memory_id = uuid.UUID(kwargs.get("memory_id"))
            content = kwargs.get("content", "")
            await self.store.update_memory(memory_id, content=content)
            return f"Memory {memory_id} updated."

        elif action == "delete":
            memory_id = uuid.UUID(kwargs.get("memory_id"))
            await self.store.delete_memory(memory_id, soft=True)
            return f"Memory {memory_id} deleted."

        return json.dumps({"error": f"Unknown action: {action}"})
