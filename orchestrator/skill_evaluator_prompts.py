from __future__ import annotations


def build_skill_creation_prompt(
    *,
    user_request: str,
    assistant_response: str,
    tool_trace: str,
    tool_call_count: int,
    conversation_summary: str | None = None,
) -> str:
    return f"""You are extracting a reusable autonomous skill from a single successful completed turn.

Your job is to generalize the procedure into a portable SKILL.md-style document that can help future runs.

Constraints:
- The turn qualified because it used {tool_call_count} tool calls.
- Generalize the procedure, not the specific conversation.
- Preserve useful tool sequencing, guardrails, and validation steps.
- Remove user-specific secrets, IDs, timestamps, or one-off context.
- Do not include markdown frontmatter. The caller will wrap metadata separately.
- The skill must be reusable by an autonomous coding agent.

Return valid JSON only with exactly these keys:
{{
  "name": "stable reusable skill name in Title Case",
  "description": "1-2 sentences explaining when this skill applies and what it accomplishes",
  "trigger_conditions": "concise trigger summary for retrieval/dedup",
  "skill_markdown": "full markdown body beginning with a top-level # heading"
}}

The `skill_markdown` value must be a complete SKILL.md-style body with this exact structure, in this order:
- # <Skill Title>
- ## Purpose
- ## When To Use
- ## Workflow
- ## Verification
- ## Guardrails

Section rules:
- Every required section must be present and non-empty.
- `## Workflow` must be a reusable numbered procedure, not prose paragraphs.
- `## Verification` must contain concrete checks or commands that confirm success.
- `## Guardrails` must contain durable failure-prevention rules and scope limits.
- Do not include frontmatter, changelogs, conversational recap, or user-specific identifiers.
- Write portable instructions for future autonomous execution, not a summary of this incident.

Conversation summary:
{conversation_summary or "None"}

User request:
{user_request}

Tool trace:
{tool_trace}

Assistant response:
{assistant_response}
"""


def build_skill_refinement_prompt(
    *,
    user_request: str,
    assistant_response: str,
    tool_trace: str,
    existing_skill_name: str,
    existing_skill_description: str,
    existing_skill_markdown: str,
    candidate_name: str,
    candidate_description: str,
    candidate_trigger_conditions: str,
    candidate_skill_markdown: str,
) -> str:
    return f"""You are refining an existing autonomous skill using evidence from a newly completed successful turn.

Decide whether the existing skill needs a targeted patch.

Rules:
- Prefer NO_CHANGE unless the new turn adds durable, reusable guidance missing from the existing skill.
- If patching, return one exact substring from the existing markdown as old_text and a replacement as new_text.
- Patch the smallest viable section. Do not rewrite the whole skill unless a full section replacement is truly necessary.
- old_text must appear verbatim in the existing skill markdown.
- Do not propose frontmatter edits.
- Keep the result portable and remove conversation-specific details.

Return valid JSON only with exactly these keys:
{{
  "decision": "NO_CHANGE" | "PATCH",
  "reason": "brief explanation",
  "trigger_conditions": "updated concise trigger summary",
  "old_text": "exact existing substring when decision=PATCH, else empty string",
  "new_text": "replacement text when decision=PATCH, else empty string"
}}

Completed turn:

User request:
{user_request}

Tool trace:
{tool_trace}

Assistant response:
{assistant_response}

Existing skill metadata:
- Name: {existing_skill_name}
- Description: {existing_skill_description}

Existing skill markdown:
{existing_skill_markdown}

Candidate skill extracted from this turn:
- Name: {candidate_name}
- Description: {candidate_description}
- Trigger conditions: {candidate_trigger_conditions}

Candidate skill markdown:
{candidate_skill_markdown}
"""
