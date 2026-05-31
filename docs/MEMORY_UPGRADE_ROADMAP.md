# Memory Upgrade Roadmap — Daemon

> Derived from: research compass (`compass_artifact_wf-f8a390bb...md`), cross-checked against `MEMORY_LAYER.md` (authoritative) and userMemories. `TECHNICAL_SPECS.md` embedding section is stale (still lists `text-embedding-3-small`); Voyage-4 is live.
> Baseline: LongMemEval_S = 67.8%. Frontier: Mem0-new 93.4%, Hindsight 91.4%, Supermemory 85.2%.
> Target trajectory: Waves 1–4 plausibly close 67.8 → 78–82%. Wave 5+ is the 82–88% tier and compounds slower.

---

## Ranked sequence (uplift-per-effort, risk-adjusted)

| # | Wave | Change | Claimed lift | Effort | Risk | Gate-clear probability [~] |
|---|------|--------|--------------|--------|------|---------------------------|
| 0 | W0 | Baseline reproducibility lock | 0 | 0.5d | trivial | N/A (prerequisite) |
| 1 | W1 | Chain-of-Note + JSON structured reading format | +10pp (LongMemEval F4) | 0.5d | low | 55% |
| 2 | W1 | Confidence surfaced in retrieved-context prompt | unknown, likely +1–3pp on abstention | 0.25d | low | 60% |
| 3 | W2 | Pool widening 10 → 50 + `voyage-rerank-2.5-lite` | +3–5pp combined | 2d | low | 80% |
| 4 | W3 | Fact-augmented embedding keys at ingest | +9.4% recall / +5.4% QA | 3–5d (inc. re-embed) | medium | 70% |
| 5 | W4 | Self-query metadata filter + time-aware expansion | +3–5pp on temporal subset | 4d | medium | 65% |
| 6 | W5 | Dual-timestamp `event_time` (Supermemory pattern) | +2–4pp temporal | 5d | medium | 55% |
| 7 | W6 | ADD-only non-destructive update policy audit | unknown isolated; integral to Mem0 +26pp | 2–4d | high | 40% |
| 8 | W7 | Scope/namespace silos | UX gain; benchmark-neutral to +1pp | 3d | low | 50% |
| 9 | W8 | Atomic + source chunk co-retrieval | +1–3pp | 4d | medium | 50% |
| 10 | W9 | Step-back prompting on hard-query classifier | +2–4pp (multi-hop/abstract only) | 3d | medium | 45% |

**Gate-clear probability** = my prior that the change will pass the strict Phase 4d gate (≥+2pp AND no subset regression) on a single run, *given* Julian's harness. Labelled inferred.

Waves 1–4 = the plausible-78–82% bundle. Wave 5+ compounds slower and more of it will fail the gate.

---

## Benchmark Protocol (inherited from Phase 4d; mandatory per wave)

### Gate definition

A wave **ships** iff:
1. **Strict lift**: +2pp or greater on LongMemEval_S aggregate vs. the immediately prior shipped baseline.
2. **No subset regression**: no per-category drop exceeding the measured-noise floor on any LongMemEval category (single-session-user, single-session-assistant, single-session-preference, temporal-reasoning, knowledge-update, multi-session).
3. **Extraction benchmark non-regression**: P ≥ 0.95, R ≥ 0.85, A ≤ 2 preserved (current: P=1.00, R=1.00, adversarial_fp=0 — see `tests/benchmark_results/extraction_benchmark_canonical.md` for canonical baseline and metric lineage).
4. **Triple-run variance ≤ 3pp** (the Phase 0 reproducibility criterion).

A wave **fails** → full rollback, document failure mode in `tests/benchmark_results/waveN_postmortem.md`, advance to next wave. **No partial ships.** No smuggling individual components from a failed bundle into the next wave without re-gating them standalone.

