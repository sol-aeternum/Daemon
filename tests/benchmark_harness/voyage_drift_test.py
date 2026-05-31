#!/usr/bin/env python3
"""
Standalone Voyage Drift Diagnostic — Wave 0

Calls Voyage API directly (not through orchestrator/memory/) to measure
embedding output stability for:
  - voyage-4-large in document mode (fixed document string, 10 calls)
  - voyage-4-lite  in query  mode (fixed query  string, 10 calls)

For each set, computes:
  - Pairwise cosine similarity (min / max / mean across all pairs)
  - Whether any output pair is not byte-identical

Results are written to tests/benchmark_results/wave0_voyage_drift_test.md
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from datetime import datetime, timezone
from typing import Any

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HARNESS_DIR)
RESULT_DIR = os.path.join(ROOT, "benchmark_results")
RESULT_FILE = os.path.join(RESULT_DIR, "wave0_voyage_drift_test.md")

FIXED_DOCUMENT = (
    "The Ford Mustang Mach-E is an all-electric compact executive SUV produced "
    "by Ford. It was introduced in 2019 and deliveries began in 2020. The vehicle "
    "offers a range of up to 370 miles on a single charge depending on the battery "
    "configuration. It competes with the Tesla Model Y, the Hyundai Ioniq 5, and "
    "the Volkswagen ID.4. The name Mach-E pays homage to the classic Ford Mustang "
    "Mach 1 performance variant."
)

FIXED_QUERY = "What is the range of the Ford Mustang Mach-E electric vehicle?"


def cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def pairwise_stats(vectors: list[list[float]]) -> dict[str, Any]:
    n = len(vectors)
    if n < 2:
        return {"min": None, "max": None, "mean": None, "pair_count": 0}
    similarities: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            similarities.append(cosine_sim(vectors[i], vectors[j]))
    return {
        "min": round(min(similarities), 6),
        "max": round(max(similarities), 6),
        "mean": round(sum(similarities) / len(similarities), 6),
        "pair_count": len(similarities),
    }


def byte_identical_check(vectors: list[list[float]]) -> dict[str, Any]:
    n = len(vectors)
    identical_count = 0
    non_identical_pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            if vectors[i] == vectors[j]:
                identical_count += 1
            else:
                non_identical_pairs += 1
    total_pairs = n * (n - 1) // 2
    return {
        "all_identical": non_identical_pairs == 0,
        "identical_pairs": identical_count,
        "non_identical_pairs": non_identical_pairs,
        "total_pairs": total_pairs,
    }


async def call_voyage(
    texts: list[str],
    model: str,
    input_type: str,
    output_dimension: int,
    api_key: str,
) -> list[list[float]]:
    import httpx

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "input": texts,
                "model": model,
                "input_type": input_type,
                "output_dimension": output_dimension,
            },
        )
        response.raise_for_status()
        payload = response.json()
    data = payload.get("data", [])
    ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
    return [list(item.get("embedding", [])) for item in ordered]


async def run_batch(
    texts: list[str],
    model: str,
    input_type: str,
    api_key: str,
    output_dimension: int = 1024,
    *,
    num_calls: int,
) -> dict[str, Any]:
    all_vectors: list[list[float]] = []
    for call_idx in range(num_calls):
        vectors = await call_voyage(
            texts=texts,
            model=model,
            input_type=input_type,
            output_dimension=output_dimension,
            api_key=api_key,
        )
        if len(vectors) == 1:
            all_vectors.append(vectors[0])
        else:
            all_vectors.extend(vectors)
        print(
            f"  [{model}/{input_type}] call {call_idx + 1}/{num_calls} → "
            f"got {len(vectors)} vector(s), dim={len(vectors[0]) if vectors else 0}"
        )
    return {
        "model": model,
        "input_type": input_type,
        "num_calls": num_calls,
        "vector_count": len(all_vectors),
        "vector_dimension": len(all_vectors[0]) if all_vectors else 0,
        "pairwise_cosine": pairwise_stats(all_vectors),
        "byte_identity": byte_identical_check(all_vectors),
    }


def write_markdown_report(
    document_result: dict[str, Any],
    query_result: dict[str, Any],
    elapsed_s: float,
    api_key_present: bool,
) -> None:
    os.makedirs(RESULT_DIR, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    def cosine_row(label: str, res: dict[str, Any]) -> str:
        c = res["pairwise_cosine"]
        identity = res["byte_identity"]
        cos = c if c else {}
        return (
            f"| {label} | {res['model']} | {res['input_type']} | "
            f"{res['num_calls']} | {res['vector_dimension']} | "
            f"{cos.get('min', 'N/A')} | {cos.get('max', 'N/A')} | "
            f"{cos.get('mean', 'N/A')} | "
            f"{'YES' if identity.get('all_identical') else 'NO'} |"
        )

    rows = "\n".join(
        [
            cosine_row("document (voyage-4-large)", document_result),
            cosine_row("query (voyage-4-lite)", query_result),
        ]
    )

    conclusion_parts: list[str] = []
    for label, res in [
        ("voyage-4-large (document)", document_result),
        ("voyage-4-lite (query)", query_result),
    ]:
        identity = res["byte_identity"]
        cos = res["pairwise_cosine"]
        if identity["all_identical"]:
            conclusion_parts.append(
                f"**{label}**: ALL {identity['total_pairs']} output pairs are byte-identical — "
                f"Voyage is fully deterministic for this input."
            )
        else:
            conclusion_parts.append(
                f"**{label}**: {identity['non_identical_pairs']}/{identity['total_pairs']} pairs "
                f"are NOT byte-identical — embedding drift confirmed. "
                f"Cosine range: [{cos['min']}, {cos['max']}], mean={cos['mean']}."
            )

    if not api_key_present:
        conclusion_parts = [
            "API key not available — no real API calls were made. "
            "Results are placeholder. Set VOYAGE_API_KEY to run the diagnostic."
        ]

    conclusion = "\n\n".join(conclusion_parts)

    content = f"""# Wave 0 — Voyage Embedding Drift Diagnostic

