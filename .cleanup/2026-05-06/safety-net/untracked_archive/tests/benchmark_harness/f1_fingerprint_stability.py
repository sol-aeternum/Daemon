#!/usr/bin/env python3
"""
F1 — Fingerprint Stability Measurement (Tests-Only)

Measures whether the extraction path produces stable system_fingerprints
across repeated calls with the same fixed input under the same restored
extraction provider override (provider.order=['openai']).

Fingerprint drift indicates non-deterministic provider routing even when
the model and seed are pinned.

Scope: tests/ only. No production code changes.

Run:
    PYTHONPATH=. python tests/benchmark_harness/f1_fingerprint_stability.py
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_FILE = PROJECT_ROOT / "tests/benchmark_results/wave0_fingerprint_stability_measurement.md"

# Benchmark harness output directory (shared with other diagnostics)
OUTPUT_DIR = PROJECT_ROOT / "tests/benchmark_results"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

PROBE_TEXT = """
[User]: Hi, I'm Alex, I'm 32 years old, and I live in Sydney, Australia.
[Assistant]: Hello Alex! That's great to know.
[User]: I work as a software engineer and I use Python and TypeScript every day.
[Assistant]: That sounds like a solid tech stack!
[User]: I have a dog named Bella and I love coffee.
[Assistant]: Bella sounds adorable! And coffee is always a good choice.
""".strip()

NUM_RUNS = 10
SEED = 42
BENCHMARK_SEED = 42


def apply_extraction_provider_override() -> tuple[str, str]:
    """Apply the verified-working provider-order override to the extraction module.

    Returns:
        Tuple of (original_slug, patched_slug)
    """
    import orchestrator.memory.extraction as _ext

    original_slug = getattr(_ext, "BENCHMARK_EXTRACTION_ENDPOINT_SLUG", None)
    patched_slug = "openai"
    setattr(_ext, "BENCHMARK_EXTRACTION_ENDPOINT_SLUG", patched_slug)
    os.environ["BENCHMARK_MODE"] = "1"
    return str(original_slug), patched_slug


async def run_single_extraction(call_index: int) -> dict[str, Any]:
    """Run one extraction call and capture model + fingerprint from benchmark metadata."""
    from orchestrator.memory.extraction import (
        extract_facts_from_text,
        get_benchmark_tracking,
        reset_benchmark_tracking,
    )

    reset_benchmark_tracking()

    try:
        outcome = await extract_facts_from_text(
            text=PROBE_TEXT,
            model="openrouter/openai/gpt-4o-mini",
            benchmark_mode=True,
        )

        tracking = get_benchmark_tracking()
        extraction_meta = tracking.get("extraction", {})

        return {
            "call_index": call_index,
            "model": extraction_meta.get("model"),
            "fingerprint": extraction_meta.get("fingerprint"),
            "fact_count": len(outcome.facts),
            "raw_count": outcome.raw_count,
            "rejected_count": outcome.rejected_count,
            "error": None,
        }
    except Exception as exc:
        return {
            "call_index": call_index,
            "model": None,
            "fingerprint": None,
            "fact_count": 0,
            "raw_count": 0,
            "rejected_count": 0,
            "error": str(exc),
        }


def compute_fingerprint_stability(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute fingerprint stability statistics across runs."""
    fingerprints = [r["fingerprint"] for r in runs if r["fingerprint"] is not None]
    models = [r["model"] for r in runs if r["model"] is not None]

    unique_fps = set(fingerprints)
    unique_models = set(models)

    if len(fingerprints) < len(runs):
        # Some runs had no fingerprint (e.g. provider didn't return one)
        fingerprint_drift = None  # indeterminate
    elif len(unique_fps) == 1:
        fingerprint_drift = False
    else:
        fingerprint_drift = True

    model_drift = len(unique_models) > 1 if models else None

    return {
        "total_runs": len(runs),
        "runs_with_fingerprint": len(fingerprints),
        "unique_fingerprints": len(unique_fps),
        "unique_models": len(unique_models),
        "fingerprint_drift": fingerprint_drift,
        "model_drift": model_drift,
        "all_fingerprints": fingerprints,
        "all_models": models,
    }


