"""Output rendering for Council deliberation."""

from __future__ import annotations

from typing import Any

from orchestrator.council.models import (
    CouncilSession,
    CouncilOutput,
    AuditFinding,
    PerspectiveResponse,
)


def build_disagreement_map(
    responses: list[PerspectiveResponse],
) -> dict[str, Any]:
    """Build disagreement map from perspective responses.

    Returns:
        Dict with agreement_zones, dissent_zones, signals
    """
    if not responses:
        return {"agreement_zones": [], "dissent_zones": [], "signals": []}

    content_by_role = {resp.perspective.value: resp.content for resp in responses}
    confidence_by_role = {resp.perspective.value: resp.confidence for resp in responses}  # noqa: F841

    agreement_zones = []
    dissent_zones = []
    signals = []

    keywords_agree = ["agree", "consensus", "same", "both", "unanimous"]
    keywords_disagree = ["disagree", "however", "but", "instead", "contrary"]

    sample_content = list(content_by_role.values())[0].lower() if content_by_role else ""
    agree_count = sum(1 for kw in keywords_agree if kw in sample_content)

    if agree_count >= 2:
        agreement_zones.append(
            {
                "description": "General alignment on approach",
                "roles": list(content_by_role.keys()),
            }
        )

    for role, content in content_by_role.items():
        content_lower = content.lower()
        dissent_count = sum(1 for kw in keywords_disagree if kw in content_lower)
        if dissent_count >= 2:
            dissent_zones.append(
                {
                    "topic": "Various considerations",
                    "positions": {role: content[:200]},
                }
            )

    for resp in responses:
        if resp.confidence < 4.0:
            signals.append(
                {
                    "role": resp.perspective.value,
                    "signal": "Low confidence",
                    "content": resp.content[:200],
                }
            )

    return {
        "agreement_zones": agreement_zones,
        "dissent_zones": dissent_zones,
        "signals": signals,
    }


class CouncilOutputRenderer:
    """Renderer for council deliberation output."""

    def __init__(self):
        """Initialize output renderer."""
        pass

    def render_session(self, session: CouncilSession) -> CouncilOutput:
        """Render council session to output format."""
        summary = self._render_summary(session)
        perspectives_summary = self._render_perspectives(session)
        consensus = self._render_consensus(session)
        findings = self._render_findings(session)
        raw_reasoning = self._render_raw_reasoning(session)

        disagreement_map = {}
        if session.rounds:
            last_round = session.rounds[-1]
            disagreement_map = build_disagreement_map(last_round.responses)

        token_costs = session.token_costs if isinstance(session.token_costs, dict) else {}
        completed_rounds = len(session.rounds)
        total_rounds = session.config.round_count + (1 if session.config.audit_enabled else 0)
        models_total = 0
        if session.rounds:
            models_total = max((len(r.responses) for r in session.rounds), default=0)
        if models_total <= 0:
            by_role = token_costs.get("by_role", {})
            if isinstance(by_role, dict):
                models_total = len([role for role in by_role if role != "auditor"])

        metadata = {
            "session_id": session.session_id,
            "disagreement_map": disagreement_map,
            "total_tokens": int(token_costs.get("total_tokens", 0) or 0),
            "total_cost": float(token_costs.get("total_cost_usd", 0.0) or 0.0),
            "total_cost_usd": float(token_costs.get("total_cost_usd", 0.0) or 0.0),
            "models_used": token_costs.get("models_used", []),
            "models_total": models_total,
            "completed_rounds": completed_rounds,
            "total_rounds": total_rounds,
            "progress_events": session.metadata.get("progress_events", []),
            "raw_reasoning": raw_reasoning,
        }

        return CouncilOutput(
            summary=summary,
            perspectives_summary=perspectives_summary,
            consensus=consensus,
            findings=findings,
            metadata=metadata,
        )

    def _render_summary(self, session: CouncilSession) -> str:
        if not session.rounds:
            return "No rounds completed."
        last_round = session.rounds[-1]
        return f"Council completed {len(session.rounds)} round(s) with {len(last_round.responses)} perspectives."

    def _render_perspectives(self, session: CouncilSession) -> dict[str, str]:
        result = {}
        for round_obj in session.rounds:
            for resp in round_obj.responses:
                role = resp.perspective.value
                result[role] = resp.content
        return result

    def _render_raw_reasoning(self, session: CouncilSession) -> str:
        blocks: list[str] = []
        for round_obj in session.rounds:
            for response in round_obj.responses:
                role = response.perspective.value
                content = response.content.strip()
                reasoning = (response.reasoning or "").strip()
                if not content and not reasoning:
                    continue

                section_lines = [f"### Round {round_obj.round_number} - {role}"]
                if content:
                    section_lines.append(content)
                if reasoning:
                    section_lines.append("\n#### Reasoning Trace")
                    section_lines.append(reasoning)
                blocks.append("\n".join(section_lines))

        return "\n\n".join(blocks)

    def _render_consensus(self, session: CouncilSession) -> str | None:
        if not session.rounds:
            return None
        last_round = session.rounds[-1]
        disagreement_map = build_disagreement_map(last_round.responses)
        if disagreement_map["agreement_zones"]:
            return "Multiple perspectives showed alignment on key points."
        return "No clear consensus reached."

    def _render_findings(self, session: CouncilSession) -> list[AuditFinding]:
        return session.audit_findings

    def render_text(self, output: CouncilOutput) -> str:
        """Render output as plain text."""
        lines = [output.summary, ""]

        if output.consensus:
            lines.append(f"Consensus: {output.consensus}")

        if output.findings:
            lines.append("")
            lines.append("Audit Findings:")
            for finding in output.findings:
                lines.append(f"  - [{finding.severity}] {finding.description}")

        return "\n".join(lines)
