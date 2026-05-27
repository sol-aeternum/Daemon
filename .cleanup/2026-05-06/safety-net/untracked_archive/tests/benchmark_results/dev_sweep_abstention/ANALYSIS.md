# Abstention Prompt Dev Sweep Analysis

Generated: 2026-04-18T16:22:45+00:00

This dev-subset ablation keeps the canonical lane pinned to `TOP_K_MEMORIES = 6` from Task 3a and compares the shared abstention guardrail off vs on.
The benchmark-side on arm patches the LongMemEval answer prompt with the exact same guardrail text now injected by `assemble_system_prompt()`, so the checkpoint prompt hash reflects a real prompt-only delta.

## Coverage gate

- Work-order status: `blocked_insufficient_target_cell`.
- Locked abstention failures in the taxonomy: `2` (promotion floor `5`).
- Nearby secondary cell `generation-error × temporal-reasoning`: `3`.
- Because the abstention target surface is still below the 5-case floor, this sweep can only inform guarded dev-subset judgment; it cannot justify full-corpus promotion by itself.

## On/off summary

| Run | Guardrail | Strict score | ABS accuracy | Locked abstention failures correct | False abstentions on non-ABS questions | Locked failure union correct | Drift warnings |
| --- | --- | --- | --- | --- | --- | --- | ---: |
| off | disabled | 24.0% | 60.0% | 0/2 | 23 | 2/39 | 1 |
| on | enabled | 18.0% | 100.0% | 2/2 | 20 | 3/39 | 2 |

## Protected dev-subset cells

Any negative delta on these locked cells is a backout condition for this prompt ablation.

| Cell | off | on | Δ on-off |
| --- | --- | --- | --- |
| single-session-user | 22.2% | 22.2% | +0.0% |
| single-session-assistant | 44.4% | 0.0% | -44.4% |
| multi-session | 20.0% | 30.0% | +10.0% |
| temporal-reasoning | 10.0% | 20.0% | +10.0% |
| knowledge-update | 33.3% | 22.2% | -11.1% |
| abstention | 60.0% | 100.0% | +40.0% |

## False-abstention risk

- off false-abstention count: `23`
- on false-abstention count: `20`
- delta on-off: `-3`
- new false-abstention QIDs in the on arm: `['09d032c9', '2318644b', 'b86304ba', 'c4f10528', 'd596882b', 'fca762bc', 'gpt4_4edbafa2', 'gpt4_65aabe59']`

False abstentions here mean answerable non-ABS questions whose hypothesis still took an abstention-like shape (`I don't have...`, `not enough information`, `cannot determine`, etc.) and remained non-correct.

## Recommendation

- Recommendation: `back_out`
- Reason: Prompt hardening regressed protected dev-subset cell accuracy, so the subset-veto rule requires backout before any downstream composition work.

Protected-cell regressions that triggered backout:

- `single-session-assistant`: `-44.4%`
- `knowledge-update`: `-11.1%`

Even without a protected-cell regression, this ablation stays non-promotable until the abstention target surface reaches the approved locked-case floor.
