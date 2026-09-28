from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass
class ModelDecision:
    tier: str
    model: str
    reason: str
    advisor_eligible: bool = False
    profile: str = "routine"


COMPLEXITY_SIGNALS = {
    "compare",
    "versus",
    "vs",
    "trade-off",
    "pros and cons",
    "should i",
    "which is better",
    "analyze",
    "evaluate",
    "summarize everything",
    "help me decide",
    "strategy",
    "plan for",
    "what do you think about",
    "implications",
    "deep dive",
    "in depth",
    "comprehensive",
    "walk me through",
    "debug",
    "debugging",
    "refactor",
    "refactoring",
    "implement",
    "implementation",
    "write code",
    "code review",
    "review code",
    "root cause",
    "prove",
    "derive",
    "optimize",
    "optimise",
    "write a python script",
    "architecture",
    "design pattern",
}

TRIVIAL_SIMPLE_SIGNALS = {
    "hi",
    "hello",
    "thanks",
    "thank you",
    "okay",
    "ok",
    "what time is it",
    "what date is it",
}

RESEARCH_SIGNALS = {"research", "search for", "look up", "find sources", "fact-check"}


def _has_signal(message: str, signals: set[str]) -> bool:
    return any(re.search(r"(?<!\w)" + re.escape(signal) + r"(?!\w)", message) for signal in signals)


def classify_message(
    message: str,
    turn_count: int = 0,
    has_code_block: bool | None = None,
) -> str:
    """Classify the requested work; size is separately bounded by compute policy.

    A long prompt or conversation does not by itself require premium reasoning.
    """
    msg_lower = message.lower().strip()
    if not msg_lower:
        return "trivial"

    detected_code_block = "```" in message if has_code_block is None else has_code_block
    if detected_code_block:
        return "complex"
    if msg_lower in TRIVIAL_SIMPLE_SIGNALS:
        return "trivial"
    if _has_signal(msg_lower, COMPLEXITY_SIGNALS):
        return "complex"
    return "standard"


def select_model_tier(
    message: str,
    turn_count: int = 0,
    has_code_block: bool | None = None,
    user_override: str | None = None,
) -> ModelDecision:
    if user_override and user_override != "auto":
        return ModelDecision(
            tier="explicit",
            model=user_override,
            reason=f"user_selected:{user_override}",
            advisor_eligible=False,
        )

    classification = classify_message(
        message,
        turn_count=turn_count,
        has_code_block=has_code_block,
    )
    if classification == "complex":
        return ModelDecision(
            tier="reasoning",
            model="",
            reason="classification:complex",
            advisor_eligible=True,
            profile="reasoning",
        )

    return ModelDecision(
        tier="fast",
        model="",
        reason=f"classification:{classification}",
        advisor_eligible=False,
        profile="research" if _has_signal(message.lower(), RESEARCH_SIGNALS) else "routine",
    )
