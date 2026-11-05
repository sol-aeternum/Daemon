# dev2 final-pin re-ingest and relabel: pre-registration (#601, owner plan Block C)

Fixed before any spend on this database. Authorises only what it states.

## Owner rulings, 2026-11-05 and 2026-11-06

| Item | Ruling |
|---|---|
| Scope | All 14 pilot questions: none ran at the final pin. They are excluded from Block E. |
| Cap | **USD 6.00** for ingest plus labelling. The driver's USD 1.00 margin applies, so ingest stops at USD 5.00. |
| Database | New `daemon_lme_bench_dev2_final`. Its ledger starts at zero, so this block reconciles on its own. The pilot's `daemon_lme_bench_dev2` stays untouched as evidence. |
| Spot-check packet | The 15% seeded sample plus every `answerable_from_store=false` label, marked as such. |
| Spot-check stop | The 20% stop is computed over the seeded sample only. The not-answerable additions get their own correction rate; above 20% flags for owner review. |

## Final pin

**The final ingest pin is the main commit this document merges in.** Its SHA, extraction route and config pin are recorded in the freeze log before the first call, and all dev2 ingest uses exactly that pin.

## Expected cost

The pilot arms cost USD 0.587, 1.138 and 2.407, so about **USD 4.13** for 675 sessions, plus about USD 0.01 for labelling. That is below the USD 5.00 stop, by USD 0.86. The c=8 arm's rate already includes the 4,000-token cap, and c=1 and c=4 did not, so this can differ slightly.

## Method

- **Questions** (`fixtures/dev2_pilot_ids.json`, which the packet command checks against): `07741c45`, `0bc8ad92`, `0ea62687`, `1f2b8d4f`, `2133c1b5`, `41698283`, `4dfccbf7`, `5a7937c8`, `71017276`, `a08a253f`, `edced276_abs`, `gpt4_372c3eed`, `gpt4_7ca326fa`, `gpt4_b5700ca0`.
- **Ingest:** one arm at concurrency 8, same harness, with `--cap-usd 6.00 --margin-usd 1.00`. Never `--retry-failed-sessions` or `--allow-state-mismatch`.
- **Relabel:** the frozen labelling protocol, `--max-spend-usd 6.00` as a cumulative ledger ceiling.
- **Provisional labels from pre-pin arms:** discarded as evidence, kept archived. Changes against them are reported, especially for `0bc8ad92`, `4dfccbf7`, `71017276` and `gpt4_7ca326fa`.

## Reported

Sessions, failures, truncated extractions counted separately, 429s, wall time, sessions per hour, ingest child memory, spend, ledger reconciliation, and label changes.

## Gate for Block E

All must hold:
- `extraction_failed` below 1%, and **truncated = 0**. Any truncation at 4,000 stops: #670 needs its next fix first.
- The spend ledger reconciles.
- Resume behaviour has held in at least one invocation.

## Stops

The spend stop, `extraction_failed` at or above 1%, any truncation at 4,000, more than 100 rate-limited attempts, a lost or busy run lock, duplicated or lost sessions, or a changed protected fingerprint (`daemon_lme_bench`, `5cb6ab14…`). The pilot database's fingerprint (`84a66e6e…`) is checked before and after too, because it is now evidence.

## Known first-time paths

The labelling spend ceiling, the run lock under real work and child memory sampling have only run in tests or short checks. This block is their first full live run, and a failure there is a stop, not a reason to change code mid-run.
