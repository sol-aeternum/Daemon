"""Dry-run spend planner for the reasoning-routing evaluation (Package B, B2).

Makes no provider calls and reads no secrets. It computes the *worst-case* reservation
bound for the calibration run and the pilot defined in docs/REASONING_EVAL_PROTOCOL.md,
from the frozen corpus, the deployment policy's price ceilings and the runtime's own
conservative input bound, so a USD cap can be chosen from real numbers.

Worst case per model call = ceil((input_bound * prompt_ceiling
                                  + max_output_tokens * completion_ceiling) / 1e6)

The bound assumes every call uses the full output limit and that each attempt makes
its maximum number of calls; actual spend is normally far lower. The calibration run
exists to measure the real figures before the pilot cap is set.

Usage:
    PYTHONPATH=. uv run python scripts/reasoning_eval_plan.py [--max-output-tokens 4096]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.compute_runtime import _request_bound  # noqa: E402  # pyright: ignore[reportPrivateUsage]
from orchestrator.entitlements.policy import parse_inference_policy  # noqa: E402

CORPUS = ROOT / "tests" / "fixtures" / "reasoning_eval" / "corpus_v1.json"
POLICY = ROOT / "config" / "inference_policy.production.json"
SYSTEM_PROMPT_ALLOWANCE = "x" * 8_000  # conservative stand-in for the system prompt


@dataclass(frozen=True)
class Configuration:
    label: str
    route_id: str
    effort: str


CONFIGURATIONS = (
    Configuration("luna-low", "luna-azure-eu", "low"),
    Configuration("luna-medium", "luna-azure-eu", "medium"),
    Configuration("luna-high", "luna-azure-eu", "high"),
    Configuration("sonnet-5-high", "sonnet-vertex-europe", "high"),
    Configuration("sol-6.1-high", "sol-azure-eu", "high"),
)


def max_calls(case: dict[str, Any]) -> int:
    """Model calls an attempt may make: tool cases allow tool rounds plus an answer."""
    return 3 if case["tools"] else 1


def input_bound(case: dict[str, Any]) -> int:
    messages = [{"role": "system", "content": SYSTEM_PROMPT_ALLOWANCE}]
    messages += [dict(turn) for turn in case["history"]]
    messages.append({"role": "user", "content": case["prompt"]})
    # Later tool rounds append tool results; allow for them generously.
    if case["tools"]:
        messages.append({"role": "tool", "content": json.dumps(case["tool_responses"]) * 3})
    params: dict[str, Any] = {"messages": messages}
    if case["tools"]:
        params["tools"] = case["tools"]
    return _request_bound(params).bound


def calibration_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Two development cases per slice (the protocol's calibration run)."""
    return [case for case in cases if case["split"] == "dev"]


def pilot_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [case for case in cases if case["split"] in {"dev", "val"}]


def plan(max_output_tokens: int) -> dict[str, Any]:
    cases = json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]
    policy = parse_inference_policy(
        json.loads(POLICY.read_text(encoding="utf-8")), source_path=str(POLICY)
    )
    report: dict[str, Any] = {"max_output_tokens": max_output_tokens, "runs": {}}
    for run, chosen, repeats in (
        ("calibration", calibration_cases(cases), 1),
        ("pilot", pilot_cases(cases), 3),
    ):
        rows = []
        for configuration in CONFIGURATIONS:
            route = policy.routes[configuration.route_id]
            calls = 0
            bound = 0
            for case in chosen:
                per_call = route.estimate_microusd(input_bound(case), max_output_tokens)
                calls += max_calls(case) * repeats
                bound += per_call * max_calls(case) * repeats
            rows.append(
                {
                    "configuration": configuration.label,
                    "attempts": len(chosen) * repeats,
                    "max_calls": calls,
                    "worst_case_usd": round(bound / 1_000_000, 2),
                }
            )
        report["runs"][run] = {
            "cases": len(chosen),
            "repeats": repeats,
            "configurations": rows,
            "worst_case_total_usd": round(sum(row["worst_case_usd"] for row in rows), 2),
        }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run spend planner (no provider calls).")
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--json", action="store_true", help="print the raw report")
    args = parser.parse_args(argv)
    report = plan(args.max_output_tokens)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    for run, details in report["runs"].items():
        print(f"{run}: {details['cases']} cases x {details['repeats']} repeat(s)")
        for row in details["configurations"]:
            print(
                f"  {row['configuration']:<14} attempts={row['attempts']:<4} "
                f"max_calls={row['max_calls']:<4} worst_case=${row['worst_case_usd']:.2f}"
            )
        print(f"  worst-case total: ${details['worst_case_total_usd']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
