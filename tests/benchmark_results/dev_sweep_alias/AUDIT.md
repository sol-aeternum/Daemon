# Query-Side Entity Alias Expansion Audit

Generated: 2026-04-18T15:59:18+00:00

## Decision

**Result: skip implementation.** The live LongMemEval path already performs query-side entity expansion, and the locked `retrieval-miss × single-session-user` target cell does **not** expose a distinct alias-expansion gap that can be isolated as a single variable on this dev subset.

## Existing live path (already present)

1. `tests/longmemeval/evaluate.py:344-364` calls `retrieve_memories_for_text(... query_text=query_text, retrieval_triggered_by="longmemeval")` for benchmark questions.
2. `orchestrator/memory/retrieval.py:504-558` forwards the **original query text** into `retrieve_memories(...)`.
3. `orchestrator/memory/retrieval.py:841-865` always tries `_get_entity_expanded_candidates(...)` when a normalized query exists, even if vector/BM25 retrieval is empty.
4. `_get_entity_expanded_candidates()` (`orchestrator/memory/retrieval.py:623-714`) looks up both:
   - `store.find_entities_by_alias(user_id, lookup_key)`
   - `store.get_entity_by_lookup_key(user_id, lookup_key)`
5. Query extraction and entity storage use the same normalization contract:
   - `orchestrator/memory/entities.py:202-212` `_normalize_lookup_key()`
   - `orchestrator/memory/entities.py:274-405` `extract_candidates_baseline()`
   - `orchestrator/memory/store.py:1642-1663` + `1763-1784` canonical/alias lookup

That means the approved audit had to prove a **missing benchmark-relevant behavior beyond this existing path**. It did not.

## Locked target-cell audit

Target cell from the approved work order: `retrieval-miss × single-session-user` = **6** locked failures.

| QID | Alias-like query surface (`quotes`, `@`, `#`, acronym) | Current extracted candidates | Expected answer | Why this does not prove a missing alias path |
| --- | --- | --- | --- | --- |
| `8550ddae` | none | `What`→`what` | `lavender gin fizz` | No alias-like surface exists in the question; query-time alias expansion cannot synthesize `lavender gin fizz` from a generic cocktail query. |
| `86f00804` | none | `What`→`what` | `The Seven Husbands of Evelyn Hugo` | No alias-like surface exists in the question; the query does not name the book or an alias for it. |
| `19b5f2b3` | none | `Japan`→`japan` | `two weeks` | The only concrete entity token (`Japan`) is already extracted and normalized, so the existing entity path is already the relevant behavior. |
| `ad7109d1` | none | `What`→`what` | `500 Mbps` | No alias-like surface exists in the question; this is a generic plan-speed query, not a name/alias lookup query. |
| `545bd2b5` | none | `Instagram`→`instagram` | `2 hours` | The only concrete entity token (`Instagram`) is already extracted and normalized, so there is no distinct missing alias-expansion knob here. |
| `25e5aa4f` | none | `Bachelor`→`bachelor`, `Computer Science`→`computer science` | `University of California, Los Angeles (UCLA)` | The answer contains `UCLA`, but the query never mentions `UCLA` or another alias string to expand; this is not a query-side alias miss. |


### Target-cell finding

- **0/6** target-cell questions contain quoted aliases, `@` mentions, `#` hashtags, or acronym-style alias tokens.
- **2/6** target-cell questions contain a concrete named entity in plain text (`Japan`, `Instagram`), and both are **already extracted** by `extract_candidates_baseline()`.
- The remaining **4/6** are generic referential questions (`What book...`, `What type of cocktail...`, `What speed...`, `Where did I complete...`) where query-side alias expansion has no alias string to operate on.

## Full dev-subset cross-check

Across all 50 locked dev-subset questions, alias-like surfaces are rare:

- quoted strings: **3**
- `@` mentions: **0**
- `#` hashtags: **0**
- acronym-style tokens: **2**

Only **5** questions in the full fixture expose any of those surfaces, and **none** are in the locked `retrieval-miss × single-session-user` target cell.

| QID | Type | Surface(s) | Current extracted candidates |
| --- | --- | --- | --- |
| `71a3fd6b` | `single-session-assistant` | quoted | `Speyer`→`speyer` |
| `gpt4_4edbafa2` | `temporal-reasoning` | acronym | `What`→`what`, `June`→`june` |
| `184da446` | `knowledge-update` | quoted | `Short History`→`short history`, `Nearly Everything`→`nearly everything`, `A Short History of Nearly Everything`→`a short history of nearly everything` |
| `0bb5a684` | `temporal-reasoning` | quoted | `Effective Communication`→`effective communication`, `Workplace`→`workplace`, `Effective Communication in the Workplace`→`effective communication in the workplace` |
| `7a8d0b71` | `single-session-assistant` | acronym | `Wellness Retreats`→`wellness retreats`, `Can`→`can` |


Two off-target questions do reveal a broader extractor limitation (`BBQ`, `DHL` acronym tokens are not emitted as standalone candidates), but that is **not** sufficient to promote this ablation because:

1. it does not occur in the approved target cell,
2. it is an **entity-extraction** gap, not proof of a missing benchmark-path alias-lookup toggle, and
3. implementing it here would violate the audit-first/single-variable contract by changing behavior the work order did not approve as benchmark-relevant.

## Overlap check against earlier approved sweeps

The approved `TOP_K_MEMORIES` sweep already moved this target cell without any alias change:

- `tests/benchmark_results/dev_sweep_max_returned/sweep_manifest.json`
- `TOP_K_MEMORIES = 6` recovered **2/6** target-cell cases on the locked dev subset.
- Baseline `run2` for that same cell was **0/6**.

So the dev subset already shows that generic retrieval knobs can move this cell. Without a distinct alias-only failure surface, an alias-toggle implementation would be hard to attribute honestly.

## Conclusion

No distinct query-side alias-expansion behavior remains to toggle for the locked dev-subset target cell.

- The live benchmark path already executes entity-linked expansion.
- The approved target cell contains **no alias-like query surface** that the current path fails to hand off to alias lookup.
- The few benchmark questions with richer alias-like syntax are outside the target cell and would require a broader extractor improvement, not this narrowly approved ablation.

Therefore this task should stop at audit + skip documentation, preserving single-variable integrity for downstream Phase 3 composition work.