> **⚠ gate-status — triple-run variance (2026-05-04)**
> The `≤3pp triple-run variance` gate (item 4 above) is **dead** under Path A production-injection semantics. The variance contract is now bounded single-run point estimates; the old triple-run lock is superseded. W1+ wave gates require redesign before W1 commissions. This callout does not define a replacement pass/fail threshold. W1 scope, source citations, and expected outcomes are unchanged.

### Cycle per wave

```
1. Freeze baseline:
   - record current LongMemEval_S (triple-run, confirm variance ≤3pp)
   - record extraction benchmark
   - tag git: pre-waveN
2. Implement change behind a feature flag (off by default on main)
3. Flip flag on benchmark branch
4. Triple-run LongMemEval_S + extraction benchmark
5. Evaluate gate:
   - PASS: merge, tag post-waveN, update baseline
   - FAIL: rollback branch, write postmortem, proceed to next wave
6. Retrospective: update expected-lift priors for remaining waves
```

### Rollback triggers mid-wave (abort before even running full gate)

- Extraction benchmark drops below R ≥ 0.85 during smoke test
- Retrieval latency p95 > 1500ms (hard ceiling — context is mobile-first)
- Any encryption/data-integrity assertion fails in smoke tests
- Triple-run variance > 5pp (reproducibility broken; investigate before measuring)

---

## Wave 0 — Baseline reproducibility lock

**Why first:** `MEMORY_LAYER.md` records Phase 0 reopened after a 10.0pp triple-run spread (required ≤3pp). Nothing else is measurable until this is resolved. Running Wave 1 against an unstable baseline produces uninterpretable results.

