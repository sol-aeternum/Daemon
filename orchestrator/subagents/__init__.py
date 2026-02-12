"""Subagents package for Daemon orchestration."""

from orchestrator.subagents.base import (
    BaseSubagent,
    SubagentManager,
    SubagentResult,
    SubagentType,
)
from orchestrator.subagents.research import ResearchSubagent
from orchestrator.subagents.image import ImageSubagent
from orchestrator.subagents.audio import AudioSubagent

__all__ = [
    "BaseSubagent",
    "SubagentManager",
    "SubagentResult",
    "SubagentType",
    "ResearchSubagent",
    "ImageSubagent",
    "AudioSubagent",
]
