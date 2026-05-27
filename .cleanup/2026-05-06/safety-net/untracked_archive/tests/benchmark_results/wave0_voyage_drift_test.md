# Wave 0 — Voyage Embedding Drift Diagnostic

**Generated:** 2026-04-23 02:36:42 UTC
**Runtime:** 8.7s

---

## Method

A fixed document string and a fixed query string are each embedded 10
times directly against the Voyage API (no production code paths):

- **Document set:** `voyage-4-large`, `input_type=document`, 10 calls
- **Query set:** `voyage-4-lite`, `input_type=query`, 10 calls

For each set, all pairwise cosine similarities are computed, and byte-identity is checked
across all unique pairs (10 calls → 45 pairs per set).

---

## Results Summary

| Mode | Model | input_type | Calls | Dim | Cosine Min | Cosine Max | Cosine Mean | All Identical? |
|---|---|---|---|---|---|---|---|---|
| document (voyage-4-large) | voyage-4-large | document | 10 | 1024 | 1.0 | 1.0 | 1.0 | YES |
| query (voyage-4-lite) | voyage-4-lite | query | 10 | 1024 | 0.999958 | 1.0 | 0.999992 | NO |

---

## Pairwise Cosine Details

### Document — voyage-4-large

- **Calls:** 10
- **Pairs evaluated:** 45
- **Cosine min / max / mean:** [1.0, 1.0] / 1.0
- **Byte-identical pairs:** 45 / 45
- **Non-identical pairs:** 0

### Query — voyage-4-lite

- **Calls:** 10
- **Pairs evaluated:** 45
- **Cosine min / max / mean:** [0.999958, 1.0] / 0.999992
- **Byte-identical pairs:** 36 / 45
- **Non-identical pairs:** 9

---

## Conclusion

**voyage-4-large (document)**: ALL 45 output pairs are byte-identical — Voyage is fully deterministic for this input.

**voyage-4-lite (query)**: 9/45 pairs are NOT byte-identical — embedding drift confirmed. Cosine range: [0.999958, 1.0], mean=0.999992.

---

*Diagnostic script: `tests/benchmark_harness/voyage_drift_test.py`*
*Wave 0 — Daemon project*
