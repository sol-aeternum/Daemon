"""Data models for Council deliberation."""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from functools import lru_cache

from pydantic import BaseModel, Field, field_validator

from orchestrator.council.config import load_roster
from orchestrator.model_routing import RoutingError, read_model_identity


# A council is only independent while several vendors are answering. Three is
# the smallest number of *distinct model developers* that can still disagree
# with each other, so it is the floor for both the planned roster and the models
# that actually served a round.
MIN_COUNCIL_DEVELOPERS = 3


class CouncilDiversityError(RuntimeError):
    """A council round could not field enough independent developers.

    A council that cannot be served by at least ``MIN_COUNCIL_DEVELOPERS``
    distinct model developers is not a deliberation, and reporting it as one
    would be a fabricated consensus. The integration layer surfaces this through
    the existing council error path; it is not a new event type.
    """


def read_developer(model: str) -> str | None:
    """The developer that builds ``model``, or None when it cannot be read.

    A roster reaches the engine from more than one path (config file, interview,
    the unvalidated preset assignment in the command layer), so an unreadable id
    resolves to None here instead of raising mid-deliberation. A seat with no
    readable developer can never be counted towards council independence.
    """
    try:
        return read_model_identity(model).developer
    except RoutingError:
        return None


def roster_developers(roster: Mapping[str, str]) -> dict[str, str]:
    """Map each role to the developer of its model, skipping unreadable ids.

    The engine plans diversity with this and the validator enforces it, so both
    agree on what "a different developer" means by construction.
    """
    developers: dict[str, str] = {}
    for role, model_id in roster.items():
        developer = read_developer(model_id.strip()) or ""
        if developer:
            developers[role] = developer
    return developers


@lru_cache(maxsize=1)
def _default_roster() -> dict[str, str]:
    # roster.yaml is the single source of truth for seat preferences; a copy is
    # returned so a caller mutating one config's roster cannot poison the cache.
    return dict(load_roster("default"))


def default_roster() -> dict[str, str]:
    """The shipped seat preferences, fresh per config."""
    return dict(_default_roster())


class PerspectiveType(Enum):
    """Types of perspectives in council deliberation."""

    ANALYST = "analyst"
    STRATEGIST = "strategist"
    SKEPTIC = "skeptic"
    CONTRARIAN = "contrarian"
    AUDITOR = "auditor"


class CouncilConfig(BaseModel):
    """Configuration for council deliberation with validation."""

    # validate_default keeps the shipped roster honest: if roster.yaml ever plans
    # fewer than MIN_COUNCIL_DEVELOPERS developers, every council fails loudly at
    # construction instead of quietly running a one-voice "council".
    roster: dict[str, str] = Field(default_factory=default_roster, validate_default=True)
    round_count: int = Field(default=2, ge=1, le=4)
    audit_enabled: bool = Field(default=False)
    interview_bypass: bool = Field(default=False)
    preset_name: str = Field(default="default")
    interview_state: dict[str, Any] = Field(default_factory=dict)

    @field_validator("roster")
    @classmethod
    def validate_roster_diversity(cls, v: dict[str, str]) -> dict[str, str]:
        """Require a roster that can field a genuinely independent council.

        Diversity is counted in model developers, not in the first path segment:
        serving-prefixed ids such as ``openrouter/anthropic/claude-sonnet-5``
        name an Anthropic model, and reading them as a single "openrouter"
        provider rejected valid rosters. Seats must also hold distinct models,
        because one model answering twice is one voice counted twice.
        """
        roster: dict[str, str] = {}
        developers: dict[str, str] = {}
        assigned_models: dict[str, str] = {}

        for role, model_id in v.items():
            model = model_id.strip()
            if not role.strip():
                raise ValueError("Council role names must be non-empty")
            if not model:
                raise ValueError(f"Council role {role!r} has no model assigned")
            developer = read_developer(model)
            if developer is None:
                raise ValueError(
                    f"Council role {role!r} has a malformed model id {model!r}; "
                    "expected <developer>/<model>, optionally prefixed with openrouter/"
                )
            if model in assigned_models:
                raise ValueError(
                    f"Council roster assigns {model!r} to both {assigned_models[model]!r} and "
                    f"{role!r}; every seat needs its own model to stay a separate voice"
                )
            roster[role] = model
            assigned_models[model] = role
            developers[role] = developer

        distinct = set(developers.values())
        if len(distinct) < MIN_COUNCIL_DEVELOPERS:
            raise ValueError(
                f"Council roster must plan at least {MIN_COUNCIL_DEVELOPERS} different model "
                f"developers, got {len(distinct)}: {sorted(distinct)}"
            )
        return roster


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
