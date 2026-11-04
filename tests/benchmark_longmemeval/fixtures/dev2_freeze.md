# dev2 freeze

Status: IDs and estimates only. **Nothing here authorises ingest, labelling, a
restore, provider spend or a campaign.** M2 needs separate owner approval.

dev2 is a fresh evaluation set for the multi-query exposure gate (#601). The five
omission cases used to develop that design are spent evidence and cannot accept it.

## Selection

`tests/benchmark_longmemeval/dev2.py`, seed 42, algorithm
`dev2-eligible-type-x-abstention-largest-remainder-sha256-v1`. Pins are in
`dev2_ids.json`; `test_dev2.py` checks them.

| | Count |
|---|---|
| Dataset questions | 500 |
| Excluded: dev-50 IDs | 50 |
| Excluded: holdout IDs | 100 |
| Excluded: shares an answer session with dev-50 | 0 |
| Excluded: shares an answer session with holdout | 1 |
| Excluded: type not multi-session, temporal-reasoning or knowledge-update | 105 |
| Eligible | 244 |
| **Selected** | **100** |

| Stratum | Answerable | Abstention |
|---|---|---|
| multi-session | 36 | 3 |
| temporal-reasoning | 38 | 1 |
| knowledge-update | 20 | 2 |

**Holdout handling.** Holdout question IDs and the `answer_session_ids` of those
rows were read, in code, only to exclude them. The script parses the whole dataset
file, as `holdout.py` does, but reads no other holdout field, and its output holds
only selected IDs, counts and hashes. The worker that wrote the selector had no
dataset access; the primary ran the freeze.

**Set composition differs from dev-50.** dev2 has only the three types above, so a
result on it says nothing about single-session questions. Only 6 of 100 are
abstention questions, so the distractor delta will be coarse.

## Estimate for a later approved ingest

Volumes are computed from the selected rows, with sessions de-duplicated by ID.

| Item | Estimate | Basis |
|---|---|---|
| Sessions / turns | 4,574 / 47,242 | Counted |
| Tokens | ≈ 11.67 M | `chars / 4`; approximation, not a tokenizer count |
| Ingest spend | **≈ $38** (about $30–45) | Phase D ledger: $19.22 for dev-50's 5.99 M tokens by the same approximation, ± 20 % carried from its own projection. dev2 is 1.95× that volume |
| Embedding, within that | ≤ $0.23 | Route ceiling $0.02/M applied to all tokens. An upper bound that slightly double-counts the ledger figure |
| Wall time, sequential | **≈ 105 h** | Phase D's 54 h projection (23 s/chunk), scaled by tokens. Projected, not measured; it noted an overnight slowdown of up to 4× |
| DB restores | 1 | One restore of a new dev2 snapshot for a read-only exposure measurement. **All 13 approved restores are consumed, so this needs new approval** |
| Human spot-check | 15 questions, ≈ 75 min | 15 %; 5 min per question is an assumption |

`dev2_unit_costs.json` holds these rates. `embedding_seconds_per_1k_tokens` is 0
because the wall-time basis already includes it, and `turns_per_db_restore` is set
so that the formula yields the one restore above.

**Not estimated:**
- **Model-assisted labelling of target memories.** No measured unit exists; only
  the human spot-check is covered.
- **Current extraction pricing.** The ledger rate reflects the model and route in
  use on 2026-10-27. Re-price before approval if they have changed.
- **M1.5 and M3**, which are separate decisions.
