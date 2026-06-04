from __future__ import annotations

from typing import Any


def build_consolidation_nudge_prompt(
    *,
    autonomous_skills: list[dict[str, Any]],
    recent_memories: list[dict[str, Any]],
    user_context: str | None = None,
) -> str:
    skills_text = _format_skills_for_prompt(autonomous_skills)
    memories_text = _format_memories_for_prompt(recent_memories)

    return f"""You are analyzing autonomous skills and memories for consolidation opportunities.

Your job is to identify:
1. Duplicate or highly overlapping autonomous skills that should be merged
2. Stale autonomous skills (unused for 30+ days) that should be flagged
3. Opportunities to promote useful memory patterns to new autonomous skills

Rules:
- Only autonomous skills are eligible for merge/delete. Protected skills (system/imported/manual) must be ignored.
- Each skill has an id, name, description, content, use_count, and last_used_at
- Memories have content, created_at, and status
- Return ONLY valid JSON with an "actions" array
- Each action must have: type, skill_id, reason
- Types: "merge", "delete", "flag_stale", "suggest_promotion"
- merge actions must also include: target_skill_id (the skill to merge into), similarity (0.0-1.0)
- flag_stale actions should reference skills not used in 30+ days
- suggest_promotion actions should reference memory patterns worth turning into skills
- If no actions are needed, return {{"actions": []}}

Return valid JSON only with this exact structure:
{{
  "actions": [
    {{
      "type": "merge|delete|flag_stale|suggest_promotion",
      "skill_id": "skill-id-string",
      "target_skill_id": "skill-id-to-merge-into (for merge only)",
      "reason": "brief explanation",
      "similarity": 0.95 (for merge only, 0.0-1.0)
    }}
  ]
}}

Autonomous Skills:
{skills_text}

Recent Memories (last 30 days):
{memories_text}

User Context (optional):
{user_context or "None available"}
"""


def _format_skills_for_prompt(skills: list[dict[str, Any]]) -> str:
    if not skills:
        return "(No autonomous skills found)"

    lines = []
    for s in skills:
        use_count = s.get("use_count") or 0
        last_used = s.get("last_used_at") or "never"
        content_preview = s.get("content", "")[:200].replace("\n", " ")
        lines.append(
            f"- [{s['skill_id']}] {s['name']}: {s.get('description', '')}\n"
            f"  use_count={use_count}, last_used={last_used}\n"
            f"  content: {content_preview}..."
        )
    return "\n".join(lines)


def _format_memories_for_prompt(memories: list[dict[str, Any]]) -> str:
    if not memories:
        return "(No recent memories found)"

    lines = []
    for m in memories:
        content_preview = m.get("content", "")[:150].replace("\n", " ")
        status = m.get("status", "unknown")
        created = m.get("created_at", "unknown")
        lines.append(f"- [{status}] {created}: {content_preview}...")
    return "\n".join(lines)


def parse_consolidation_actions(response_text: str) -> list[dict[str, Any]]:
    import json

    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        lines = [l for l in cleaned.splitlines() if not l.strip().startswith("```")]  # noqa: E741
        cleaned = "\n".join(lines).strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end >= start:
        cleaned = cleaned[start : end + 1]

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return []

    if not isinstance(payload, dict):
        return []

    actions = payload.get("actions")
    if not isinstance(actions, list):
        return []

    valid_types = {"merge", "delete", "flag_stale", "suggest_promotion"}
    result = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_type = str(action.get("type") or "").lower()
        if action_type not in valid_types:
            continue
        result.append(
            {
                "type": action_type,
                "skill_id": action.get("skill_id"),
                "target_skill_id": action.get("target_skill_id"),
                "reason": action.get("reason", ""),
                "similarity": action.get("similarity"),
            }
        )
    return result
