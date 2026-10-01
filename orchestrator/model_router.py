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
    "analyse",
    "critique",
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

#: Inflected forms of single-word complexity signals that still express a request or
#: topic (plural, third person, gerund), plus the unhyphenated "tradeoff" spelling.
#: Past tense is deliberately excluded: "compared" or "derived" mostly appear in
#: narrative, not in a request for analysis.
COMPLEXITY_SIGNAL_FORMS = {
    "trade-offs",
    "tradeoff",
    "tradeoffs",
    "compares",
    "comparing",
    "analyzes",
    "analyzing",
    "analyses",
    "analysing",
    "critiques",
    "critiquing",
    "evaluates",
    "evaluating",
    "strategies",
    "implication",
    "debugs",
    "refactors",
    "implements",
    "implementing",
    "implementations",
    "proves",
    "proving",
    "derives",
    "deriving",
    "optimizes",
    "optimizing",
    "optimises",
    "optimising",
    "architectures",
}

_COMPLEXITY_MATCH = COMPLEXITY_SIGNALS | COMPLEXITY_SIGNAL_FORMS

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


_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _closes_fence(line: str, marker: str) -> bool:
    stripped = line.strip()
    return (
        len(line) - len(line.lstrip(" ")) <= 3
        and len(stripped) >= len(marker)
        and set(stripped) == {marker[0]}
    )


def _instruction_text(message: str) -> str:
    """The text whose wording selects a workload: the user's own instruction.

    A routing heuristic, not proof of authorship or a security boundary. Closed fenced
    blocks are pasted data and never count; an unclosed fence is kept as ordinary
    text. ``>`` blockquote lines are quoted material and count only when nothing else
    remains, so an instruction written entirely inside a quote still selects its work.
    The model always receives the full message; only routing reads this view.
    """
    lines = message.splitlines()
    unfenced: list[str] = []
    index = 0
    while index < len(lines):
        opening = _FENCE_OPEN.match(lines[index])
        # A backtick "fence" whose info string holds a backtick is an inline code span.
        if opening and not (opening.group(1)[0] == "`" and "`" in opening.group(2)):
            marker = opening.group(1)
            close = next(
                (
                    candidate
                    for candidate in range(index + 1, len(lines))
                    if _closes_fence(lines[candidate], marker)
                ),
                None,
            )
            if close is not None:
                index = close + 1
                continue
        unfenced.append(lines[index])
        index += 1
    own = "\n".join(line for line in unfenced if not line.lstrip().startswith(">")).strip()
    return own or "\n".join(unfenced).strip()


def classify_message(
    message: str,
    turn_count: int = 0,
) -> str:
    """Classify the requested work; size is separately bounded by compute policy.

    A long prompt or conversation does not by itself require premium reasoning.
    Signals are read from the user's own instruction text (see
    :func:`_instruction_text`), not from pasted or quoted material. A code block on
    its own is pasted data, not a reasoning requirement.
    """
    msg_lower = message.lower().strip()
    if not msg_lower:
        return "trivial"

    if msg_lower in TRIVIAL_SIMPLE_SIGNALS:
        return "trivial"
    if _has_signal(_instruction_text(message).lower(), _COMPLEXITY_MATCH):
        return "complex"
    return "standard"


def select_model_tier(
    message: str,
    turn_count: int = 0,
    user_override: str | None = None,
) -> ModelDecision:
    if user_override and user_override != "auto":
        return ModelDecision(
            tier="explicit",
            model=user_override,
            reason=f"user_selected:{user_override}",
            advisor_eligible=False,
        )

    classification = classify_message(message, turn_count=turn_count)
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
        profile=(
            "research"
            if _has_signal(_instruction_text(message).lower(), RESEARCH_SIGNALS)
            else "routine"
        ),
    )
