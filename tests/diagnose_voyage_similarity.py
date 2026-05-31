#!/usr/bin/env python3
"""
Voyage Similarity Diagnostic Script

Embeds all 30 benchmark facts using voyage-4-large (same as production dedup),
computes pairwise cosine similarity matrix, and analyzes distribution to determine
if the 0.75 supersede threshold causes false positives.

Run: python tests/diagnose_voyage_similarity.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.memory.embedding import embed_documents


# The 30 benchmark facts extracted from benchmark_extraction.py SCENARIOS
# Format: (fact_text, scenario_label, description)
BENCHMARK_FACTS: list[tuple[str, str, str]] = [
    # S1 - Dense Personal Facts (9 facts)
    ("julian", "S1", "name"),
    ("28 years", "S1", "age"),
    ("adelaide", "S1", "location"),
    ("software engineer", "S1", "job"),
    ("brother callan", "S1", "sibling"),
    ("dog koda", "S1", "pet"),
    ("python", "S1", "primary language"),
    ("typescript", "S1", "secondary language"),
    ("neovim", "S1", "editor preference"),
    # S2 - Ephemeral vs Durable (1 fact)
    ("move melbourne", "S2", "relocation plan"),
    # S3 - Corrections/Supersession (1 fact)
    ("tesla model 3", "S3", "current vehicle"),
    # S4 - Projects and Goals (3 facts)
    ("daemon ai assistant", "S4", "project"),
    ("rust", "S4", "learning goal"),
    ("memories active", "S4", "memory system issue"),
    # S5 - Hedged Statements (6 facts)
    ("shellfish", "S5", "allergy definite"),
    ("lactose", "S5", "intolerance hedged"),
    ("girlfriend", "S5", "has girlfriend"),
    ("girlfriend cat", "S5", "girlfriend wants cat"),
    ("cat", "S5", "considering cat hedged"),
    ("japan october", "S5", "Japan trip hedged"),
    # S6 - Realistic Multi-Turn (7 facts)
    ("9950x3d", "S6", "CPU choice"),
    ("be quiet light base", "S6", "case"),
    ("cachyos", "S6", "distro preference"),
    ("arch", "S6", "Arch experience"),
    ("birthday march", "S6", "birthday"),
    ("tailscale", "S6", "remote access"),
    ("llm", "S6", "server purpose"),
    # S7 - Explicit Instructions (3 facts)
    ("aws 123456789012", "S7", "AWS account"),
    ("ap-southeast-2", "S7", "deploy region"),
    ("yaml", "S7", "YAML hatred"),
    # S8 - Adversarial Empty (0 facts)
]


@dataclass
class SimilarityStats:
    min: float
    max: float
    mean: float
    p25: float
    p50: float
    p75: float
    p95: float
    count: int


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def compute_similarity_matrix(embeddings: list[np.ndarray]) -> np.ndarray:
    n = len(embeddings)
    matrix = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            matrix[i, j] = cosine_similarity(embeddings[i], embeddings[j])
    return matrix


def get_stats(values: np.ndarray) -> SimilarityStats:
    return SimilarityStats(
        min=float(np.min(values)),
        max=float(np.max(values)),
        mean=float(np.mean(values)),
        p25=float(np.percentile(values, 25)),
        p50=float(np.percentile(values, 50)),
        p75=float(np.percentile(values, 75)),
        p95=float(np.percentile(values, 95)),
        count=len(values),
    )


def group_by_scenario(facts: list[tuple[str, str, str]]) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {}
    for idx, (_, scenario, _) in enumerate(facts):
        if scenario not in groups:
            groups[scenario] = []
        groups[scenario].append(idx)
    return groups


async def main() -> None:
    print("=" * 60)
    print("Voyage Similarity Diagnostic")
    print("=" * 60)
    print(f"Embedding {len(BENCHMARK_FACTS)} facts with voyage-4-large (document)")
    print()

    fact_texts = [fact[0] for fact in BENCHMARK_FACTS]
    fact_scenarios = [fact[1] for fact in BENCHMARK_FACTS]
    fact_descriptions = [fact[2] for fact in BENCHMARK_FACTS]

    print("Calling Voyage API...")
    embeddings = await embed_documents(fact_texts)
    print(f"Got {len(embeddings)} embeddings, dimension: {len(embeddings[0])}")
    print()

    np_embeddings = [np.array(e) for e in embeddings]

    print("Computing similarity matrix...")
    sim_matrix = compute_similarity_matrix(np_embeddings)
    print(f"Matrix shape: {sim_matrix.shape}")
    print()

    scenario_groups = group_by_scenario(BENCHMARK_FACTS)
    print("Scenario groupings:")
    for scenario, indices in sorted(scenario_groups.items()):
        print(f"  {scenario}: {len(indices)} facts")
    print()

    within_scenario: list[float] = []
    cross_scenario: list[float] = []

    for i in range(len(BENCHMARK_FACTS)):
        for j in range(i + 1, len(BENCHMARK_FACTS)):
            sim = sim_matrix[i, j]
            if fact_scenarios[i] == fact_scenarios[j]:
                within_scenario.append(sim)
            else:
                cross_scenario.append(sim)

    within_array = np.array(within_scenario)
    cross_array = np.array(cross_scenario)

    within_stats = get_stats(within_array)
    cross_stats = get_stats(cross_array)
    all_stats = get_stats(sim_matrix[np.triu_indices(len(BENCHMARK_FACTS), k=1)])

    print("=" * 60)
    print("SIMILARITY DISTRIBUTION STATISTICS")
    print("=" * 60)
    print()
    print("ALL PAIRS (n={})".format(len(within_scenario) + len(cross_scenario)))
    print(f"  min:   {all_stats.min:.4f}")
    print(f"  max:   {all_stats.max:.4f}")
    print(f"  mean:  {all_stats.mean:.4f}")
    print(f"  p25:   {all_stats.p25:.4f}")
    print(f"  p50:   {all_stats.p50:.4f}")
    print(f"  p75:   {all_stats.p75:.4f}")
    print(f"  p95:   {all_stats.p95:.4f}")
    print()
    print("WITHIN-SCENARIO PAIRS (n={})".format(len(within_scenario)))
    print(f"  min:   {within_stats.min:.4f}")
    print(f"  max:   {within_stats.max:.4f}")
    print(f"  mean:  {within_stats.mean:.4f}")
    print(f"  p25:   {within_stats.p25:.4f}")
    print(f"  p50:   {within_stats.p50:.4f}")
    print(f"  p75:   {within_stats.p75:.4f}")
    print(f"  p95:   {within_stats.p95:.4f}")
    print()
    print("CROSS-SCENARIO PAIRS (n={})".format(len(cross_scenario)))
    print(f"  min:   {cross_stats.min:.4f}")
    print(f"  max:   {cross_stats.max:.4f}")
    print(f"  mean:  {cross_stats.mean:.4f}")
    print(f"  p25:   {cross_stats.p25:.4f}")
    print(f"  p50:   {cross_stats.p50:.4f}")
    print(f"  p75:   {cross_stats.p75:.4f}")
    print(f"  p95:   {cross_stats.p95:.4f}")
    print()

    THRESHOLD = 0.75
    high_similarity_pairs: list[dict[str, Any]] = []
    cross_threshold: list[dict[str, Any]] = []

    print("=" * 60)
    print(f"PAIRS ABOVE {THRESHOLD} THRESHOLD (potential false dedup)")
    print("=" * 60)
    print()

    for i in range(len(BENCHMARK_FACTS)):
        for j in range(i + 1, len(BENCHMARK_FACTS)):
            sim = float(sim_matrix[i, j])
            if sim >= THRESHOLD:
                pair_info = {
                    "index_a": i,
                    "index_b": j,
                    "fact_a": fact_texts[i],
                    "fact_b": fact_texts[j],
                    "scenario_a": fact_scenarios[i],
                    "scenario_b": fact_scenarios[j],
                    "desc_a": fact_descriptions[i],
                    "desc_b": fact_descriptions[j],
                    "similarity": round(sim, 4),
                    "same_scenario": fact_scenarios[i] == fact_scenarios[j],
                }
                high_similarity_pairs.append(pair_info)

    if high_similarity_pairs:
        high_similarity_pairs.sort(key=lambda x: -x["similarity"])

        within_threshold = [p for p in high_similarity_pairs if p["same_scenario"]]
        cross_threshold.clear()
        cross_threshold.extend(p for p in high_similarity_pairs if not p["same_scenario"])

        print(f"Found {len(high_similarity_pairs)} pairs >= {THRESHOLD}:")
        print()

        if within_threshold:
            print(f"  WITHIN-SCENARIO ({len(within_threshold)} pairs):")
            for p in within_threshold:
                print(
                    f"    [{p['scenario_a']}] '{p['fact_a']}' <-> '{p['fact_b']}' = {p['similarity']:.4f}"
                )
            print()

        if cross_threshold:
            print(f"  CROSS-SCENARIO ({len(cross_threshold)} pairs):")
            for p in cross_threshold:
                print(
                    f"    [{p['scenario_a']}] '{p['fact_a']}' <-> [{p['scenario_b']}] '{p['fact_b']}' = {p['similarity']:.4f}"
                )
            print()
    else:
        print(f"  No pairs found above {THRESHOLD} threshold.")
        print()

    print("=" * 60)
    print("PER-SCENARIO STATISTICS")
    print("=" * 60)
    print()

    scenario_stats: dict[str, dict[str, float]] = {}
    for scenario, indices in sorted(scenario_groups.items()):
        if len(indices) < 2:
            scenario_stats[scenario] = {
                "min": 0.0,
                "max": 0.0,
                "mean": 0.0,
                "count": 0,
            }
            continue

        scenario_sims = []
        for i_idx, i in enumerate(indices):
            for j in indices[i_idx + 1 :]:
                scenario_sims.append(float(sim_matrix[i, j]))

        if scenario_sims:
            arr = np.array(scenario_sims)
            scenario_stats[scenario] = {
                "min": float(np.min(arr)),
                "max": float(np.max(arr)),
                "mean": float(np.mean(arr)),
                "count": len(scenario_sims),
            }
            print(f"  {scenario} ({len(indices)} facts, {len(scenario_sims)} pairs):")
            print(f"    min:  {scenario_stats[scenario]['min']:.4f}")
            print(f"    max:  {scenario_stats[scenario]['max']:.4f}")
            print(f"    mean: {scenario_stats[scenario]['mean']:.4f}")
            print()

    results_dir = Path("tests/results")
    results_dir.mkdir(parents=True, exist_ok=True)
    output_path = results_dir / "voyage_similarity_analysis.json"

    output: dict[str, Any] = {
        "run_timestamp": asyncio.get_event_loop().time(),
        "model": "voyage-4-large",
        "input_type": "document",
        "threshold": THRESHOLD,
        "total_facts": len(BENCHMARK_FACTS),
        "total_pairs": len(within_scenario) + len(cross_scenario),
        "within_scenario_pairs": len(within_scenario),
        "cross_scenario_pairs": len(cross_scenario),
        "pairs_above_threshold": len(high_similarity_pairs),
        "distribution": {
            "all": {
                "min": all_stats.min,
                "max": all_stats.max,
                "mean": all_stats.mean,
                "p25": all_stats.p25,
                "p50": all_stats.p50,
                "p75": all_stats.p75,
                "p95": all_stats.p95,
            },
            "within_scenario": {
                "min": within_stats.min,
                "max": within_stats.max,
                "mean": within_stats.mean,
                "p25": within_stats.p25,
                "p50": within_stats.p50,
                "p75": within_stats.p75,
                "p95": within_stats.p95,
            },
            "cross_scenario": {
                "min": cross_stats.min,
                "max": cross_stats.max,
                "mean": cross_stats.mean,
                "p25": cross_stats.p25,
                "p50": cross_stats.p50,
                "p75": cross_stats.p75,
                "p95": cross_stats.p95,
            },
        },
        "scenario_stats": scenario_stats,
        "high_similarity_pairs": high_similarity_pairs,
        "facts": [
            {
                "index": i,
                "text": fact_texts[i],
                "scenario": fact_scenarios[i],
                "description": fact_descriptions[i],
            }
            for i in range(len(BENCHMARK_FACTS))
        ],
        "similarity_matrix": sim_matrix.tolist(),
    }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Total facts: {len(BENCHMARK_FACTS)}")
    print(f"  Total pairs: {len(within_scenario) + len(cross_scenario)}")
    print(f"  Within-scenario: {len(within_scenario)}")
    print(f"  Cross-scenario: {len(cross_scenario)}")
    print(f"  Pairs >= {THRESHOLD}: {len(high_similarity_pairs)}")
    print()
    print(f"Results saved to: {output_path}")
    print()

    if cross_threshold:
        print("⚠️  WARNING: Cross-scenario pairs above threshold detected!")
        print("   This indicates the 0.75 threshold may cause false dedup triggers.")
    else:
        print("✓  No cross-scenario false positives detected at 0.75 threshold.")


if __name__ == "__main__":
    asyncio.run(main())
