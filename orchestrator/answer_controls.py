"""Per-turn exposition and investigation instructions; never execution authority."""

from typing import Literal

AnswerDetail = Literal["brief", "balanced", "detailed"]
ResearchEffort = Literal["auto", "research"]


def answer_control_instructions(
    answer_detail: AnswerDetail | None = None,
    research_effort: ResearchEffort | None = None,
) -> str:
    """Legacy missing/null controls add nothing, including on reconstructed attempts."""
    lines: list[str] = []
    if answer_detail == "brief":
        lines.append(
            "Answer detail: brief. Keep exposition concise, retaining essential evidence, "
            "citations, uncertainty and corrections. Brevity does not reduce verification."
        )
    elif answer_detail == "balanced":
        lines.append("Answer detail: balanced. Give a clear answer with proportionate explanation.")
    elif answer_detail == "detailed":
        lines.append(
            "Answer detail: detailed. Explain the findings, evidence, caveats and relevant "
            "reasoning thoroughly without filler or unsupported claims."
        )
    if research_effort == "auto":
        lines.append(
            "Research effort: auto. Choose investigation proportional to the question and "
            "uncertainty, independently of answer length. Verify central claims as needed."
        )
    elif research_effort == "research":
        lines.append(
            "Research effort: research. Investigate and cross-check central claims using "
            "available permitted sources; continue actual research after useful partial "
            "findings when needed. This applies even to a brief answer. Disclose unavailable "
            "sources and remaining uncertainty rather than claiming checks not performed."
        )
    if lines:
        lines.append(
            "These per-turn controls do not expand budgets, permissions, tools, model access "
            "or concurrency. Stay within existing limits and report incomplete work honestly."
        )
    return "\n".join(lines)


def append_answer_controls(
    prompt: str,
    answer_detail: AnswerDetail | None = None,
    research_effort: ResearchEffort | None = None,
) -> str:
    instructions = answer_control_instructions(answer_detail, research_effort)
    return f"{prompt.rstrip()}\n\n{instructions}" if instructions else prompt
