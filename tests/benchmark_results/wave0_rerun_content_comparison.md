# Wave 0 Rerun Content Comparison

**Generated:** 2026-04-25
**Scope:** Documentation only — no code changes

---

## Archived Rerun Directories

The three archived subset rerun directories are:

- `tests/benchmark_results/wave0_subset_rerun_run1/`
- `tests/benchmark_results/wave0_subset_rerun_run2/`
- `tests/benchmark_results/wave0_subset_rerun_run3/`

### Contents of Each Archived Rerun Directory

Each directory contains exactly two files:

| File | Present |
|---|---|
| `longmemeval_checkpoint.json` | Yes |
| Markdown report (`wave0_subset_rerun_run{N}.md`) | Yes |
| `run_metrics.json` | **No** |
| Per-run extracted-fact JSON payload | **No** |
| Extraction-log export | **No** |

Confirmed by direct `ls` of each directory:
- `run1/`: `longmemeval_checkpoint.json` + `wave0_subset_rerun_run1.md` only
- `run2/`: `longmemeval_checkpoint.json` + `wave0_subset_rerun_run2.md` only
- `run3/`: `longmemeval_checkpoint.json` + `wave0_subset_rerun_run3.md` only

### Checkpoint Contents

Each `longmemeval_checkpoint.json` is the benchmark-runner checkpoint produced by `LongMemEvalRunner`. It records per-session outcome (`completed`, `errored`, `empty`) and status (`complete`, `extraction_failed`). It does **not** contain the raw extracted-fact payloads from each run.

---

## Why V1 Content-Hash Comparison Cannot Be Completed

### DB State Limitation

The current database was reset and reused across multiple benchmark runs. The benchmark-user account's memory state reflects the **latest** run only. There is no stored record that distinguishes the three archived subset reruns from each other at the per-session level in the DB.

Therefore:
- The DB cannot supply per-run extracted-fact sets for hash comparison.
- Archived rerun directories do not contain per-run extracted-fact payloads.

### Artifact Limitation

A literal Step V1 content-hash comparison — computing a hash of each run's extracted-fact set and comparing across run1/run2/run3 — requires extracted-fact payloads. Those payloads do not exist in the archived directories and are not recoverable from the DB in a per-run-disaggregated form.

### Consequence for V2/V3 Decision Rules

V2 and V3 decision rules that depend on actual per-run extracted-fact hash values **cannot be applied** from existing artifacts. The triggering condition for those rules (a hash mismatch indicating non-deterministic extraction) has not been evaluated against the three archived reruns because the necessary input data (per-run extracted-fact content) is not available.

---

## Decision for V1

**Status: INSUFFICIENT ARTIFACTS**

V1 (literal content-hash comparison across three archived subset reruns) cannot be completed with existing evidence. The archived rerun directories do not contain per-run extracted-fact payloads, and the DB cannot disambiguate runs. This is not a measurement gap that can be filled retroactively — the artifacts were not preserved in a form that enables the comparison.

---

## Consequence for V5

Full-corpus baseline must **NOT** proceed on the basis of an unperformed V1 comparison.

No V1 result means:
- No confirmed baseline hash exists for the subset.
- V2/V3 decision rules have not been evaluated.
- Proceeding to full-corpus baseline without V1 validation would run against the documented gating logic.

V5 is blocked pending either:
1. A new, artifact-preserving rerun of the subset with explicit per-run extracted-fact export, or
2. A documented exception approved through the normal review channel.

---

*Documentation artifact: `tests/benchmark_results/wave0_rerun_content_comparison.md`*
*Wave 0 — Daemon project*
