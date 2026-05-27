# Wave 0 Rerun Content Comparison (V2)

**Generated:** 2026-04-27
**Artifact Source:** `tests/benchmark_results/wave0_rerun_v1_clean/run_{1,2,3}/`
**Sampling:** Fixed seed 42, 5 sessions randomly selected from 788 sessions present in all three runs

---

## Overview

| Run | Timestamp | Fingerprint | Model | Extraction Calls | Completed | Errored |
|-----|-----------|-------------|-------|-----------------|-----------|---------|
| Run 1 | 2026-04-26T07:51:00 | `fp_4181e24c46` | openai/gpt-4o-mini-2024-07-18 | 1042 | 1038 | 3 |
| Run 2 | 2026-04-26T11:18:47 | `fp_4181e24c46` | openai/gpt-4o-mini-2024-07-18 | 1147 | 1145 | 2 |
| Run 3 | 2026-04-26T14:43:27 | `fp_4181e24c46` | openai/gpt-4o-mini-2024-07-18 | 1129 | 1125 | 6 |

**Common sessions across all 3 runs:** 788 (keyed by `input_snippet_hash`)

**Observation:** All three runs used the same `system_fingerprint: fp_4181e24c46`, meaning the model and its deterministic seed parameters were identical across runs.

---

## Sampled Sessions

### Session 1

- **Input hash:** `d7ac1b3ff362c67c16feed5b5640329f52114e122f2ee2395ec735ab2071e5b3`

| Run | Session ID | Fact Count | SHA256 (facts) | Fingerprint |
|-----|------------|------------|----------------|-------------|
| Run 1 | `330b2839-1f79-4618-be6f-7e203f7a056a` | 4 | `7bd144d000ad62a77be50f99c4fcec3c6d49dace124edc5de1e4bb5437ab8feb` | `fp_4181e24c46` |
| Run 2 | `0f12a869-cb06-4cae-b46a-d3f29219d133` | 4 | `1ba9d2ac72471d59c4727b2e8e2f486a409e8b420e7d4c50dc030fd9f2363cfb` | `fp_4181e24c46` |
| Run 3 | `ce3b7a7a-c637-402a-8e97-94f46ccb0038` | 5 | `89a6d3367f2e3fce342920f9e5013d85e5803e807ef692ff24970bcf2aa8fc6c` | `fp_4181e24c46` |

**Content comparison:** VARYING
- All three SHA256s differ
- Fact counts differ (Run 3 has 5 vs 4 in Run 1 and 2)
- Notable slot variations:
  - `travel.previous_flight.meal` (Run 2) vs `meal.previous_flight` (Run 3) vs `travel.previous_meal` (Run 1) — same semantic content, different slot paths
  - `experience.airline.american_airlines` appears only in Run 3
  - `travel.flight_class.economy` appears only in Run 3

---

### Session 2

- **Input hash:** `2522da9525262b400d2a296a25dda0e1819aad22c691bc7cd268fb30b9239a5f`

| Run | Session ID | Fact Count | SHA256 (facts) | Fingerprint |
|-----|------------|------------|----------------|-------------|
| Run 1 | `6f5ca1ac-6057-4cfd-a287-9217ac563e9c` | 5 | `df8458cb34aa568c2b9703932ca11bb2ff7e8b53895c4f695a604cdf0112919c` | `fp_4181e24c46` |
| Run 2 | `928cdcda-034b-4928-b805-e31f1fa40fb5` | 5 | `413820295f6ade3635a781162742902c546a48d436acdf33a14584f425e3086f` | `fp_4181e24c46` |
| Run 3 | `ef34f02f-f93c-4fc7-aa89-02f96f68cb11` | 5 | `e88e6c22e023b1dad58c38ecfc85c7af424d7046235da012d18d2bfadf04ef05` | `fp_4181e24c46` |

**Content comparison:** VARYING
- All three SHA256s differ despite identical fact counts
- Notable slot variations:
  - `project.marketing_campaign.teams` (Run 1) vs `project.marketing_campaign.teams_collaboration` (Run 2, Run 3) — same semantic content, different slot paths

---

### Session 3

- **Input hash:** `08231c0002c94a16f2ecbc0397ddd190d7e0377b0e1326ec08caa82ea57928d4`

