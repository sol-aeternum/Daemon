"""Memory promote tool - moves a memory from L1/L2 to L0 (frozen)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from orchestrator.memory.store import MemoryStore
from orchestrator.tools.registry import Tool


class MemoryPromoteTool(Tool):
    name = "memory_promote"
    description = "Promote a memory to L0 (frozen) tier. L0 memories are always injected into every prompt without embedding-based retrieval. Use this to flag critically important memories that should never be forgotten."
    parameters = {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "string",
                "description": "UUID of the memory to promote to L0",
            },
        },
        "required": ["memory_id"],
    }

    def __init__(self, store: MemoryStore, user_id: uuid.UUID) -> None:
        self.store = store
        self.user_id = user_id

    async def execute(self, **kwargs: Any) -> str:
        memory_id_str = kwargs.get("memory_id")
        if not memory_id_str:
            return json.dumps({"error": "memory_id is required"})

        try:
            memory_id = uuid.UUID(memory_id_str)
        except ValueError:
            return json.dumps({"error": "Invalid memory_id format"})

        memory = await self.store.get_memory(memory_id)
        if not memory:
            return json.dumps({"error": "Memory not found"})

        if memory.get("user_id") != self.user_id:
            return json.dumps({"error": "Memory not found"})

        current_tier = memory.get("tier", "l1")
        if current_tier == "l0":
            return json.dumps(
                {
                    "success": True,
                    "memory_id": str(memory_id),
                    "tier": "l0",
                    "message": "Memory is already at L0 tier",
                }
            )

        success = await self.store.update_memory_tier(memory_id, "l0")
        if not success:
            return json.dumps({"error": "Failed to promote memory to L0"})

        return json.dumps(
            {
                "success": True,
                "memory_id": str(memory_id),
                "previous_tier": current_tier,
                "new_tier": "l0",
                "message": f"Memory promoted from {current_tier} to L0",
            }
        )
