"""Memory demote tool - moves a memory from L0 to L1."""

from __future__ import annotations

import json
import uuid
from typing import Any

from orchestrator.memory.store import MemoryStore
from orchestrator.tools.registry import Tool


class MemoryDemoteTool(Tool):
    name = "memory_demote"
    description = "Demote a memory from L0 to L1 tier. L0 memories are always injected into every prompt; demoting to L1 means the memory will only be retrieved via semantic search when relevant. Use this when a previously critical memory is no longer universally important."
    parameters = {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "string",
                "description": "UUID of the memory to demote to L1",
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

        current_tier = memory.get("tier", "l0")
        if current_tier != "l0":
            return json.dumps(
                {
                    "success": True,
                    "memory_id": str(memory_id),
                    "tier": current_tier,
                    "message": f"Memory is not at L0 tier (currently {current_tier})",
                }
            )

        success = await self.store.update_memory_tier(memory_id, "l1")
        if not success:
            return json.dumps({"error": "Failed to demote memory to L1"})

        return json.dumps(
            {
                "success": True,
                "memory_id": str(memory_id),
                "previous_tier": "l0",
                "new_tier": "l1",
                "message": "Memory demoted from L0 to L1",
            }
        )