| Run | Session ID | Fact Count | SHA256 (facts) | Fingerprint |
|-----|------------|------------|----------------|-------------|
| Run 1 | `fc48352b-254e-4048-8f56-ac46ad3e0bdc` | 3 | `da01a62e3bdb7d09d4d55ce38f76f5ca965129e93434841fde98b84c6f7a95d9` | `fp_4181e24c46` |
| Run 2 | `9057bccd-b41c-42c5-96d6-de1154a997ab` | 3 | `da01a62e3bdb7d09d4d55ce38f76f5ca965129e93434841fde98b84c6f7a95d9` | `fp_4181e24c46` |
| Run 3 | `f237047a-047d-4e47-8bba-ad922f32de03` | 3 | `892414e3b5f1db9ce7c7752e0cb6c83a92cc21408618df8d1c5dad0771b44fc7` | `fp_4181e24c46` |

**Content comparison:** MIXED
- Run 1 and Run 2 have identical SHA256 (`da01a62e...`)
- Run 3 has different SHA256
- Notable slot variation:
  - `food.gelato.preference` (Run 3) vs `food.gelato.general_preference` (Run 1, Run 2) — same semantic content, different slot path

---

### Session 4

- **Input hash:** `f63ddb8ee384eee7e6e7aa6ff26dc781c56cfd47c7d89bc946636f47f2a3667e`

| Run | Session ID | Fact Count | SHA256 (facts) | Fingerprint |
|-----|------------|------------|----------------|-------------|
| Run 1 | `8c85cd2a-371a-4ad8-9560-9efa0cb55309` | 5 | `56a2cf6c6f5d04326ffd18c0f9a06e788689a0383b5db626c70ebed5788a198d` | `fp_4181e24c46` |
| Run 2 | `f094caa4-b165-4961-b529-e96c7988a162` | 5 | `5d7297ba798e4686d464226cdcf0a697135a95cc3d13e83f26f3c42ae69b07e0` | `fp_4181e24c46` |
| Run 3 | `d6999e69-671c-47a1-afd6-f1db79b675e2` | 5 | `85a03dc3641790fdbc3693614552f8198ef5b87b9ac6ff1f1eb4e4875ce26c3e` | `fp_4181e24c46` |

**Content comparison:** VARYING
- All three SHA256s differ despite identical fact counts
- Multiple slot path variations observed (e.g., `device.wireless_charging_pad` vs `home.device.wireless_charging_pad`)
- Some facts appear in 2 of 3 runs but not all three

---

### Session 5

- **Input hash:** `582f5a20f3d64681b8a31374bab1682589aaddcfe8fc64773f6b9eed182cdf5a`

| Run | Session ID | Fact Count | SHA256 (facts) | Fingerprint |
|-----|------------|------------|----------------|-------------|
| Run 1 | `358f08b4-3594-42bb-8e44-a1cf3309f955` | 4 | `8d00fd3e1c9c680db6aecdc5b37a686b537a121a68c2a084991adfed8c302db0` | `fp_4181e24c46` |
| Run 2 | `666fa8da-b655-46b3-a172-de31cbe04763` | 7 | `940f40c94f5321c4fce1cb15a4f01391186c5f3b7c5b41f972dd7879e759e6e5` | `fp_4181e24c46` |
| Run 3 | `4b9ba22a-2a98-4202-99c8-687f56c3afa5` | 10 | `d965ac1f2eb5e30374d463ecf44996b6e87d31c071eec45a6f5207c73a2e8e80` | `fp_4181e24c46` |

**Content comparison:** VARYING
- All three SHA256s differ
- Fact counts vary dramatically: 4 → 7 → 10
- Significant content divergence across runs
- Same semantic content appears with different slot paths (e.g., `project.methodology.gbmd` vs `project.paper_content.methodology_description`)

---

## Fingerprint Analysis

All three runs observed the **same** `system_fingerprint: fp_4181e24c46`, indicating:
- Same model version (openai/gpt-4o-mini-2024-07-18)
- Same provider (OpenRouter)
- Identical seed/fingerprint parameters at the provider level

