"""Substrate taxonomy + gate-guard for LongMemEval harness comparison.

Two non-comparable memory substrates:

- ``chunk``: raw 4000-char overlapping chunks inserted directly (no LLM extraction).
- ``fact``: LLM-extracted facts via production ``store.insert_memory``.

Cross-substrate score comparison silently corrupts the data narrative, so any
comparison site must pass through ``assert_substrate_match``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

Substrate = Literal["chunk", "fact"]
VALID_SUBSTRATES: tuple[Substrate, ...] = ("chunk", "fact")


class SubstrateMismatchError(RuntimeError):
    """Raised when comparison sites encounter different substrates or untagged scores."""


def normalize_substrate(value: object) -> Substrate:
    if value in VALID_SUBSTRATES:
        return value  # type: ignore[return-value]
    raise SubstrateMismatchError(f"Unknown substrate {value!r}; expected one of {VALID_SUBSTRATES}")


def read_score_substrate(score_path: Path) -> Substrate:
    if not score_path.exists():
        raise FileNotFoundError(f"Score JSON not found: {score_path}")
    try:
        payload = json.loads(score_path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Score JSON is not valid: {score_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Score JSON must be an object: {score_path}")
    if "substrate" not in payload:
        raise SubstrateMismatchError(
            f"Score JSON {score_path} is missing required 'substrate' field. "
            "Re-run the harness to produce a tagged score file."
        )
    return normalize_substrate(payload["substrate"])


def assert_substrate_match(
    score_path_a: Path,
    score_path_b: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load two score JSONs and assert they share a substrate.

    Returns the parsed payloads so the gate-guard is the single
    delta-computation site for cross-substrate prevention.

    Raises:
        FileNotFoundError: if either score path is missing.
        SubstrateMismatchError: if substrates disagree or either file is
            missing the ``substrate`` tag.
    """
    substrate_a = read_score_substrate(score_path_a)
    substrate_b = read_score_substrate(score_path_b)
    if substrate_a != substrate_b:
        raise SubstrateMismatchError(
            f"Cannot compare score JSONs across substrates: "
            f"{score_path_a} declares {substrate_a!r}, "
            f"{score_path_b} declares {substrate_b!r}. "
            f"Chunk and fact substrates are not directly comparable — they "
            f"measure different memory pools."
        )
    payload_a = json.loads(score_path_a.read_text())
    payload_b = json.loads(score_path_b.read_text())
    return payload_a, payload_b


def load_tagged_score(score_path: Path) -> dict[str, Any]:
    """Load a single score JSON, validating its substrate tag.

    Use this at any site that reads a score JSON for downstream processing
    (delta computation, reporting, or comparison). The validation ensures
    the file was produced by a substrate-tagged harness; pre-tag files
    hard-fail here.

    Raises:
        FileNotFoundError: if the score path is missing.
        SubstrateMismatchError: if the file is missing the ``substrate`` field.
    """
    read_score_substrate(score_path)
    payload = json.loads(score_path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"Score JSON must be an object: {score_path}")
    return payload
