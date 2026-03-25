"""Data models for Council deliberation."""

from __future__ import annotations

from typing import Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class PerspectiveType(Enum):
    """Types of perspectives in council deliberation."""

    ANALYST = "analyst"
    STRATEGIST = "strategist"
    SKEPTIC = "skeptic"
    CONTRARIAN = "contrarian"
    AUDITOR = "auditor"


class CouncilConfig(BaseModel):
    """Configuration for council deliberation with validation."""

    roster: dict[str, str] = Field(
        default_factory=lambda: {
            "analyst": "openrouter/anthropic/claude-opus-4.6",
            "strategist": "openrouter/openai/gpt-5.4",
            "skeptic": "openrouter/moonshotai/kimi-k2.5",
            "contrarian": "openrouter/x-ai/grok-4",
            "auditor": "openrouter/deepseek/deepseek-r1",
        }
    )
    round_count: int = Field(default=2, ge=1, le=4)
    audit_enabled: bool = Field(default=False)
    interview_bypass: bool = Field(default=False)
    preset_name: str = Field(default="default")
    interview_state: dict[str, Any] = Field(default_factory=dict)

    @field_validator("roster")
    @classmethod
    def validate_heterogeneity(cls, v: dict[str, str]) -> dict[str, str]:
        """Validate minimum 3 different providers in roster."""
        providers = set()
        for model_id in v.values():
            # Extract provider from model_id like "anthropic/claude-opus-4.6"
            if "/" in model_id:
                provider = model_id.split("/")[0]
                providers.add(provider)
        if len(providers) < 3:
            raise ValueError(
                f"Roster must have at least 3 different providers, got {len(providers)}: {providers}"
            )
        return v


@dataclass
class PerspectiveResponse:
    """Response from a single perspective."""

    perspective: PerspectiveType
    content: str
    confidence: float = 0.0
    concerns: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    reasoning: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    model_id: str | None = None


@dataclass
class CouncilRound:
    """Single round of council deliberation."""

    round_number: int
    prompt: str
    responses: list[PerspectiveResponse] = field(default_factory=list)
    consensus: str | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class CouncilSession:
    """Complete council deliberation session."""

    session_id: str
    conversation_id: str
    prompt: str
    config: CouncilConfig
    interview_state: dict[str, Any] = field(default_factory=dict)
    rounds: list[CouncilRound] = field(default_factory=list)
    audit_findings: list[AuditFinding] = field(default_factory=list)
    token_costs: dict[str, Any] = field(default_factory=dict)
    final_output: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_db_record(self) -> dict[str, Any]:
        """Serialize session to database record."""
        return {
            "id": self.session_id,
            "conversation_id": self.conversation_id,
            "prompt": self.prompt,
            "config": self.config.model_dump(),
            "interview_state": self.interview_state,
            "rounds": [
                {
                    "round_number": r.round_number,
                    "prompt": r.prompt,
                    "responses": [
                        {
                            "perspective": resp.perspective.value,
                            "content": resp.content,
                            "confidence": resp.confidence,
                            "concerns": resp.concerns,
                            "suggestions": resp.suggestions,
                            "reasoning": resp.reasoning,
                            "usage": resp.usage,
                            "model_id": resp.model_id,
                        }
                        for resp in r.responses
                    ],
                    "consensus": r.consensus,
                    "timestamp": r.timestamp.isoformat(),
                }
                for r in self.rounds
            ],
            "audit_findings": [
                {
                    "category": f.category,
                    "severity": f.severity,
                    "description": f.description,
                    "recommendation": f.recommendation,
                }
                for f in self.audit_findings
            ],
            "token_costs": self.token_costs,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_db_record(cls, record: dict[str, Any]) -> CouncilSession:
        """Deserialize session from database record."""
        config = CouncilConfig(**record.get("config", {}))
        rounds = []
        for r in record.get("rounds", []):
            responses = []
            for resp in r.get("responses", []):
                perspective = PerspectiveType(resp.get("perspective", "analyst"))
                responses.append(
                    PerspectiveResponse(
                        perspective=perspective,
                        content=resp.get("content", ""),
                        confidence=resp.get("confidence", 0.0),
                        concerns=resp.get("concerns", []),
                        suggestions=resp.get("suggestions", []),
                        reasoning=resp.get("reasoning"),
                        usage=resp.get("usage", {}),
                        model_id=resp.get("model_id"),
                    )
                )
            rounds.append(
                CouncilRound(
                    round_number=r.get("round_number", 0),
                    prompt=r.get("prompt", ""),
                    responses=responses,
                    consensus=r.get("consensus"),
                    timestamp=datetime.fromisoformat(r["timestamp"])
                    if r.get("timestamp")
                    else datetime.utcnow(),
                )
            )
        audit_findings = []
        for f in record.get("audit_findings", []):
            audit_findings.append(
                AuditFinding(
                    category=f.get("category", ""),
                    severity=f.get("severity", ""),
                    description=f.get("description", ""),
                    recommendation=f.get("recommendation"),
                )
            )
        return cls(
            session_id=record.get("id", ""),
            conversation_id=record.get("conversation_id", ""),
            prompt=record.get("prompt", ""),
            config=config,
            interview_state=record.get("interview_state", {}),
            rounds=rounds,
            audit_findings=audit_findings,
            token_costs=record.get("token_costs", {}),
            final_output=record.get("final_output"),
            created_at=datetime.fromisoformat(record["created_at"])
            if record.get("created_at")
            else datetime.utcnow(),
            updated_at=datetime.fromisoformat(record["updated_at"])
            if record.get("updated_at")
            else datetime.utcnow(),
            metadata=record.get("metadata", {}),
        )


@dataclass
class AuditFinding:
    """Audit finding from council deliberation."""

    category: str
    severity: str
    description: str
    recommendation: str | None = None


@dataclass
class CouncilOutput:
    """Formatted output from council deliberation."""

    summary: str
    perspectives_summary: dict[str, str] = field(default_factory=dict)
    consensus: str | None = None
    findings: list[AuditFinding] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
