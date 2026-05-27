#!/usr/bin/env python3
"""Tests-Only Infrastructure Guardrails (Wave 0).

Minimal set for current recovery flow:
  G1: Provider health check — probe endpoint before ingestion
  G3: Errored-floor gate — halt if errored sessions > threshold on checkpoint
  G5: Credit instrumentation — log only (documented, not blocking)

G2 (memory sanity) and G4 (extraction log) documented as non-critical for
current recovery sequence.

Scope: tests/ only. No production code changes.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time as time_module
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ["BENCHMARK_MODE"] = "1"
import dotenv
dotenv.load_dotenv()

from orchestrator.config import get_settings

import litellm  # noqa: E402


# ---------------------------------------------------------------------------
# G1: Provider health check
# ---------------------------------------------------------------------------


async def probe_provider(
    provider_slug: str = "openai",
    model: str = "openrouter/openai/gpt-4o-mini",
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Return healthy=True if provider responds to a minimal completion call."""
    call_params: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "Hi."}],
        "max_tokens": 5,
        "temperature": 0.0,
        "timeout": timeout_s,
    }
    if provider_slug != "openai":
        call_params["extra_body"] = {
            "provider": {"order": [provider_slug], "allow_fallbacks": False}
        }

    t0 = time_module.monotonic()
    try:
        response = await litellm.acompletion(**call_params)
        elapsed = time_module.monotonic() - t0
        content = ""
        _resp: Any = response
        if hasattr(_resp, "choices") and _resp.choices:
            content = getattr(_resp.choices[0].message, "content", "") or ""
        return {"healthy": bool(content), "latency_s": round(elapsed, 2), "error": None}
    except Exception as exc:  # noqa: BLE001
        return {
            "healthy": False,
            "latency_s": round(time_module.monotonic() - t0, 2),
            "error": f"{type(exc).__name__}: {exc}",
        }


def run_provider_health_check(
    provider_slug: str = "openai",
    model: str = "openrouter/openai/gpt-4o-mini",
) -> dict[str, Any]:
    """Raise RuntimeError if provider is not healthy. Call before ingestion."""
    result = asyncio.run(probe_provider(provider_slug, model))
    print(f"[guardrail:provider_health] healthy={result['healthy']} "
          f"latency={result['latency_s']}s error={result['error']}")
    if not result["healthy"]:
        raise RuntimeError(
            f"[GUARDRAIL FAIL] Provider health check FAILED: {result['error']}"
        )
    return result


# ---------------------------------------------------------------------------
# Canonical outcome mapping (mirrors orchestrator.eval.runner._extract_outcome)
# ---------------------------------------------------------------------------


def _canonical_outcome(status: str) -> str:
    """Derive outcome from status using the canonical mapping.

    Mirrors the logic in orchestrator.eval.runner._extract_outcome():
      - complete/completed -> completed
      - extraction_failed/error -> errored
      - extraction_timeout -> timed_out
      - else -> unknown
    """
    if status in ("completed", "complete"):
        return "completed"
    if status == "extraction_failed":
        return "errored"
    if status == "extraction_timeout":
        return "timed_out"
    if status == "error":
        return "errored"
    return "unknown"


# ---------------------------------------------------------------------------
# G3: Extraction errored-floor gate
# ---------------------------------------------------------------------------


def check_errored_floor(
    checkpoint: dict[str, Any],
    max_errored_rate: float = 5.0,
) -> dict[str, Any]:
    """Read checkpoint outcome counts, raise if errored% > max_errored_rate.

    Returns a dict with outcome_counts, errored_rate, and passed.
    """
    results = (
        checkpoint.get("phases", {})
        .get("ingest", {})
        .get("results", {})
    )

    outcome_counts = {"completed": 0, "errored": 0, "empty": 0, "timed_out": 0, "unknown": 0}
    for r in results.values():
        outcome = _canonical_outcome(r.get("status", ""))
        if outcome in outcome_counts:
            outcome_counts[outcome] += 1

    total = len(results) or 1
    errored_rate = outcome_counts.get("errored", 0) / total * 100
    errored_ok = errored_rate <= max_errored_rate

    print(f"[guardrail:errored_floor] errored={errored_rate:.1f}% "
          f"(max={max_errored_rate}%) passed={errored_ok}")

    result = {
        "total_sessions": total,
        "outcome_counts": outcome_counts,
        "errored_rate": errored_rate,
        "max_errored_rate": max_errored_rate,
        "passed": errored_ok,
    }

    if not errored_ok:
        raise AssertionError(
            f"[GUARDRAIL FAIL] Errored floor BREACH: "
            f"errored={errored_rate:.1f}% (max={max_errored_rate}%)"
        )
    return result


# ---------------------------------------------------------------------------
# G5: Credit instrumentation (log only — not a blocking guardrail)
# ---------------------------------------------------------------------------


def log_credit_instrumentation(context: str = "benchmark_run") -> None:
    """Log available credit/quota info. Always runs, never fails."""
    video_credits = os.environ.get("VIDEO_CREDITS_BALANCE")
    has_openrouter = os.environ.get("OPENROUTER_API_KEY") is not None
    print(f"[guardrail:credit] context={context} "
          f"video_credits={video_credits or 'N/A'} "
          f"openrouter_key={'present' if has_openrouter else 'absent'}")