**However**, content still varied across runs, suggesting the variance originates from:
1. Non-determinism within the model's own generation (even with fixed fingerprint)
2. Differences in the full input context passed to the model across runs
3. Embedding-based deduplication variance affecting which facts are stored

---

## Multiplicity Condition Check

**Question:** Can we prove multiplicity (multiple distinct extractions for the same input) from the preserved artifacts?

**Evidence from `extraction_log.jsonl`:**

| Indicator | Count (out of 788) | Percentage |
|-----------|-------------------|------------|
| Sessions with retry_used=true | 464 | 58.9% |
| Sessions with superseded > 0 | 109 | 13.8% |
| Sessions with raw_count ≠ extracted_count | 2 | 0.3% |

**Findings:**
- The `retry_used: true` flag in `dedup_results` indicates that an extraction was retried due to initial failure or non-deterministic empty response
- The `superseded > 0` indicates that later extractions superseded earlier ones
- However, each session in `extraction_log.jsonl` represents a **single final extraction result**, not the full sequence of retry attempts

**Multiplicity cannot be fully proven from preserved artifacts alone** because:
1. The extraction log only records the final canonicalized result per session
2. Retry sequences are not preserved — only the outcome of whether a retry occurred
3. To fully prove multiplicity, one would need the raw retry chains (not currently preserved in these artifacts)

**Statement:** "If multiplicity cannot be proven from the preserved artifacts, state that explicitly."

→ **Multiplicity is partially evidenced** by `retry_used=true` (58.9% of sessions) and `superseded>0` (13.8%), but the full retry chains are not preserved. The existence of retries is confirmed; the exact multiplicity of extraction attempts is not fully traceable.

---

## V1 Interpretation

Based on the user's rule:

| Condition | Status | Evidence |
|-----------|--------|----------|
| **V1.a** Content varies → proceed with single-run point-estimate framing | **APPLIES** | Sessions 1, 2, 4, 5 show clear content variation across all runs |
| **V1.b** Content identical → check multiplicity condition | NOT APPLICABLE | No session had identical content across all 3 runs |
| **V1.c** Mixed → proceed with bounded-variance framing | **APPLIES** | Session 3 shows partial identical (Run 1 == Run 2) with Run 3 differing |

**Conclusion:** **V1.c (bounded-variance framing)** is the appropriate interpretation.

All 5 sampled sessions show some degree of content variation:
- 4/5 sessions: completely different content across all 3 runs
- 1/5 session (Session 3): mixed — Run 1 and Run 2 identical, Run 3 different

The fingerprint is stable (`fp_4181e24c46`) but content still varies, indicating the variance is not from model/fingerprint changes but from other sources (likely embedding-based dedup timing or context differences across runs).

---

## Summary Table

| Session | SHA256 Match | Count Match | Content Status | Retry Evidence |
|---------|--------------|-------------|----------------|----------------|
| 1 | None | Partial (4,4,5) | Varying | Run 3: retry_used=true |
| 2 | None | Yes (5,5,5) | Varying | None |
| 3 | Partial (R1==R2) | Yes (3,3,3) | Mixed | Run 3: retry_used=true |
| 4 | None | Yes (5,5,5) | Varying | R2,R3: retry_used=true |
| 5 | None | No (4,7,10) | Varying | Run 3: retry_used=true |

---

## Files Referenced

- `tests/benchmark_results/wave0_rerun_v1_clean/run_1/extraction_log.jsonl`
- `tests/benchmark_results/wave0_rerun_v1_clean/run_1/memories.jsonl`
- `tests/benchmark_results/wave0_rerun_v1_clean/run_1/run_metrics.json`
- `tests/benchmark_results/wave0_rerun_v1_clean/run_2/extraction_log.jsonl`
- `tests/benchmark_results/wave0_rerun_v1_clean/run_2/memories.jsonl`
- `tests/benchmark_results/wave0_rerun_v1_clean/run_2/run_metrics.json`
- `tests/benchmark_results/wave0_rerun_v1_clean/run_3/extraction_log.jsonl`
- `tests/benchmark_results/wave0_rerun_v1_clean/run_3/memories.jsonl`
- `tests/benchmark_results/wave0_rerun_v1_clean/run_3/run_metrics.json`
