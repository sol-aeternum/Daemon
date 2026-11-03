"""Blind query generation contract. This module deliberately imports only stdlib."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any

ROUTE_ID = "luna-azure-eu"
MODEL = "openrouter/openai/gpt-6-luna"
MAX_TOKENS = 2048
SEED = 42
MAX_QUERIES = 3
MAX_QUERY_CHARS = 300
SYSTEM_PROMPT = (
    "Decompose the user's question into 1–3 standalone search queries for facts that "
    "might be stored in the user's memory, one per distinct fact needed. A question "
    "needing one fact gets one query. Do not answer, speculate about fact values, "
    "or refer to dates beyond the supplied as-of date. Return only a JSON object "
    'of the form {"queries": [string, ...]}. Each query must be nonempty, trimmed, '
    "and at most 300 characters."
)
USER_TEMPLATE = "As-of date: {as_of}\nUser question:\n{question}"
SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_QUERIES,
            "items": {"type": "string", "minLength": 1, "maxLength": MAX_QUERY_CHARS},
        }
    },
    "required": ["queries"],
    "additionalProperties": False,
}


def generate_request(question: str, as_of: str) -> dict[str, Any]:
    """The sole generation input surface: no IDs, cases, memories or gold data."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Question must be nonempty")
    datetime.fromisoformat(as_of)
    return {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(question=question, as_of=as_of)},
        ],
        "seed": SEED,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "memory_queries", "strict": True, "schema": SCHEMA},
        },
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }


def validate_queries(value: object) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_QUERIES:
        raise ValueError("Expected 1–3 queries")
    if any(
        not isinstance(query, str)
        or not query.strip()
        or query != query.strip()
        or len(query) > MAX_QUERY_CHARS
        for query in value
    ):
        raise ValueError("Invalid query text")
    return list(value)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def parse_queries(text: str) -> list[str]:
    payload = json.loads(text, object_pairs_hook=_object)
    if not isinstance(payload, dict) or set(payload) != {"queries"}:
        raise ValueError("Expected only queries")
    return validate_queries(payload["queries"])
