"""Base subagent framework for Daemon."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from dataclasses import dataclass, field
from enum import Enum
import asyncio
import json
import uuid


class SubagentType(Enum):
    """Available subagent types."""

    RESEARCH = "research"
    IMAGE = "image"
    CODE = "code"
    READER = "reader"
    AUDIO = "audio"


@dataclass
class SubagentResult:
    """Result from a subagent execution."""

    agent_type: SubagentType
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary."""
        return {
            "agent_type": self.agent_type.value,
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata,
        }


class BaseSubagent(ABC):
    """Base class for all subagents."""

    agent_type: SubagentType
    description: str = ""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize subagent with optional config."""
        self.config = config or {}

    @abstractmethod
    async def execute(
        self, task: str, context: dict[str, Any] | None = None
    ) -> SubagentResult:
        """Execute the subagent task.

        Args:
            task: The task description/query for this subagent
            context: Optional context from parent conversation

        Returns:
            SubagentResult with execution results
        """
        pass

    def _create_result(
        self,
        success: bool,
        data: dict[str, Any] | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SubagentResult:
        """Helper to create a SubagentResult."""
        return SubagentResult(
            agent_type=self.agent_type,
            success=success,
            data=data or {},
            error=error,
            metadata=metadata or {},
        )


class SubagentManager:
    """Manages subagent instances and execution."""

    def __init__(self) -> None:
        """Initialize the subagent manager."""
        self._agents: dict[SubagentType, BaseSubagent] = {}
        self._execution_history: list[SubagentResult] = []
        self._sessions: dict[str, list[dict[str, Any]]] = {}

    def register(self, agent: BaseSubagent) -> None:
        """Register a subagent."""
        self._agents[agent.agent_type] = agent

    def get(self, agent_type: SubagentType) -> BaseSubagent | None:
        """Get a registered subagent."""
        return self._agents.get(agent_type)

    def list_available(self) -> list[dict[str, str]]:
        """List available subagent types with descriptions."""
        return [
            {
                "type": agent_type.value,
                "description": agent.description,
            }
            for agent_type, agent in self._agents.items()
        ]

    async def spawn(
        self,
        agent_type: SubagentType,
        task: str,
        context: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> SubagentResult:
        """Spawn a subagent to execute a task.

        Args:
            agent_type: Type of subagent to spawn
            task: Task description/query
            context: Optional context from parent

        Returns:
            SubagentResult from execution
        """
        if session_id is None:
            session_id = str(uuid.uuid4())

        history = self._sessions.setdefault(session_id, [])
        context_payload = dict(context or {})
        context_payload.setdefault("session_id", session_id)
        context_payload.setdefault("history", history.copy())

        agent = self._agents.get(agent_type)
        if not agent:
            return SubagentResult(
                agent_type=agent_type,
                success=False,
                error=f"Unknown subagent type: {agent_type.value}",
                metadata={"session_id": session_id},
            )

        try:
            result = await agent.execute(task, context_payload)
            result.metadata["session_id"] = session_id
            self._execution_history.append(result)
            history.append({"task": task, "result": result.to_dict()})
            return result
        except Exception as e:
            error_result = SubagentResult(
                agent_type=agent_type,
                success=False,
                error=f"Execution failed: {str(e)}",
                metadata={"session_id": session_id},
            )
            self._execution_history.append(error_result)
            history.append({"task": task, "result": error_result.to_dict()})
            return error_result

    async def spawn_multiple(
        self,
        spawns: list[tuple[SubagentType, str, dict[str, Any] | None, str | None]],
        context: dict[str, Any] | None = None,
    ) -> list[SubagentResult]:
        """Spawn multiple subagents in parallel.

        Args:
            spawns: List of (agent_type, task) tuples
            context: Optional shared context

        Returns:
            List of SubagentResults
        """
        tasks: list[asyncio.Task[SubagentResult] | asyncio.Future[SubagentResult]] = []
        for spawn in spawns:
            agent_type = spawn[0]
            task = spawn[1]
            spawn_context = spawn[2] if len(spawn) > 2 else None
            session_id = spawn[3] if len(spawn) > 3 else None
            tasks.append(
                self.spawn(agent_type, task, spawn_context or context, session_id)
            )
        return await asyncio.gather(*tasks)

    def get_history(self) -> list[SubagentResult]:
        """Get execution history."""
        return self._execution_history.copy()

    def clear_history(self) -> None:
        """Clear execution history."""
        self._execution_history.clear()
