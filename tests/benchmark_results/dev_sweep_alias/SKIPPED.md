# Alias Expansion Ablation Skipped

Generated: 2026-04-18T15:59:18+00:00

Implementation was intentionally skipped after the required audit.

## Why this stayed skipped

- `retrieve_memories_for_text()` already feeds LongMemEval questions into `_get_entity_expanded_candidates()` on the live benchmark path.
- `_get_entity_expanded_candidates()` already queries both canonical entity lookup keys and stored alias lookup keys.
- The locked `retrieval-miss × single-session-user` target cell has **6** cases, but **0/6** contain an alias-like query surface (`quotes`, `@`, `#`, acronym) that would justify a distinct query-side alias toggle.
- The only plain-text entities in that cell (`Japan`, `Instagram`) are already extracted today, so there is no missing alias-expansion behavior to ablate there.
- One off-target full-fixture observation (`BBQ` / `DHL` acronym extraction) is a broader entity-extraction issue, not a target-cell alias-toggle proof, so changing code here would exceed the approved C1 scope.

## Outcome

- Audit artifact written: `tests/benchmark_results/dev_sweep_alias/AUDIT.md`
- No retrieval code changed.
- No dev-subset benchmark rerun was executed, because the audit did not establish a real missing alias-path knob.
