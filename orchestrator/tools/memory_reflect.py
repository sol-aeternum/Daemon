"""Memory reflect tool - reactive synthesis of memories without persistent writes."""

from __future__ import annotations

import uuid
from typing import Any

import litellm

from orchestrator.config import get_settings
from orchestrator.memory.embedding import embed_query
from orchestrator.memory.retrieval import retrieve_memories_for_text
from orchestrator.memory.store import MemoryStore
from orchestrator.tools.registry import Tool

REFLECT_SYNTHESIS_PROMPT = """You are a thoughtful memory analyst. Given the retrieved memories below, synthesize them into a coherent, nuanced reflection on the topic.

Guidelines:
- Preserve specifics — don't over-generalize
- Include concrete details, numbers, dates, and specific names when available
- Identify patterns, connections, and potential tensions across memories
- Maintain the user's voice and perspective where evident
- Acknowledge uncertainty when memories conflict or are ambiguous
- Keep the reflection focused and insightful

Output format:
Provide only the synthesized reflection, no additional commentary or preamble."""


class MemoryReflectTool(Tool):
    name = "memory_reflect"
    description = "Synthesize memories into a coherent reflection on a topic. Uses expanded retrieval (top-15) with L0 memories included. Non-persistent: produces no memory writes."
    parameters = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "The topic or question to reflect on",
            },
            "limit": {
                "type": "integer",
                "default": 15,
                "description": "Maximum number of memories to retrieve (default: 15)",
            },
        },
        "required": ["topic"],
    }

    def __init__(self, store: MemoryStore, user_id: uuid.UUID) -> None:
        self.store = store
        self.user_id = user_id

    async def execute(self, **kwargs: Any) -> str:
        topic = kwargs.get("topic", "")
        if not topic or not topic.strip():
            return "No topic provided for reflection."

        limit = kwargs.get("limit", 15)
        effective_limit = max(1, min(limit, 50))

        query_embedding = await embed_query(topic)
        memories = await retrieve_memories_for_text(
            store=self.store,
            query_text=topic,
            user_id=self.user_id,
            query_embedding=query_embedding,
            limit=effective_limit,
            include_local=True,
            include_historical=True,
            include_l0=True,
            include_dream_observations=True,
        )

        if not memories:
            return (
                "No relevant memories found for reflection. "
                "Either no memories exist yet, or none matched the topic closely enough."
            )

        # Format memories for the LLM
        formatted_memories = self._format_memories(memories)

        model = self._get_orchestrator_model()
        settings = get_settings()
        provider_config = settings.get_provider_config("openrouter")

        messages = [
            {"role": "system", "content": REFLECT_SYNTHESIS_PROMPT},
            {
                "role": "user",
                "content": f"Topic for reflection: {topic}\n\nRetrieved memories:\n{formatted_memories}",
            },
        ]

        call_params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "timeout": provider_config.timeout_s,
        }

        if provider_config.base_url:
            call_params["api_base"] = provider_config.base_url

        if provider_config.api_key:
            call_params["api_key"] = provider_config.api_key
        elif provider_config.requires_auth:
            return f"Reflection synthesis failed: provider '{provider_config.name}' requires an API key but none was provided"

        if provider_config.extra_headers:
            call_params["extra_headers"] = provider_config.extra_headers

        try:
            response = await litellm.acompletion(**call_params)

            content = self._extract_content(response)
            if content:
                return content.strip()
            return "Reflection generated but produced no content."

        except Exception as e:
            return f"Reflection synthesis failed: {str(e)}"

    def _format_memories(self, memories: list[dict[str, Any]]) -> str:
        """Format memories for inclusion in the synthesis prompt."""
        lines = []
        for i, mem in enumerate(memories, 1):
            content = mem.get("content", "")
            category = str(mem.get("category") or "unknown")
            slot = mem.get("memory_slot")
            source = mem.get("source", "unknown")

            slot_text = f" (slot={slot})" if slot else ""
            lines.append(f"{i}. [{category.upper()}]{slot_text} [source={source}] {content}")

        return "\n".join(lines)

    def _get_orchestrator_model(self) -> str:
        """Get the orchestrator-tier model from settings."""
        settings = get_settings()
        tier_config = settings.get_tier_config(settings.default_tier)
        return tier_config.orchestrator.model

    def _extract_content(self, response: Any) -> str:
        """Extract content from litellm response."""
        try:
            choices = getattr(response, "choices", None)
            if choices and len(choices) > 0:
                choice = choices[0]
                if hasattr(choice, "message"):
                    content = getattr(choice.message, "content", None)
                    if content:
                        return str(content)
                elif isinstance(choice, dict):
                    message = choice.get("message", {})
                    if isinstance(message, dict):
                        content = message.get("content")
                        if content:
                            return str(content)

            # Fallback: model_dump
            model_dump = getattr(response, "model_dump", None)
            if callable(model_dump):
                try:
                    data = model_dump()
                    if isinstance(data, dict):
                        choices = data.get("choices")
                        if choices and len(choices) > 0:
                            message = choices[0].get("message")
                            if isinstance(message, dict):
                                content = message.get("content")
                                if content:
                                    return str(content)
                except Exception:
                    pass

            # Direct attribute
            if hasattr(response, "content"):
                content = response.content
                if content:
                    return str(content)

            return ""

        except Exception:
            return ""
