#!/usr/bin/env python3
"""
D4 — Extraction Provider-Order Override (Tests-Only Runtime Patch)

This harness applies a runtime patch to the extraction benchmark path, overriding
BENCHMARK_EXTRACTION_ENDPOINT_SLUG from the broken full-model-slug value to the
verified-working "openai" provider-name only.

Verified by prior direct probe:
  - provider.order=['openrouter/openai/gpt-4o-mini-2024-07-18'] → 404
  - provider.order=['openai'] + allow_fallbacks=false → 200 success

Scope: tests/ only. No production code changes.

Run:
    PYTHONPATH=. python tests/benchmark_harness/extraction_provider_override.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Ensure project root on path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ------------------------------------------------------------------
# RUNTIME PATCH — applied before extraction module is used
# ------------------------------------------------------------------
# The broken value: "openrouter/openai/gpt-4o-mini-2024-07-18"
# causes OpenRouter to return 404 when used as provider.order entry.
# The verified-working value is just the provider name "openai".
_EXTRACTION_MODULE_PATH = "orchestrator.memory.extraction"
_extraction_module = __import__(
    _EXTRACTION_MODULE_PATH, fromlist=["BENCHMARK_EXTRACTION_ENDPOINT_SLUG"]
)

# Save original for reference
_original_slug = getattr(_extraction_module, "BENCHMARK_EXTRACTION_ENDPOINT_SLUG", None)
print(f"[D4 override] Original BENCHMARK_EXTRACTION_ENDPOINT_SLUG = {_original_slug!r}")

# Apply the override — this is a tests-only runtime patch
_fixed_slug = "openai"
setattr(_extraction_module, "BENCHMARK_EXTRACTION_ENDPOINT_SLUG", _fixed_slug)
print(f"[D4 override] Patched BENCHMARK_EXTRACTION_ENDPOINT_SLUG = {_fixed_slug!r}")

# Also ensure BENCHMARK_MODE is active for this process
os.environ["BENCHMARK_MODE"] = "1"
print("[D4 override] BENCHMARK_MODE=1 set in environment")

# ------------------------------------------------------------------
# SINGLE-CALL VERIFICATION PROBE
# ------------------------------------------------------------------


async def run_probe() -> bool:
    from orchestrator.memory.extraction import extract_facts_from_text

    # Minimal conversation text that should produce at least one valid fact
    # when extraction works correctly
    probe_text = """
[User]: Hi, I'm Alex, I'm 32 years old, and I live in Sydney, Australia.
[Assistant]: Hello Alex! That's great to know.
[User]: I work as a software engineer and I use Python and TypeScript every day.
[Assistant]: That sounds like a solid tech stack!
[User]: I have a dog named Bella and I love coffee.
[Assistant]: Bella sounds adorable! And coffee is always a good choice.
    """.strip()

    print("\n[D4 probe] Calling extract_facts_from_text() in benchmark mode...")

    try:
        outcome = await extract_facts_from_text(
            text=probe_text,
            model="openrouter/openai/gpt-4o-mini",  # model arg is ignored when benchmark_mode picks up BENCHMARK_MODE
            benchmark_mode=True,
        )

        print(
            f"[D4 probe] ExtractionOutcome: raw={outcome.raw_count}, "
            f"calibrated={outcome.calibrated_count}, "
            f"validated={len(outcome.facts)}, "
            f"rejected={outcome.rejected_count}"
        )

        if outcome.facts:
            print("[D4 probe] Sample extracted facts:")
            for fact in outcome.facts[:3]:
                print(f"  - [{fact.category}] {fact.content[:60]}... (conf={fact.confidence})")

        # Success = we got at least one validated fact without exceptions
        success = len(outcome.facts) >= 1
        print(f"\n[D4 probe] RESULT: {'PASS' if success else 'FAIL'}")
        return success

    except Exception as exc:
        print(f"[D4 probe] EXCEPTION: {type(exc).__name__}: {exc}")
        print("[D4 probe] RESULT: FAIL")
        return False


def main() -> int:
    print("=" * 60)
    print("D4 — Extraction Provider-Order Override (Tests-Only)")
    print("=" * 60)
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Original slug: {_original_slug!r}")
    print(f"Patched slug:  {_fixed_slug!r}")
    print(f"BENCHMARK_MODE: {os.environ.get('BENCHMARK_MODE')!r}")
    print("-" * 60)

    success = asyncio.run(run_probe())

    print("-" * 60)
    if success:
        print("D4 VERIFICATION: PASSED")
        print("Extraction benchmark call succeeded under provider-order override.")
        return 0
    else:
        print("D4 VERIFICATION: FAILED")
        print("Extraction benchmark call did NOT succeed under provider-order override.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
