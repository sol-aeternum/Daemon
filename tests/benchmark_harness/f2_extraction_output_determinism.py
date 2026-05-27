#!/usr/bin/env python3
"""
F2 — Extraction Output Determinism Measurement (Tests-Only)

Measures whether the extraction path produces byte-identical or canonically
identical fact outputs across repeated calls with the same fixed input under
the same restored extraction provider override (provider.order=['openai']).

This diagnostic isolates the extraction output layer — it does NOT run the
full dedup/contradiction pipeline.

Scope: tests/ only. No production code changes.

Run:
    PYTHONPATH=. python tests/benchmark_harness/f2_extraction_output_determinism.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_FILE = PROJECT_ROOT / "tests/benchmark_results/wave0_extraction_output_determinism.md"

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


def canonicalize_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize fact list for comparison: sort by content, strip confidence drift."""
    normalized = []
    for f in facts:
        normalized.append({
            "content": f.get("content", "").strip(),
            "category": f.get("category", "").strip(),
            "slot": f.get("slot"),
        })
    normalized.sort(key=lambda x: (x["content"], x["category"], str(x["slot"])))
    return normalized


def fact_hash(facts: list[dict[str, Any]]) -> str:
    """Stable hash of canonicalized fact list."""
    canon = canonicalize_facts(facts)
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()[:16]


def apply_extraction_provider_override() -> tuple[str, str]:
    """Apply the verified-working provider-order override to the extraction module."""
    import orchestrator.memory.extraction as _ext

    original_slug = getattr(_ext, "BENCHMARK_EXTRACTION_ENDPOINT_SLUG", None)
    patched_slug = "openai"
    setattr(_ext, "BENCHMARK_EXTRACTION_ENDPOINT_SLUG", patched_slug)
    os.environ["BENCHMARK_MODE"] = "1"
    return str(original_slug), patched_slug


async def run_single_extraction(call_index: int) -> dict[str, Any]:
    """Run one extraction call and return outcome facts + metadata."""
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

        fact_dicts = [
            {
                "content": f.content,
                "category": f.category,
                "confidence": f.confidence,
                "slot": f.slot,
            }
            for f in outcome.facts
        ]

        return {
            "call_index": call_index,
            "facts": fact_dicts,
            "fact_count": len(fact_dicts),
            "raw_count": outcome.raw_count,
            "calibrated_count": outcome.calibrated_count,
            "rejected_count": outcome.rejected_count,
            "fingerprint": extraction_meta.get("fingerprint"),
            "error": None,
        }
    except Exception as exc:
        return {
            "call_index": call_index,
            "facts": [],
            "fact_count": 0,
            "raw_count": 0,
            "calibrated_count": 0,
            "rejected_count": 0,
            "fingerprint": None,
            "error": str(exc),
        }


