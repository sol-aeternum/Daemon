from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModelDecision:
    tier: str
    model: str
    reason: str


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
    "architecture",
    "design pattern",
}

SIMPLE_SIGNALS = {
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


def select_model_tier(
    message: str,
    turn_count: int = 0,
    has_code_block: bool = False,
    user_override: str | None = None,
) -> ModelDecision:
    if user_override and user_override != "auto":
        return ModelDecision(
            tier="explicit",
            model=user_override,
            reason=f"user_selected:{user_override}",
        )

    msg_lower = message.lower().strip()
    msg_len = len(message)

    for signal in SIMPLE_SIGNALS:
        if signal in msg_lower:
            return ModelDecision(
                tier="fast",
                model="",
                reason=f"simple_signal:{signal}",
            )

    for signal in COMPLEXITY_SIGNALS:
        if signal in msg_lower:
            return ModelDecision(
                tier="reasoning",
                model="",
                reason=f"complexity_signal:{signal}",
            )

    if msg_len > 500:
        return ModelDecision(
            tier="reasoning",
            model="",
            reason=f"long_message:{msg_len}",
        )

    if has_code_block:
        return ModelDecision(
            tier="reasoning",
            model="",
            reason="code_block",
        )

    if turn_count > 10:
        return ModelDecision(
            tier="reasoning",
            model="",
            reason=f"deep_conversation:{turn_count}",
        )

    return ModelDecision(
        tier="fast",
        model="",
        reason="default_fast",
    )