**Scope:**
- Identify variance sources. Suspects (high → low): judge-model non-determinism, retrieval tie-breaking instability, arq job completion timing (see `benchmark_extraction.py` fixed-50s-sleep issue — CURRENT_ISSUES.md #5), embedding retries returning intermittent values.
- Lock judge model + temperature + seed where supported.
- Replace fixed-sleep extraction wait with `extraction_log` completion poll.
- Add deterministic tie-breakers in retrieval (e.g., `ORDER BY score DESC, id ASC`).
- Confirm triple-run spread ≤3pp on LongMemEval_S before any other work.

**Exit criterion:** three consecutive full-corpus LongMemEval_S runs at current code within ≤3pp.

**Investigation TODOs (pre-implementation):**
- `explore`: identify all non-deterministic operators in the benchmark harness
- `explore`: confirm whether `retrieval_log` contains enough data to replay a deterministic retrieval
- `librarian`: verify the LongMemEval judge model supports temperature=0 / fixed seed

---

## Wave 1 — Prompt-surface changes (batch)

Pure prompt/injection changes. Zero schema, zero re-embed, zero new dependencies. Ship both together as one gate because they share the injection path and isolating them costs more than bundling.

### W1.a — Chain-of-Note + JSON-structured reading format

- **Source:** LongMemEval paper Finding 4 — +10 absolute points across LLMs.
- **Change surface:** `orchestrator/memory/injection.py` (single file per MEMORY_LAYER.md §11).
- **Mechanism:** Inject retrieved memories as a JSON array, fields: `content`, `provenance`, `timestamp`, `confidence`, `source_type`. Prompt instructs the answering model to produce an internal "note" (reasoning over the memories) *before* the final answer.
- **Risk:** The +10pp is measured on frontier models (GPT-4, Gemini). MiniMax M2.7 (your default executor per userMemories) may not generalize. Your gate likely catches this.
- **Expected outcome:** 60–70% probability of +3–6pp lift given your executor model; 25–30% probability of +6–10pp lift; 5–15% probability of flat or regression. Inferred.

### W1.b — Surface confidence as a first-class prompt field

- **Source:** ChatGPT reverse-engineering (Rehberger); research §4 and §5.7.
- **Change surface:** `injection.py` only. Data already exists (`memories.confidence`, `memories.trust_score`).
- **Mechanism:** Format as `[confidence: high|med|low]` inline per memory (thresholds: ≥0.85 high, 0.6–0.85 med, <0.6 low). Prompt instructs model to hedge low-confidence facts and ask-or-abstain rather than assert.
- **Why bundled with W1.a:** Both changes to the same file. Bundling halves the benchmark cycle time. If both fail, the combined effect is what matters anyway.
- **Risk:** Low — purely additive prompt text. Main failure mode is verbosity regression on single-session-assistant (summarization quality drop).

**Combined gate:** standard Phase 4d gate. If one component is suspected as a regression source (e.g., abstention-specific lift from W1.b while W1.a regresses single-session), ablate post-gate with two separate runs.

---

## Wave 2 — Retrieval pipeline widening

### W2 — Pool 10 → 50 + `voyage-rerank-2.5-lite` integration

- **Sources:** aimultiple +20pp Hit@1 from pool expansion; Voyage's +3.26% NDCG@10 same-vendor pairing.
- **Change surface:** `orchestrator/memory/retrieval.py`. Current constants (per MEMORY_LAYER.md): `INITIAL_VECTOR_CANDIDATES = 10`, `MAX_RETURNED_MEMORIES = 5`, `MIN_FINAL_SCORE = 0.15`.
  - `INITIAL_VECTOR_CANDIDATES`: 10 → 50
  - `MIN_FINAL_SCORE`: 0.15 → 0.05 (widen net — the reranker does precision, not the pre-filter)
  - Insert rerank stage between candidate fetch and top-k selection
- **Rerank instruction string** (encode trust taxonomy — this is the Voyage feature that matters): *"Prioritize recent, non-contradicted user-asserted facts and stated preferences. Down-weight low-confidence extractions and superseded memories. For temporal queries, prefer memories whose validity window intersects the query time range."*
- **Cost accounting:** ~$0.0003/query at 50 candidates × 200 tokens. Free tier covers first 200M tokens. Personal-scale: effectively free.
- **Latency:** +~600ms p50 for rerank. Parallelize with LLM prewarm (fire rerank in parallel with orchestrator model warm-up; block on rerank completion before injection).

**Investigation TODOs (before implementation):**
- `librarian`: verify Voyage rerank-2.5-lite API is GA (not preview/deprecated), confirm pricing, confirm max docs per call (relevant if scaling beyond 50). **This is a blocker — if the API has changed since the research was written, the whole wave needs re-specification.**
- `explore`: locate current pool-size constants; confirm they're config-driven, not hardcoded
- `explore`: measure current retrieval p50/p95 to establish latency baseline

**Risk flags:**
- If `MIN_FINAL_SCORE = 0.05` surfaces L2-consolidated/demoted memories the reranker can't discriminate, single-session-assistant may regress. If so, reinstate a floor but wider than 0.15 (try 0.10).
- Voyage instruction-following quality on your specific ranking criteria is untested. Expect to iterate on the instruction string within the wave.

**Expected lift:** +3–5pp aggregate. Likeliest to clear the gate of any wave — same-vendor reranker + pool expansion is the most reproducible finding in the research.

---

## Wave 3 — Fact-augmented embedding keys

### W3 — Ingest-side fact augmentation before embedding

- **Source:** LongMemEval §5.3 — +9.4% recall, +5.4% QA.
- **Change surface:**
  - `orchestrator/memory/extraction.py` — concatenate extracted atomic fact with context facts and entity names before embedding
  - `orchestrator/memory/store.py` — ensure `content` (encrypted) and `embedding_text` (for the vector) are stored separately; the **embedded key** is augmented, the **stored content** stays atomic
  - `orchestrator/memory/embedding.py` — pass the augmented key, not the raw content
  - **Migration**: re-embed all existing memories with augmented keys (existing `re_embed` endpoint at `/reembed` — extend to build augmented keys from existing data)

**Embedding text format:**
```
{raw_fact} | context: {semicolon-joined-context-facts} | entities: {semicolon-joined-entity-names}
```

**Why the separation matters:** encrypted content column stays atomic (retrieval and display use it). Only the vector-space representation is fattened. This is Anthropic's Contextual Retrieval pattern.

**Investigation TODOs:**
- `explore`: audit current `memories.content` vs embedding pipeline — confirm content is not currently being embedded alongside context (if it already is, the change is smaller)
- `explore`: estimate total memory count for re-embed cost projection (Voyage-4-large document embed pricing × count)
- `oracle`: review whether existing extraction produces rich enough context-fact metadata, or whether the extraction prompt itself needs modification to emit usable context

**Risk flags:**
- Re-embedding cost is one-time but non-trivial. Freeze writes during migration or accept eventual consistency.
- Entity resolution (`entities.py` per MEMORY_LAYER.md) must be queryable per memory. Schema supports this; verify it's populated on all existing memories.
- If context-fact extraction is currently session-scoped and shallow, this wave needs the extraction prompt changed first — which could regress the extraction benchmark. **Run extraction benchmark pre-merge even though this wave's gate is LongMemEval.**

**Expected lift:** +3–6pp aggregate. Second-most-likely wave to clear the gate. Main failure mode is if Daemon's current `content` field already includes enough context that the re-embed doesn't change the key.

---

## Wave 4 — Time-aware query path

### W4 — Self-query metadata filter + time-aware expansion

- **Sources:** LongMemEval §5.4 (+11.3% recall on TR); Zep +17.3pp on TR.
- **Change surface:**
  - New module: `orchestrator/memory/query_parser.py` — parses incoming queries into `{semantic_query, time_range, entities, is_temporal}` using a GPT-4-class model
  - `retrieval.py` — push parsed filters into Postgres WHERE clause: `valid_from <= upper_bound AND (valid_to IS NULL OR valid_to >= lower_bound)`
  - Fallback: if parse fails or times out, run unfiltered (never block retrieval on parse failure)

**Why GPT-4-class:** LongMemEval §E.4 explicitly documents Llama-3-8B hallucinating time ranges. Use your existing Claude Sonnet or Opus routing — this is not a hot-path executor call, it's a query preprocess. Latency target: ≤400ms.

**Investigation TODOs:**
- `oracle`: decide routing — per-query GPT-4-class call is +~400ms + $ on every turn; gate with a cheap classifier (is the query temporal at all?) to skip the call when not needed
- `explore`: confirm bitemporal columns (`valid_from`, `valid_to`) are indexed
- `librarian`: LongMemEval §5.4 query-expansion prompt — port the exact prompt, don't freelance

**Risk flags:**
- Latency. Mitigate with cheap classifier (regex heuristics for date/time tokens — "yesterday", "last week", "in 2024", ISO dates). Only invoke the expensive parser when heuristic triggers.
- Subset regression risk on non-temporal queries if the filter leaks. Explicit test: non-temporal queries must have identical retrieval vs. pre-wave baseline.

**Expected lift:** +3–5pp concentrated in TR and knowledge-update categories. Strict aggregate gate may be marginal; TR subset lift should be dramatic.

---

## Wave 5 — Dual-timestamp extraction

### W5 — `event_time` separate from `valid_from`

- **Source:** Supermemory's architecture; research §4 — attributed to their 76.69% TR score vs. baseline 45%.
- **Semantic distinction:**
  - `valid_from` / `valid_to` = when Daemon knew/believed the fact (existing bitemporal)
  - `event_time` = when the referenced real-world event occurred ("User moved to Singapore in March 2024" → event_time = 2024-03-XX, valid_from = time-of-conversation)
- **Change surface:**
  - Migration: `ALTER TABLE memories ADD COLUMN event_time TIMESTAMPTZ NULL, ADD COLUMN event_time_range TSTZRANGE NULL`
  - Extraction prompt: instruct model to emit `event_time` when the fact references a specific temporal event; null otherwise
  - Retrieval ranker: when query parser detects a time anchor, boost memories whose `event_time` intersects query's time range
  - Backfill: null `event_time` for legacy memories — **do not** attempt LLM-based re-extraction on the back catalog unless specifically scoped (cost). Accept that historical TR queries have reduced coverage.

**Investigation TODOs:**
- `oracle`: decide backfill strategy — null-and-accept-reduced-coverage, or budgeted LLM re-extract pass, or opportunistic (re-extract on next access)
- `explore`: confirm extraction prompt location (`orchestrator/memory/extraction.py` per MEMORY_LAYER.md) and assess whether the prompt can accept another field without regressing other outputs

**Risk flags:**
- Extraction benchmark regression risk is real — adding a new optional field to extraction can cause the model to underperform on existing fields. **Run extraction benchmark pre-merge.** This is the kind of change that routinely fails the extraction gate.
- Ranker tuning: adding `event_time` boost requires re-calibrating the ranker weights. Current formula is `0.5*vector + 0.3*bm25 + 0.2*(recency*confidence*trust)`. Don't just add a term — reweight or risk regressing non-temporal queries.

**Expected lift:** +2–4pp on temporal subset; may be under the aggregate gate on its own. Consider running as a bundle with W4 if W4 clears and W5's aggregate lift is marginal alone.

---

## Wave 6 — Update policy audit (ADD-only review)

### W6 — Confirm non-destructive semantics across dedup

- **Source:** Mem0 April 2026 pivot — removed UPDATE/DELETE from the dedup decision tree, keeping only ADD. Their reported +26pp is a bundle, not this alone; isolating the update-policy effect is hard.
- **Your current state:** Per MEMORY_LAYER.md, dedup uses merge at 0.90 (touches `last_accessed_at`), supersede at 0.82 (inserts new, sets prior `valid_to`), same-slot supersede at 0.65. This already *looks* non-destructive — supersession preserves the old memory with a closure timestamp.
- **The real question:** is merge (at 0.90+ sim) silently lossy for transition-history queries? "User moved NY→SF" should retain both memories; does a 0.92-sim merge coalesce them into one?

**Investigation TODOs — precede any implementation:**
- `explore`: trace the merge path in `dedup.py`. Does it modify `content`? Does it merge into one memory or preserve both? If it only touches `last_accessed_at`, the update policy is already ADD-only and the wave is a no-op.
- `explore`: find real transition-history examples in the memory table — users whose facts evolved (location, job, relationship status). Confirm both old and new records persist with proper `valid_to` closure.
- `oracle`: verify bitemporal closure semantics match literature. Specifically: does `valid_to = now()` on supersede, or is it a flag?

**Decision gate (post-investigation, before implementation):**
- If investigation confirms policy is already ADD-only → **close the wave as no-change-needed**, advance to Wave 7.
- If investigation reveals lossy merge → spec a minimal fix (change merge semantics to touch-only; never coalesce content).

**Risk flags:**
- This is the wave most likely to fail the gate because its isolated lift is unknown. Mem0 reports it as integral to +26pp but never isolates it.
- If implementing a change: high regression risk on single-session-user (where merge probably helps avoid redundant near-duplicates).

---

## Wave 7 — Scope/namespace silos

### W7 — Per-scope memory partitioning

- **Source:** Claude Projects design (research §4); addresses "Context rot / cross-project preference bleeding" — the single most common user complaint across commercial memory.
- **Change surface:**
  - Schema: `memories.scope TEXT DEFAULT 'personal'`
  - Retrieval: `WHERE scope = $active_scope` default; optional cross-scope mode
  - Frontend Projects page (currently placeholder per CURRENT_ISSUES.md #3): finally gets a real contract — project = scope
  - Incognito mode: conversations with `scope = 'ephemeral'` neither read nor write memory

**Why deferred to W7 rather than earlier:** benchmark-neutral on LongMemEval (single-user, single-scope corpus). The gain is UX/trust, not LongMemEval_S. Ship it when the easier benchmark wins have been banked and there's slack to invest in UX.

**Risk flags:**
- This resolves OPEN_QUESTIONS.md #4 (Projects feature scope) — if you ship this, Projects = conversation+memory scoping, not task management.
- Benchmark gate: expect flat on LongMemEval_S. The gate may need waiving for this wave *if* the wave is justified by user-visible value alone. **Be explicit about this in the wave's ship decision** — this is the legitimate exception to the automatic gate.

**Expected lift:** LongMemEval benchmark-neutral; real users who've burned out on ChatGPT's context rot will rate this highly.

---

## Wave 8 — Atomic + source chunk co-retrieval

### W8 — Persist raw source; inject both at retrieval

- **Source:** Supermemory architecture; research §5.8.
- **Change surface:**
  - Schema: `memories.source_message_id UUID REFERENCES messages(id)` (already present via `source_conversation_id`? verify — you may already have the linkage at conversation level and need message-level)
  - Retrieval: when a memory is selected, fetch the originating message chunk and include it in the injected context
  - Injection: format as `{atomic_fact} [source: "{raw_chunk}"]`

**Investigation TODOs:**
- `explore`: confirm current schema's source linkage granularity (conversation vs. message)
- `oracle`: token budget math — does adding source chunks per memory blow the 1500-token default budget? If yes, choose which subset gets chunks (top-N only) vs. chunk-instead-of-atomic for the rest

**Risk flags:**
- Token budget blowout is the primary risk. Default budget per MEMORY_LAYER.md is 1500 tokens for memory injection.
- Increases noise in the prompt for the answering model — some LLMs will over-weight the raw chunk and under-weight the distilled fact.

**Expected lift:** +1–3pp, concentrated in single-session-user where nuance matters.

---

## Wave 9 — Step-back prompting (classified routing)

### W9 — Abstract-the-query pattern for hard classes only

- **Source:** Zheng 2023 (arXiv 2310.06117). TimeQA +27%, MuSiQue +7%.
- **Change surface:**
  - Query classifier (cheap heuristic + small-model classifier): is this query abstract/conceptual OR multi-session-reasoning?
  - If yes: issue an LLM call to produce a "step-back" abstraction of the query ("Steve Jobs's 1990 employer?" → "Steve Jobs's employment history?")
  - Retrieve on the abstraction, answer on the concrete
- **Gate carefully — this is a latency cost of +~500ms on classified-hard queries only**

**Why last:** effort/uplift is marginal after Waves 1–5 have closed the easier categories. Step-back specifically targets multi-hop/abstract — narrow contribution. Most of the benchmark will already be recovered by then.

**Risk flags:**
- Classifier false positives cost latency; false negatives forfeit the lift. Tune on LongMemEval multi-session subset explicitly.

---

## Skip list (do not implement; reasons)

Ordered by research-recommendation strength × your-stack-fit.

- **HyDE as default** — hurts proper-noun and abstention. Voyage-4 specifically underperforms the headline numbers per T2-RAGBench 2026.
- **LLM-as-reranker** (RankGPT/RankZephyr/RankLlama/Qwen3-32B-as-judge) — Voyage's Oct 2025 analysis shows *degradation*, not improvement, on top of Voyage embeddings.
- **Cohere Rerank 3.5 over Voyage rerank-2.5-lite** — ~40× cost for same/worse quality, no co-training advantage.
- **ColBERT/ColBERTv2** — multi-vector infra cost not earned at memory-chunk scale.
- **Neo4j/FalkorDB graph layer** — meaningful operational cost; Postgres + a `memory_links` edge table gets 80% of A-MEM behavior. Defer indefinitely unless Waves 1–9 plateau well below target.
- **LangMem framework** — p95 extraction latency 59.82s is disqualifying. Take the procedural-memory idea only; leave the framework.
- **MemGPT-style agentic tool-call retrieval** — quality depends on the model calling the right tool; introduces prompt-dependency variance you don't currently have.
- **Supermemory-ASMR multi-agent default** — cost-infeasible at production by Supermemory's own admission. Viable *only* as a routed fallback for classified-hard queries (defer to post-W9).
- **Parametric memory / MemOS MemCube** — no production-quality evidence; orthogonal to everything else.
- **Multi-query retrieval stacked with reranking** — production study shows Hit@10 regression when combined. If you deploy multi-query, do it *without* the reranker or gate carefully.

---

## Evidence gaps (be prepared to experiment)

- **Voyage rerank-2.5-lite vs. bge-reranker-v2-m3 on Voyage embeddings** — no independent reproduction. Self-hosting bge is a partial fly-blind decision.
- **Optimal candidate pool for conversational memory** — 50 is a document-retrieval sweet spot; your optimum may be 30. Measure.
- **LongMemEval >90% results** trustworthiness — treat Mastra 94.87% / OMEGA 95.4% / Supermemory-ASMR 99% with ±15pp skepticism due to judge-model drift. Mem0's 93.4% April 2026 eval is the most trustworthy frontier reference.
- **Ingest-cost sustainability at scale** — fact-aug + event_time + per-query parser all add LLM work per turn. Personal-scale is fine; instrument cost-per-turn as you ship each wave.
- **L0/L1/L2 tiering vs. GAM semantic-shift consolidation** — unmeasured competing claim. Worth an A/B test post-Wave 5.

---

## Master protocol summary

1. **Wave 0 first or stop.** Unreproducible baseline makes every subsequent wave uninterpretable.
2. **One wave at a time.** No parallel merges. No composition-of-waves runs until each wave has been gated standalone.
3. **Investigation TODOs precede implementation TODOs.** Per the skill-creator pattern — no wave's implementation begins until its investigation TODOs have confirmed assumptions.
4. **Strict gate or rollback.** Phase 4d gate (≥+2pp strict, no subset regression) adopted verbatim. W7 (scope silos) is the only legitimate gate-waive candidate, and only because its value is UX-not-benchmark.
5. **Postmortem on every failure.** `tests/benchmark_results/waveN_postmortem.md` per failed wave; update expected-lift priors for remaining waves.
6. **Each wave's PLAN.md gets commissioned separately via Prometheus when its turn arrives.** This roadmap is the strategic sequence, not the per-wave execution spec. Producing all 10 PLAN.mds upfront would stale as early waves teach us about the codebase.

---

## Decision points Julian owns before starting

These are the calls I won't make for you:

1. **Bundle W1.a + W1.b or ship separately?** Bundled halves benchmark time; separating lets you isolate CoN-vs-confidence effect. Recommendation: bundle for speed, ablate post-ship if curious.
2. **W2 instruction-string philosophy** — how much of Daemon's trust taxonomy to encode. Long instruction = more Voyage-instruction-following leverage but higher latency variance.
3. **W3 re-embed strategy** — freeze-and-migrate (downtime) vs. rolling re-embed (period of mixed keys). Rolling is operationally cleaner but complicates the benchmark run.
4. **W5 backfill strategy** — null-legacy vs. budgeted LLM re-extract. Cost call.
5. **W6 scope** — whether to investigate update-policy at all, or close as no-change-needed on MEMORY_LAYER.md evidence that current semantics are already ADD-only via supersession. Could kill this wave pre-investigation.
6. **W7 gate treatment** — accept benchmark-neutral ship, or require a contrived TR-subset improvement from scope pruning?
7. **Whether to insert a W9.5** for Hindsight-style 4-way parallel retrieval (semantic + BM25 + graph + temporal) with RRF+CE rerank. This is the single most-replicated frontier pattern and you already have 3 of 4 signals (no graph). The research ranks it as a 4–6 week structural investment, not a wave item. Decide whether it's a post-Wave-9 effort or a separate roadmap.
