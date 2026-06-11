from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModelDecision:
    tier: str
    model: str
    reason: str
    advisor_eligible: bool = False


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
    "refactor",
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

STANDARD_SIMPLE_SIGNALS = {
    "what is my",
    "what's my",
    "remember that",
    "remember my",
    "what time",
    "what date",
    "weather",
    "set a reminder",
    "notify me",
    "generate an image",
    "make an image",
    "search for",
    "look up",
    "find me",
}


def classify_message(
    message: str,
    turn_count: int = 0,
    has_code_block: bool | None = None,
) -> str:
    msg_lower = message.lower().strip()
    if not msg_lower:
        return "trivial"

    detected_code_block = "```" in message if has_code_block is None else has_code_block
    if detected_code_block:
        return "complex"
    if turn_count > 10:
        return "complex"
    if len(message) > 500:
        return "complex"
    if len(message.split()) > 80:
        return "complex"

    if msg_lower in TRIVIAL_SIMPLE_SIGNALS:
        return "trivial"
    for signal in STANDARD_SIMPLE_SIGNALS:
        if signal in msg_lower:
            return "standard"
    for signal in COMPLEXITY_SIGNALS:
        if signal in msg_lower:
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
        )

    return ModelDecision(
        tier="fast",
        model="",
        reason=f"classification:{classification}",
        advisor_eligible=False,
    )