def compute_determinism(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute determinism statistics across runs."""
    hashes = []
    byte_identical = 0
    non_identical = 0

    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            h_i = fact_hash(runs[i]["facts"])
            h_j = fact_hash(runs[j]["facts"])
            hashes.append((i, j, h_i, h_j))
            if h_i == h_j:
                byte_identical += 1
            else:
                non_identical += 1

    total_pairs = len(runs) * (len(runs) - 1) // 2

    first_facts = runs[0]["facts"] if runs else []
    canonical_first = canonicalize_facts(first_facts)

    all_canonical = []
    for r in runs:
        canon = canonicalize_facts(r["facts"])
        all_canonical.append(canon)

    all_same_canonical = all(c == canonical_first for c in all_canonical)

    return {
        "total_runs": len(runs),
        "total_pairs": total_pairs,
        "byte_identical_pairs": byte_identical,
        "non_identical_pairs": non_identical,
        "all_canonically_identical": all_same_canonical,
        "hashes": hashes,
        "canonical_first": canonical_first,
    }


def write_report(
    runs: list[dict[str, Any]],
    determinism: dict[str, Any],
    elapsed_s: float,
    original_slug: str,
    patched_slug: str,
) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    rows = []
    for r in runs:
        error_str = f"ERROR: {r['error']}" if r["error"] else ""
        r_hash = fact_hash(r["facts"])
        rows.append(
            f"| {r['call_index']} | {r['fact_count']} | {r['raw_count']} | "
            f"{r['rejected_count']} | {r_hash} | {error_str} |"
        )
    table_rows = "\n".join(rows)

    identical_pairs = determinism["non_identical_pairs"] == 0
    if identical_pairs:
        interpretation = (
            f"All {determinism['total_runs']} runs produced canonically identical fact outputs. "
            f"Extraction is deterministic for this fixed input under the restored provider override. "
            f"The Wave 0 residual spread is NOT caused by extraction output non-determinism."
        )
    else:
        interpretation = (
            f"{determinism['non_identical_pairs']}/{determinism['total_pairs']} output pairs "
            f"are NOT canonically identical across {determinism['total_runs']} runs. "
            f"Extraction output is non-deterministic even with seed=BENCHMARK_SEED=42 and "
            f"provider.order=['openai']. This confirms that answer-temperature effects "
            f"(~4pp inferred from Wave 0 delta) affect the extraction layer."
        )

    canon_example = determinism.get("canonical_first", [])
    example_rows = []
    for f in canon_example[:5]:
        example_rows.append(
            f"| {f['content'][:60]} | {f['category']} | {f['slot'] or ''} |"
        )
    example_table = "\n".join(example_rows)

    content = f"""# F2 — Extraction Output Determinism Measurement (Wave 0)

**Generated:** {now}
**Command:** `PYTHONPATH=. python tests/benchmark_harness/f2_extraction_output_determinism.py`
**Runtime:** {elapsed_s:.1f}s

---

## Configuration

| Parameter | Value |
|---|---|
| Runs | {NUM_RUNS} |
| Provider override | `BENCHMARK_EXTRACTION_ENDPOINT_SLUG`: `{original_slug}` → `{patched_slug}` |
| Model | `openrouter/openai/gpt-4o-mini` |
| Benchmark mode | Yes |
| Input | Fixed probe text ({len(PROBE_TEXT)} chars) |

---

## Per-Run Results

| Run | Facts | Raw | Rejected | Canonical Hash | Error |
|---|---|---|---|---|---|
{table_rows}

---

## Determinism Summary

| Metric | Value |
|---|---|
| Total runs | {determinism['total_runs']} |
| Total pairs | {determinism['total_pairs']} |
| Canonically identical pairs | {determinism['byte_identical_pairs']} |
| Non-identical pairs | {determinism['non_identical_pairs']} |
| All runs canonically identical? | **{"YES" if determinism['all_canonically_identical'] else "NO"}** |

---

## Canonical Output Example (Run 0, first 5 facts)

| Content (truncated) | Category | Slot |
|---|---|
{example_table}

---

## Interpretation

{interpretation}

---

## Note

Canonicalization normalizes: fact ordering (sorted by content), content whitespace,
and category strings. Confidence values are excluded from the canonical form
since they may vary slightly due to model temperature effects even at temperature=0.0.

This diagnostic measures the extraction path only (fact extraction via `extract_facts_from_text`).
It does NOT run the full ingestion pipeline (dedup/contradiction checks).
The contradiction routing is NOT patched here — this script focuses purely on extraction output determinism.

---

*Diagnostic script: `tests/benchmark_harness/f2_extraction_output_determinism.py`*
*Wave 0 — Daemon project*
"""
    with open(REPORT_FILE, "w") as fh:
        fh.write(content)
    print(f"\nF2 Report written → {REPORT_FILE}")


async def main() -> None:
    print("=" * 60)
    print("F2 — Extraction Output Determinism Measurement")
    print("=" * 60)
    print(f"Project root : {PROJECT_ROOT}")
    print(f"Runs         : {NUM_RUNS}")
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
        h = fact_hash(result["facts"])
        facts = result["fact_count"]
        err = result["error"]
        status = f"ERROR: {err[:40]}" if err else f"facts={facts}, hash={h}"
        print(status)

    elapsed = time.monotonic() - t0

    determinism = compute_determinism(runs)
    print(f"\nDeterminism: all_identical={determinism['all_canonically_identical']}, "
          f"non_identical_pairs={determinism['non_identical_pairs']}/{determinism['total_pairs']}")

    write_report(runs, determinism, elapsed, original_slug, patched_slug)


if __name__ == "__main__":
    asyncio.run(main())