**Generated:** {now}
**Runtime:** {elapsed_s:.1f}s

---

## Method

A fixed document string and a fixed query string are each embedded {document_result["num_calls"]}
times directly against the Voyage API (no production code paths):

- **Document set:** `voyage-4-large`, `input_type=document`, {document_result["num_calls"]} calls
- **Query set:** `voyage-4-lite`, `input_type=query`, {query_result["num_calls"]} calls

For each set, all pairwise cosine similarities are computed, and byte-identity is checked
across all unique pairs ({document_result["num_calls"]} calls → {document_result["num_calls"] * (document_result["num_calls"] - 1) // 2} pairs per set).

---

## Results Summary

| Mode | Model | input_type | Calls | Dim | Cosine Min | Cosine Max | Cosine Mean | All Identical? |
|---|---|---|---|---|---|---|---|---|
{rows}

---

## Pairwise Cosine Details

### Document — voyage-4-large

- **Calls:** {document_result["num_calls"]}
- **Pairs evaluated:** {document_result["pairwise_cosine"].get("pair_count", "N/A")}
- **Cosine min / max / mean:** [{document_result["pairwise_cosine"].get("min", "N/A")}, {document_result["pairwise_cosine"].get("max", "N/A")}] / {document_result["pairwise_cosine"].get("mean", "N/A")}
- **Byte-identical pairs:** {document_result["byte_identity"].get("identical_pairs", "N/A")} / {document_result["byte_identity"].get("total_pairs", "N/A")}
- **Non-identical pairs:** {document_result["byte_identity"].get("non_identical_pairs", "N/A")}

### Query — voyage-4-lite

- **Calls:** {query_result["num_calls"]}
- **Pairs evaluated:** {query_result["pairwise_cosine"].get("pair_count", "N/A")}
- **Cosine min / max / mean:** [{query_result["pairwise_cosine"].get("min", "N/A")}, {query_result["pairwise_cosine"].get("max", "N/A")}] / {query_result["pairwise_cosine"].get("mean", "N/A")}
- **Byte-identical pairs:** {query_result["byte_identity"].get("identical_pairs", "N/A")} / {query_result["byte_identity"].get("total_pairs", "N/A")}
- **Non-identical pairs:** {query_result["byte_identity"].get("non_identical_pairs", "N/A")}

---

## Conclusion

{conclusion}

---

*Diagnostic script: `tests/benchmark_harness/voyage_drift_test.py`*
*Wave 0 — Daemon project*
"""
    with open(RESULT_FILE, "w") as fh:
        fh.write(content)
    print(f"\nReport written → {RESULT_FILE}")


async def main() -> None:
    api_key = os.environ.get("VOYAGE_API_KEY", "")
    api_key_present = bool(api_key)

    if not api_key_present:
        print("WARNING: VOYAGE_API_KEY not set — diagnostic cannot run against real API.")
        print("         Writing placeholder report...")
        doc_result = {
            "model": "voyage-4-large",
            "input_type": "document",
            "num_calls": 10,
            "vector_count": 10,
            "vector_dimension": 0,
            "pairwise_cosine": {"min": None, "max": None, "mean": None, "pair_count": 45},
            "byte_identity": {
                "all_identical": False,
                "identical_pairs": 0,
                "non_identical_pairs": 45,
                "total_pairs": 45,
            },
        }
        query_result = {
            "model": "voyage-4-lite",
            "input_type": "query",
            "num_calls": 10,
            "vector_count": 10,
            "vector_dimension": 0,
            "pairwise_cosine": {"min": None, "max": None, "mean": None, "pair_count": 45},
            "byte_identity": {
                "all_identical": False,
                "identical_pairs": 0,
                "non_identical_pairs": 45,
                "total_pairs": 45,
            },
        }
        write_markdown_report(doc_result, query_result, elapsed_s=0.0, api_key_present=False)
        return

    start = time.monotonic()
    print("=== Voyage Drift Diagnostic ===")
    print(f"Fixed document: {FIXED_DOCUMENT[:60]}...")
    print(f"Fixed query:    {FIXED_QUERY[:60]}...")
    print()

    print("[1/2] Document — voyage-4-large (10 calls) ...")
    doc_result = await run_batch(
        texts=[FIXED_DOCUMENT],
        model="voyage-4-large",
        input_type="document",
        api_key=api_key,
        output_dimension=1024,
        num_calls=10,
    )
    print()

    print("[2/2] Query — voyage-4-lite (10 calls) ...")
    query_result = await run_batch(
        texts=[FIXED_QUERY],
        model="voyage-4-lite",
        input_type="query",
        api_key=api_key,
        output_dimension=1024,
        num_calls=10,
    )
    print()

    elapsed = time.monotonic() - start
    print(f"Done in {elapsed:.1f}s")
    write_markdown_report(doc_result, query_result, elapsed, api_key_present=True)


if __name__ == "__main__":
    asyncio.run(main())