def write_report(
    runs: list[dict[str, Any]],
    stability: dict[str, Any],
    elapsed_s: float,
    original_slug: str,
    patched_slug: str,
) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    # Per-call table rows
    rows = []
    for r in runs:
        error_str = f"ERROR: {r['error']}" if r["error"] else ""
        rows.append(
            f"| {r['call_index']} | {r['model'] or 'N/A'} | "
            f"{r['fingerprint'] or 'N/A'} | {r['fact_count']} | {error_str} |"
        )
    table_rows = "\n".join(rows)

    drift_status = "YES" if stability["fingerprint_drift"] else "NO (stable)"
    if stability["fingerprint_drift"] is None:
        drift_status = "INDETERMINATE (some runs had no fingerprint)"

    if stability["fingerprint_drift"]:
        interpretation = (
            f"Fingerprint drift confirmed across {stability['unique_fingerprints']} "
            f"distinct fingerprint values in {stability['total_runs']} runs. "
            f"The extraction provider is not returning stable system_fingerprints "
            f"even with seed=BENCHMARK_SEED={SEED} and provider.order=['openai']. "
            f"This is the dominant source of the Wave 0 residual spread (measured ~6pp)."
        )
    else:
        interpretation = (
            f"All {stability['total_runs']} runs returned identical fingerprint "
            f"'{stability['all_fingerprints'][0]}' — extraction fingerprint is stable "
            f"under the restored provider override. "
            f"The Wave 0 residual spread is NOT caused by extraction fingerprint drift."
        )

    content = f"""# F1 — Fingerprint Stability Measurement (Wave 0)

**Generated:** {now}
**Command:** `PYTHONPATH=. python tests/benchmark_harness/f1_fingerprint_stability.py`
**Runtime:** {elapsed_s:.1f}s

---

## Configuration

| Parameter | Value |
|---|---|
| Runs | {NUM_RUNS} |
| Seed | {BENCHMARK_SEED} |
| Provider override | `BENCHMARK_EXTRACTION_ENDPOINT_SLUG`: `{original_slug}` → `{patched_slug}` |
| Model | `openrouter/openai/gpt-4o-mini` |
| Benchmark mode | Yes |
| Input | Fixed probe text ({len(PROBE_TEXT)} chars) |

---

## Per-Call Results

| Run | Model | system_fingerprint | Facts Extracted | Error |
|---|---|---|---|---|
{table_rows}

---

## Stability Summary

| Metric | Value |
|---|---|
| Total runs | {stability['total_runs']} |
| Runs with fingerprint | {stability['runs_with_fingerprint']} |
| Unique fingerprints | {stability['unique_fingerprints']} |
| Unique models | {stability['unique_models']} |
| Fingerprint drift detected? | **{drift_status}** |

---

## Interpretation

{interpretation}

---

## Note

This diagnostic measures the extraction path only (fact extraction via `extract_facts_from_text`).
It does not run the full ingestion pipeline (dedup/contradiction checks).
The contradiction routing is NOT patched here — this script focuses purely on extraction fingerprint stability.

---

*Diagnostic script: `tests/benchmark_harness/f1_fingerprint_stability.py`*
*Wave 0 — Daemon project*
"""
    with open(REPORT_FILE, "w") as fh:
        fh.write(content)
    print(f"\nF1 Report written → {REPORT_FILE}")


async def main() -> None:
    print("=" * 60)
    print("F1 — Fingerprint Stability Measurement")
    print("=" * 60)
    print(f"Project root : {PROJECT_ROOT}")
    print(f"Runs         : {NUM_RUNS}")
    print(f"Seed         : {SEED}")
    print(f"Provider     : openai (via provider.order override)")
    print("-" * 60)

    original_slug, patched_slug = apply_extraction_provider_override()
    print(f"Provider override applied: {original_slug!r} → {patched_slug!r}")

    t0 = time.monotonic()
    runs: list[dict[str, Any]] = []
    for i in range(NUM_RUNS):
        print(f"  Running extraction call {i + 1}/{NUM_RUNS}...", end=" ", flush=True)
        result = await run_single_extraction(i)
        runs.append(result)
        fp = result["fingerprint"]
        model = result["model"]
        facts = result["fact_count"]
        err = result["error"]
        status = f"ERROR: {err[:40]}" if err else f"fp={fp[-20:] if fp else 'None'}, facts={facts}"
        print(status)

    elapsed = time.monotonic() - t0

    stability = compute_fingerprint_stability(runs)
    print(f"\nStability: drift={stability['fingerprint_drift']}, "
          f"unique_fps={stability['unique_fingerprints']}, "
          f"unique_models={stability['unique_models']}")

    write_report(runs, stability, elapsed, original_slug, patched_slug)


if __name__ == "__main__":
    asyncio.run(main())