"""Interview functionality for Council deliberation."""

from __future__ import annotations

from typing import Any

from orchestrator.council.models import CouncilConfig, CouncilSession, PerspectiveType
from orchestrator.council.config import load_roster


def render_interview_message(config: CouncilConfig | None = None) -> str:
    """Render the council interview message with inline options."""
    roster = config.roster if config else load_roster("default")
    preset = config.preset_name if config else "default"
    rounds = config.round_count if config else 2
    audit = config.audit_enabled if config else False

    role_names = {
        "analyst": "Opus",
        "strategist": "Gemini 3.1 Pro",
        "skeptic": "Kimi K2 Thinking",
        "contrarian": "Grok 3",
        "auditor": "DeepSeek R1",
    }

    roster_str = ", ".join(
        f"{role} ({role_names.get(role, role)})" for role in roster.keys()
    )

    msg = f"""🏛️ Council convened. Before I send this out:

**Roster**: {roster_str}
→ [Default] [Customise] [Lean (3 models)]

**Rounds**: {rounds}
→ [1] [2] [3]

**Audit round** (DeepSeek R1 checks everyone's logic after debate):
→ [Off] [On]

Or reply "go" / tap [Default] to run with defaults."""

    return msg


def parse_interview_response(response: str, config: CouncilConfig) -> CouncilConfig:
    """Parse user's interview response into CouncilConfig."""
    response_lower = response.lower().strip()

    if response_lower in ["default", "go", "run", ""]:
        config.interview_bypass = True
        return config

    if "lean" in response_lower:
        config.preset_name = "lean"
        config.roster = load_roster("lean")
    elif "custom" in response_lower:
        config.preset_name = "default"
        config.roster = load_roster("default")

    if "1" in response_lower and "round" in response_lower:
        config.round_count = 1
    elif "3" in response_lower and "round" in response_lower:
        config.round_count = 3
    elif "2" in response_lower and "round" in response_lower:
        config.round_count = 2

    if "on" in response_lower and "audit" in response_lower:
        config.audit_enabled = True
    elif "off" in response_lower and "audit" in response_lower:
        config.audit_enabled = False

    return config


class CouncilInterview:
    """Interview handler for council perspectives."""

    def __init__(self):
        """Initialize council interview handler."""
        pass

    async def conduct_interview(
        self,
        session: CouncilSession,
        perspective: PerspectiveType,
        question: str,
    ) -> str:
        """Conduct interview with a specific perspective."""
        return ""

    async def generate_questions(
        self,
        session: CouncilSession,
        perspective: PerspectiveType,
    ) -> list[str]:
        """Generate interview questions for a perspective."""
        return []

    async def synthesize_interview(
        self,
        session: CouncilSession,
    ) -> dict[str, Any]:
        """Synthesize results from all perspective interviews."""
        return {}
