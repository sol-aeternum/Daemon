# Memory Upgrade Roadmap — Daemon

> Baseline status (generated 2026-05-06): `tests/benchmark_results/harness_parity_baseline_decision.md` declares **HALT — baseline undeterminable**. The blocking T14 artifact `tests/benchmark_results/harness_parity_baseline_run.json` halted because the full haystack-bearing LongMemEval_S corpus is unavailable, so no numeric T15 production-aligned baseline exists today. The historical **10.4% adjusted** (49/473) Wave 0 Option A figure remains a pre-parity / harness-artifact comparison anchor only, not the current post-parity production baseline.
>
> Frontier reference remains Mem0-new 93.4%, Hindsight 91.4%, Supermemory 85.2%, but any gap-to-frontier arithmetic derived from the historical 10.4% / 49/473 anchor is provisional until a completed parity baseline exists.

---

## What changed at Wave 0 closure (load-bearing for all that follows)

Three structural facts now anchor every downstream decision:

1. **The benchmark and the production system were measuring different things.** Until Path A alignment, `tests/longmemeval/evaluate.py` used a thin bullet-list prompt that bypassed `orchestrator/memory/injection.py` entirely. Every score in the project's history before tag `pre-wave-1` reflected harness behavior, not production. The 10.4% number is the first production-aligned measurement.

2. **Original wave gates were authored without production-aligned data.** The `≥+2pp aggregate AND no per-subset regression AND ≤3pp triple-run variance` gate referenced floors (>15% aggregate, >5% per-category) that turned out to be aspirational rather than calibrated. The variance lock is incompatible with the production injection path's bounded variance sources. Those gates are dead. New gate definition below.

3. **The system has demonstrated a high rate of latent infrastructure defects.** Wave 0 surfaced eleven distinct defects across five rounds — provider routing class bugs, encryption key drift, FK violations, supersede precondition drift, JSONB deserialization, harness verdict misreporting, memories_used=0 wiring, ciphertext corruption. Each surfaced only when full-corpus benchmarks ran against the production path. Future waves land in this system. Plan accordingly.

---

## Production-aligned baseline structure

10.4% aggregate decomposes into a per-category structure that drives wave priority and gate exemptions:

| Category | Score | Signal |
|---|---:|---|
| KU | 18.7% | Best. Bitemporal closure + supersession working at retrieval. |
| IE-preference | 17.2% | Slot-aware extraction holding through retrieval. |
| IE-user | 16.1% | Core fact extraction reaching the answer model. |
| IE-assistant | 13.7% | Selective extraction firing; lossy at retrieval/reading. |
| MR | 7.1% | Cross-session synthesis is a structural gap. |
| TR | 2.3% | Temporal reasoning is a category collapse, not a gap. |
| ABS | 0.0% | Wave 0 closure caveat per closure memo disposition. |

The IE-* range at 14-17% with median 5 memories used is the load-bearing signal: facts arrive at the answer model and answers come back wrong. This is the W1 (Chain-of-Note + JSON-structured reading + confidence surfacing) and W2 (reranker) target surface — not an extraction issue, an injection-format and ranking-precision issue.

TR at 2.3% with ~80 of 500 questions is structural absence: bitemporal `valid_from`/`valid_to` exist in storage but are not exercised at retrieval. W4/W5 are the explicit target. Closing TR to even 30% recovers ~4-5pp aggregate from one wave [~%].

ABS at 0.0% disposition is per the Wave 0 closure memo; not gated until reopened by an explicit wave assignment.

---

## Producer vs full-pipeline benchmarks

Two benchmarks coexist and measure different surfaces:

- **In-house extraction benchmark** (`tests/benchmark_extraction.py`, 8 scenarios, 30 expected facts) — the **producer-layer regression detector**. Verifies extraction emits the keyword-defined fact and stores it as a memory. Current state: P=1.0 R=1.0 A=0. Used as fast iteration guard during wave implementation. Same-vocabulary source-and-target; structurally cannot test ranking, paraphrasing, retrieval scope, injection format, or full-pipeline behavior.

- **LongMemEval_S** (500 questions, judge-scored) — the **full-pipeline wave gate**. Verifies producer + retrieval ranking + injection format + answer model reading + judge agreement, with paraphrased question targets. Current production-aligned baseline: 10.4%. Used as the gate for every wave from W1 onward.

The 100% in-house score and 10.4% LongMemEval score are not contradictory. They measure different surfaces. The cumulative loss across retrieval ranking, injection format, paraphrasing bridging, and judge agreement is exactly what LongMemEval is built to expose and the in-house benchmark is structurally unable to detect.

Each wave's plan checkpoints against both: in-house for fast iteration during implementation, LongMemEval for the gate decision. Wave plans must explicitly state which benchmark each TODO checkpoints against.

---

## Wave gate definition

A wave **ships** iff all four conditions hold:

1. **Aggregate lift**: aggregate LongMemEval_S score is ≥ +2pp over the immediately prior shipped baseline.
2. **No regression on previously-passing categories**: any category that scored ≥5% in the prior baseline must remain ≥5% (within measured noise floor) post-wave.
3. **Sub-floor exemptions with roadmap mapping**: TR (W4/W5 target) and ABS (per closure disposition) are not gated. If a future wave is the explicit target for closing a sub-floor category, that category becomes gated for that wave only.
4. **Extraction non-regression**: P≥0.95, R≥0.85, A≤2 preserved on the in-house extraction benchmark.

**Variance contract:** single-run point estimate. Bounded variance from documented sources (OpenAI near-determinism token drift at seed+temp=0 within stable system_fingerprint; Voyage AI retrieval determinism uncertainty; arq job timing). The triple-run variance lock is dead. If a wave's measured lift is between +2pp and +4pp, run a confirmation second run; if confirmation lifts ≥+2pp from baseline, gate passes. Above +4pp, single run suffices.

A wave **fails** → full rollback, document failure mode in `tests/benchmark_results/waveN_postmortem.md`, advance to next wave or revise priority. **No partial ships.** No smuggling individual components from a failed bundle into the next wave without re-gating them standalone.

### Rollback triggers mid-wave (abort before running full gate)

- Extraction benchmark drops below R≥0.85 during smoke
- Retrieval p95 latency > 1500ms (mobile-first hard ceiling)
- Encryption / data-integrity assertion fails in smoke
- Memories_used median = 0 across smoke sample (the C3-round-1 wiring failure pattern)
- Provider routing failure rate > 5% during smoke

---

## Failure mode budget per wave

Empirical rate from Wave 0: each full-corpus benchmark cycle surfaces 1-3 latent infrastructure defects. Plan for it as a baseline expectation, not a contingency.

Each wave's plan budget includes:

- Pre-implementation system audit per the diagnostic protocol (mandatory; see below)
- Investigation TODOs that precede implementation TODOs
- Explicit time line item for "system defects surfaced during execution" — not a contingency, a baseline cost
- Postmortem section in the wave's PLAN.md whether or not the wave ships, capturing any defects found and their dispositions

If a wave surfaces a defect that requires production code modification of `orchestrator/memory/**`, the wave halts and a separate planning round is commissioned for the production fix before the wave continues. The constraint that protected Wave 0 (`orchestrator/memory/**` stays clean during benchmark cycles) extends to all waves until explicitly waived for the wave's specific implementation scope.

---

## Per-wave system audit (mandatory)

Each wave's PLAN.md invokes the **memory-wave-diagnostic** skill (`skills/memory-wave-diagnostic/SKILL.md`) as the first TODO before any implementation. The audit produces:

- Inventory of production code paths the wave will modify or depend on (extraction.py, retrieval.py, injection.py, dedup.py, etc.)
- End-to-end smoke trace on a known LongMemEval question — captures memories_used count, scoped retrieval verification, full assembled prompt content, judge result
- Bounded probes per the skill's D-chain pattern for the wave's specific risk surface (W2 probes ranking, W3 probes embedding key generation, W4 probes time-anchor parsing, etc.)
- Disposition matrix populated for each anticipated failure class — surgical fix in scope, deferral to wave with cited mapping, escalation requiring user authorization

The audit is non-optional and front-loaded. The pattern of "surface defects mid-execution and burn the cycle" demonstrated five times in Wave 0 is exactly what the audit prevents.

---

## Adaptive wave ordering

Wave ordering is adaptive, not fixed. After each wave ships, the per-category breakdown determines the next wave's priority. If wave N lifted IE-* but did nothing for MR, the next wave targets the unmoved categories (W2 for ranking, W3 for paraphrasing, W4/W5 for temporal). The ranked sequence below is the starting prior, not a fixed schedule.

The starting question — W1 vs W2 priority — is settled by the **pre-W1 probe** described in the W1 section, not assumed.

---

## Ranked sequence (post-Wave 0 priors)

Lift estimates were originally derived from frontier-system measurements at 67-93% baselines. **Lift behavior at 10.4% is structurally different and cannot be predicted from those numbers.** Treat estimates as priors with wide confidence intervals; treat each wave's actual measured lift as new data.

| # | Wave | Change | Original prior lift | Effort | Risk | Confidence at 10.4% baseline |
|---|------|--------|---------------------|--------|------|------------------------------|
| 1 | W1 | Chain-of-Note + JSON structured reading + confidence surfacing | +10pp @67.8 | 0.5d | low | medium — IE-* targeted; low-base behavior unknown |
| 2 | W2 | Pool widening 10 → 50 + voyage-rerank-2.5-lite | +3-5pp @67.8 | 2d | low | medium — MR targeted; same-vendor reranker most reproducible finding |
| 3 | W3 | Fact-augmented embedding keys at ingest | +5-9pp @67.8 | 3-5d | medium | medium — paraphrasing gap targeted |
| 4 | W4 | Self-query metadata filter + time-aware expansion | +3-5pp @67.8 | 4d | medium | high on TR subset — collapse means headroom |
| 5 | W5 | Dual-timestamp event_time | +2-4pp @67.8 | 5d | medium | medium — depends on W4 landing |
| 6 | W6 | ADD-only update policy audit | unknown | 0.5-2d | low | likely no-op — see W6 |
| 7 | W7 | Scope/namespace silos | bench-neutral | 3d | low | UX-driven |
| 8 | W8 | Atomic + source chunk co-retrieval | +1-3pp @67.8 | 4d | medium | low — token budget risk |
| 9 | W9 | Step-back prompting on hard-query classifier | +2-4pp multi-hop | 3d | medium | low until easier categories close |

---

## Wave 0 — CLOSED

Status: complete at tag `pre-wave-1` (HEAD `07e9e6e7`). Production-aligned LongMemEval_S baseline = 10.4% adjusted (49/473), 27 errors formally excluded with per-question root-cause attribution. Architectural finding (benchmark-production injection decoupling) documented in `tests/benchmark_results/wave0_closure_memo.md`. Eleven-plus infrastructure defects fixed across five rounds. Variance lock declared dead. Producer-vs-full-pipeline distinction codified in `baselines.md`.

No further Wave 0 work. References to Wave 0 from W1+ point to the closure memo and `wave0_aligned_baseline.md`.

---

## Wave 1 — Prompt-surface changes (batch)

**Pre-W1 probe (mandatory before commissioning):** sample 30 incorrect IE-* answers from the C3 results. For each, classify:

- **(R) Right memory not in top-5** — ranking failure, W2 should ship first
- **(F) Right memory in top-5 but answer wrong** — reading/format failure, W1 should ship first
- **(A) Right memory not extracted at all** — would invalidate P=1.0 R=1.0 producer claim; halt and re-audit producer

If F dominates (>60%): W1 first as planned. If R dominates: swap to W2 first. If A appears at any rate >5%: producer audit before any wave commissions.

This probe is cheap (~30 min agent work) and resolves the W1/W2 ordering with data, not priors.

### W1.a — Chain-of-Note + JSON-structured reading format

- **Source:** LongMemEval paper Finding 4 (+10 absolute points across LLMs at 67.8% baseline).
- **Change surface:** `orchestrator/memory/injection.py` only.
- **Mechanism:** Inject retrieved memories as a JSON array with fields `content`, `provenance`, `timestamp`, `confidence`, `source_type`. Prompt instructs answering model to produce an internal "note" reasoning over the memories before producing the final answer.
- **Confidence at 10.4% base:** medium. Targets the IE-* failure mode directly (right memory + wrong answer). The +10pp prior was at 67.8% — at low base, plausible outcomes range from +1pp (model can't read low-quality retrievals well) to +15pp (format unblocks substantial latent quality).
- **Expected category effect:** lifts IE-*; possibly KU. Probably no effect on MR (cross-session synthesis is upstream of injection format). No effect on TR/ABS.

### W1.b — Surface confidence as first-class prompt field

- **Source:** ChatGPT reverse-engineering (Rehberger).
- **Change surface:** `injection.py` only. Data exists (`memories.confidence`, `memories.trust_score`).
- **Mechanism:** Format as `[confidence: high|med|low]` inline per memory (≥0.85 high, 0.6-0.85 med, <0.6 low). Prompt instructs model to hedge low-confidence facts and ask-or-abstain rather than assert.
- **Why bundled:** same file, same gate cycle, halves benchmark time. Ablate post-ship if curious.
- **Expected category effect:** modest lift on IE-* via reduced confident-but-wrong errors. Possible secondary lift on ABS if abstention guardrail interacts with confidence signaling.

**Combined gate:** standard wave gate. If a regression source is suspected (e.g., abstention-specific lift from W1.b while W1.a regresses single-session-assistant), ablate post-gate with two separate runs.

**System audit (mandatory pre-implementation):** invoke `memory-wave-diagnostic` skill targeting `injection.py`. Capture full assembled prompt for one IE-* question; verify token budget enforcement, MEMORY_EVIDENCE_ABSTENTION_GUARDRAIL presence, L0 prepending behavior. Probe for any benchmark-mode flags that bypass production guardrails.

---

## Wave 2 — Retrieval pipeline widening

### W2 — Pool 10 → 50 + voyage-rerank-2.5-lite

- **Sources:** aimultiple +20pp Hit@1 from pool expansion; Voyage's +3.26% NDCG@10 same-vendor pairing.
- **Change surface:** `orchestrator/memory/retrieval.py`. Current constants: `INITIAL_VECTOR_CANDIDATES=10`, `MAX_RETURNED_MEMORIES=5`, `MIN_FINAL_SCORE=0.15`.
  - `INITIAL_VECTOR_CANDIDATES`: 10 → 50
  - `MIN_FINAL_SCORE`: 0.15 → 0.05 (reranker does precision; pre-filter widens net)
  - Insert rerank stage between candidate fetch and top-k selection
- **Rerank instruction string** (encodes trust taxonomy): "Prioritize recent, non-contradicted user-asserted facts and stated preferences. Down-weight low-confidence extractions and superseded memories. For temporal queries, prefer memories whose validity window intersects the query time range."
- **Cost:** ~$0.0003/query at 50 candidates × 200 tokens. Free tier covers first 200M tokens.
- **Latency:** +~600ms p50 for rerank. Parallelize with LLM prewarm (fire rerank in parallel with orchestrator model warm-up; block on rerank before injection).

**System audit:** invoke `memory-wave-diagnostic` targeting `retrieval.py`. Verify pool-size constants are config-driven not hardcoded. Measure current p50/p95 retrieval latency to establish baseline. Verify MIN_FINAL_SCORE callsites — confirm no benchmark-mode override that would break the new floor.

**Investigation TODOs (pre-implementation):**
- `librarian`: verify Voyage rerank-2.5-lite is GA (not preview/deprecated), confirm pricing, confirm max docs per call. **Blocker if API has changed.**
- `explore`: locate pool-size constants; confirm config-driven
- `explore`: measure current retrieval p50/p95 baseline

**Risk flags:**
- If `MIN_FINAL_SCORE=0.05` surfaces L2-consolidated/demoted memories the reranker can't discriminate, single-session-assistant may regress. Reinstate higher floor (try 0.10) if so.
- Voyage instruction-following quality on Daemon's specific ranking criteria is untested. Iterate on instruction string within the wave.

**Confidence at 10.4% base:** medium-high. Same-vendor reranker + pool expansion is the most reproducible finding in the research. Most likely wave to clear gate of any W1-W5.

**Expected category effect:** lifts MR primarily (cross-session synthesis benefits from better top-5 selection). Secondary lift on IE-* through better candidate quality.

---

## Wave 3 — Fact-augmented embedding keys

### W3 — Ingest-side fact augmentation before embedding

- **Source:** LongMemEval §5.3 (+9.4% recall, +5.4% QA at 67.8% base).
- **Change surface:**
  - `orchestrator/memory/extraction.py` — concatenate atomic fact with context facts and entity names before embedding
  - `orchestrator/memory/store.py` — `content` (encrypted) and `embedding_text` stored separately; vector key is augmented, stored content stays atomic
  - `orchestrator/memory/embedding.py` — pass augmented key, not raw content
  - **Migration**: re-embed all existing memories with augmented keys (use existing `/memories/re-embed` endpoint; extend to build augmented keys from existing context)

**Embedding text format:** `{raw_fact} | context: {semicolon-joined-context-facts} | entities: {semicolon-joined-entity-names}`

**Why the separation matters:** encrypted content stays atomic (retrieval and display use it). Only vector-space representation is fattened. This is Anthropic's Contextual Retrieval pattern.

**System audit:** invoke `memory-wave-diagnostic` targeting `extraction.py`, `store.py`, `embedding.py`. Confirm content is not currently embedded alongside context. Estimate total memory count for re-embed cost projection. Probe entity resolution coverage on existing memories — schema supports it (per `entities` table) but verify population rate.

**Investigation TODOs:**
- `explore`: audit current `memories.content` vs embedding pipeline
- `explore`: total memory count for re-embed cost (Voyage-4-large document × count)
- `oracle`: review whether existing extraction produces rich enough context-fact metadata, or whether extraction prompt itself needs modification

**Risk flags:**
- Re-embedding cost is one-time but non-trivial. Freeze writes during migration or accept eventual consistency.
- If context-fact extraction is currently shallow, this wave needs the extraction prompt changed first — could regress in-house extraction benchmark. **Run extraction benchmark pre-merge.**
- Entity resolution must be queryable per memory.

**Confidence at 10.4% base:** medium. Targets the paraphrasing gap (in-house vs LongMemEval delta). Main failure mode: if current `content` already includes enough context, re-embed produces minimal change.

**Expected category effect:** lifts IE-* through better paraphrasing bridging. Possible secondary lift on MR.

---

## Wave 4 — Time-aware query path

**Priority note:** TR at 2.3% with ~16% of corpus is the largest concentrated headroom in the production-aligned baseline. **Consider promoting W4 to position 2 or 3 once W1/W2 ship** — closing TR to 30% is +4-5pp aggregate from a single wave [~%]. Adaptive ordering rule applies; revisit priority after each shipped wave.

### W4 — Self-query metadata filter + time-aware expansion

- **Sources:** LongMemEval §5.4 (+11.3% recall on TR); Zep +17.3pp on TR.
- **Change surface:**
  - New module: `orchestrator/memory/query_parser.py` — parses incoming queries into `{semantic_query, time_range, entities, is_temporal}` using a GPT-4-class model
  - `retrieval.py` — push parsed filters into Postgres WHERE: `valid_from <= upper_bound AND (valid_to IS NULL OR valid_to >= lower_bound)`
  - Fallback: if parse fails or times out, run unfiltered (never block retrieval on parse failure)

**Why GPT-4-class:** LongMemEval §E.4 documents Llama-3-8B hallucinating time ranges. Use existing Claude Sonnet/Opus routing — query preprocess, not hot-path executor call. Latency target: ≤400ms.

**System audit:** invoke `memory-wave-diagnostic` targeting `retrieval.py` time-filter behavior. Confirm bitemporal columns (`valid_from`, `valid_to`) are indexed. Probe whether `valid_from` is reliably populated on memories from Wave 0 ingest (the recovery ingest may have left some rows with NULL or default `valid_from`). Capture one TR-category prompt end-to-end to verify temporal context propagation.

**Investigation TODOs:**
- `oracle`: routing decision — per-query GPT-4-class call adds ~400ms + cost on every turn; gate with cheap classifier (regex heuristics: "yesterday", "last week", "in 2024", ISO dates) to skip when not needed
- `explore`: confirm bitemporal columns are indexed
- `librarian`: port LongMemEval §5.4 query-expansion prompt verbatim — don't freelance

**Risk flags:**
- Latency. Mitigate with cheap classifier; only invoke expensive parser when heuristic triggers.
- Subset regression on non-temporal queries if filter leaks. Explicit test: non-temporal queries must have identical retrieval pre-vs-post wave.

**Confidence at 10.4% base:** high on TR subset. TR collapse means high headroom. Aggregate gate may hinge on whether TR lift × TR corpus weight (~16%) clears +2pp.

**Expected category effect:** TR concentrated lift, secondary KU lift (knowledge updates often have temporal anchor).

---

## Wave 5 — Dual-timestamp extraction

### W5 — `event_time` separate from `valid_from`

- **Source:** Supermemory architecture; attributed to their 76.69% TR vs baseline 45%.
- **Semantic distinction:**
  - `valid_from` / `valid_to` = when Daemon knew/believed the fact (existing bitemporal)
  - `event_time` = when the referenced real-world event occurred ("User moved to Singapore in March 2024" → event_time = 2024-03-XX, valid_from = time-of-conversation)
- **Change surface:**
  - Migration: `ALTER TABLE memories ADD COLUMN event_time TIMESTAMPTZ NULL, ADD COLUMN event_time_range TSTZRANGE NULL`
  - Extraction prompt: emit `event_time` when fact references a specific temporal event; null otherwise
  - Retrieval ranker: when query parser detects time anchor, boost memories whose `event_time` intersects query's range
  - Backfill: null `event_time` for legacy memories. **No LLM-based re-extraction on back catalog** unless specifically scoped.

**System audit:** invoke `memory-wave-diagnostic` targeting `extraction.py` for prompt modification risk. Run in-house extraction benchmark immediately on prompt change before any LongMemEval cycle — adding optional fields to extraction has historically regressed P/R on existing fields.

**Investigation TODOs:**
- `oracle`: backfill strategy — null-and-accept-reduced-coverage, budgeted LLM re-extract pass, or opportunistic on next access
- `explore`: extraction prompt location and capacity for additional field without regressing existing outputs

**Risk flags:**
- Extraction benchmark regression risk is real. **Run extraction benchmark pre-merge.** This is the kind of change that routinely fails the producer gate.
- Ranker tuning: adding `event_time` boost requires recalibrating weights. Current formula: `0.5*vector + 0.3*bm25 + 0.2*(recency*confidence*trust)`. Don't just add a term — reweight or risk regressing non-temporal queries.

**Confidence at 10.4% base:** medium. Depends on W4 landing first. Standalone aggregate lift may be marginal; bundle with W4 if W4 clears and W5 lift is sub-gate.

**Expected category effect:** TR concentrated. Marginal MR if multi-session synthesis benefits from event-time anchoring.

---

## Wave 6 — Update policy audit (likely no-op)

### W6 — Confirm non-destructive semantics across dedup

**Status:** likely closes as no-change-needed pre-implementation.

**Reason:** MEMORY_LAYER.md evidence indicates dedup is already supersession-based with bitemporal closure. Merge at 0.90 only touches `last_accessed_at`. Supersede at 0.82 inserts new and sets prior `valid_to`. Same-slot supersede at 0.65 is family-aware. The current semantics already match the ADD-only pattern Mem0 attributed +26pp to (in their bundle).

**Investigation TODOs (the entire wave):**
- `explore`: trace merge path in `dedup.py`. Confirm it modifies only `last_accessed_at`, never `content`. Confirm both old and new memories persist on supersede with proper `valid_to` closure.
- `explore`: find real transition-history examples in production memory table. Confirm preservation.
- `oracle`: verify bitemporal closure semantics match literature.

**Decision gate (post-investigation, before implementation):**
- If investigation confirms ADD-only → close as no-change-needed, skip to W7
- If investigation reveals lossy merge → spec a minimal fix (touch-only, never coalesce content)

**No LongMemEval cycle commissioned unless implementation is scoped.** Prior probability of implementation: ~15% [~%].

---

## Wave 7 — Scope/namespace silos

### W7 — Per-scope memory partitioning

- **Source:** Claude Projects design; addresses "context rot / cross-project preference bleeding."
- **Change surface:**
  - Schema: `memories.scope TEXT DEFAULT 'personal'`
  - Retrieval: `WHERE scope = $active_scope` default; optional cross-scope mode
  - Frontend Projects page (currently placeholder per CURRENT_ISSUES.md #3): real contract — project = scope
  - Incognito mode: `scope='ephemeral'` neither reads nor writes memory

**System audit:** invoke `memory-wave-diagnostic` targeting retrieval scoping. Probe how `scoped_retrieval_verification` (from Wave 0 C3) interacts with proposed scope filter — both filter on conversation IDs; verify no double-filtering bug.

**Why deferred:** benchmark-neutral on LongMemEval (single-user, single-scope corpus). Gain is UX/trust, not LongMemEval_S. Ship after easier benchmark wins are banked.

**Risk flags:**
- Resolves OPEN_QUESTIONS.md #4 (Projects feature scope) — if shipped, Projects = conversation+memory scoping, not task management.
- Benchmark gate: expect flat. **Legitimate gate exception** — wave justified by user-visible value, not benchmark lift. Be explicit in the wave's ship decision.

**Expected category effect:** none on LongMemEval. Real users who've burned out on ChatGPT context rot will rate this highly.

---

## Wave 8 — Atomic + source chunk co-retrieval

### W8 — Persist raw source; inject both at retrieval

- **Source:** Supermemory architecture; research §5.8.
- **Change surface:**
  - Schema: `memories.source_message_id UUID REFERENCES messages(id)` (verify current granularity — may be conversation-level only)
  - Retrieval: when memory selected, fetch originating message chunk and include in injection
  - Injection: format as `{atomic_fact} [source: "{raw_chunk}"]`

**System audit:** invoke `memory-wave-diagnostic` targeting injection token budget. Default budget per MEMORY_LAYER.md is 1500 tokens. Probe whether adding source chunks per memory blows budget at typical retrieval depth.

**Investigation TODOs:**
- `explore`: confirm current schema source linkage granularity (conversation vs message)
- `oracle`: token budget math — does adding source chunks blow the 1500-token default? If yes, choose subset for chunks (top-N only) vs chunk-instead-of-atomic for the rest

**Risk flags:**
- Token budget blowout is primary risk.
- Increases prompt noise — some LLMs over-weight raw chunk and under-weight distilled fact.

**Confidence at 10.4% base:** low. Token budget risk is real; lift is marginal.

**Expected category effect:** modest IE-* lift through nuance preservation.

---

## Wave 9 — Step-back prompting (classified routing)

### W9 — Abstract-the-query pattern for hard classes only

- **Source:** Zheng 2023 (arXiv 2310.06117). TimeQA +27%, MuSiQue +7%.
- **Change surface:**
  - Query classifier (cheap heuristic + small-model classifier): is query abstract/conceptual OR multi-session-reasoning?
  - If yes: LLM call to produce step-back abstraction ("Steve Jobs's 1990 employer?" → "Steve Jobs's employment history?")
  - Retrieve on abstraction, answer on concrete
- Latency cost: +~500ms on classified-hard queries only

**Why last:** marginal effort/uplift after W1-W5 close easier categories. Step-back targets multi-hop/abstract narrowly. Most benchmark already recovered by then.

**Risk flags:**
- Classifier false positives cost latency; false negatives forfeit lift. Tune on LongMemEval multi-session subset.

**Confidence at 10.4% base:** low until easier categories close. Reassess after W1-W5.

---

## Skip list (do not implement; reasoning unchanged from prior version)

Ordered by research-recommendation strength × stack-fit:

- **HyDE as default** — hurts proper-noun and abstention. Voyage-4 specifically underperforms headline numbers per T2-RAGBench 2026.
- **LLM-as-reranker** (RankGPT/RankZephyr/RankLlama/Qwen3-32B-as-judge) — Voyage's Oct 2025 analysis shows degradation on top of Voyage embeddings.
- **Cohere Rerank 3.5 over voyage-rerank-2.5-lite** — ~40× cost for same/worse, no co-training advantage.
- **ColBERT/ColBERTv2** — multi-vector infra cost not earned at memory-chunk scale.
- **Neo4j/FalkorDB graph layer** — meaningful operational cost; Postgres + `memory_links` edge table gets 80% of A-MEM behavior. Defer indefinitely unless Waves 1-9 plateau well below target.
- **LangMem framework** — p95 extraction latency 59.82s is disqualifying. Take procedural-memory idea only; leave the framework.
- **MemGPT-style agentic tool-call retrieval** — quality depends on model calling right tool; introduces prompt-dependency variance.
- **Supermemory-ASMR multi-agent default** — cost-infeasible at production by their own admission. Viable only as routed fallback for classified-hard queries; defer to post-W9.
- **Parametric memory / MemOS MemCube** — no production-quality evidence; orthogonal.
- **Multi-query retrieval stacked with reranking** — production study shows Hit@10 regression when combined. If deploying multi-query, do it without reranker or gate carefully.

---

## Evidence gaps (be prepared to experiment)

- **voyage-rerank-2.5-lite vs bge-reranker-v2-m3 on Voyage embeddings** — no independent reproduction.
- **Optimal candidate pool for conversational memory** — 50 is document-retrieval sweet spot; Daemon's optimum may differ. Measure.
- **LongMemEval >90% trustworthiness** — treat Mastra 94.87% / OMEGA 95.4% / Supermemory-ASMR 99% with ±15pp skepticism due to judge-model drift. Mem0 93.4% April 2026 is the most trustworthy frontier reference.
- **Lift behavior at 10.4% baseline vs frontier baselines** — entire roadmap's lift estimates derive from 67-93% baselines. Daemon's actual per-wave lifts will recalibrate the priors.
- **Ingest-cost sustainability at scale** — fact-aug + event_time + per-query parser all add LLM work per turn. Personal-scale fine; instrument cost-per-turn as each wave ships.
- **L0/L1/L2 tiering vs GAM semantic-shift consolidation** — unmeasured competing claim. A/B test post-W5.

---

## Master protocol summary

1. **Wave 0 closed.** Production-aligned baseline = 10.4% adjusted. All gates from this point forward measure against this number.
2. **One wave at a time.** No parallel merges. No composition runs until each wave is gated standalone.
3. **System audit precedes implementation.** Each wave invokes `memory-wave-diagnostic` skill before any code change. Non-optional.
4. **Investigation TODOs precede implementation TODOs** within each wave's plan.
5. **Strict gate or rollback.** New gate: ≥+2pp aggregate, no regression on previously-passing categories, sub-floor exemptions with cited mapping, extraction non-regression preserved. W7 (scope silos) is the only legitimate gate-waive candidate.
6. **Postmortem on every wave.** Pass or fail. `tests/benchmark_results/waveN_postmortem.md`. Update lift priors for remaining waves.
7. **Adaptive ordering.** Wave selection after each ship informed by per-category breakdown of post-wave run, not fixed sequence.
8. **Each wave's PLAN.md commissioned separately via Prometheus.** This roadmap is strategic sequence, not execution spec. Stale early-wave PLAN.mds drafted today would not survive what later waves teach about the codebase.
9. **`orchestrator/memory/**` clean during benchmark cycles** unless wave's implementation scope explicitly authorizes modification. The Wave 0 constraint extends.

---

## Decision points Julian owns before W1 commissions

These calls precede W1's PLAN.md generation:

1. **Run pre-W1 probe?** Sample 30 incorrect IE-* from C3 results; classify R/F/A; settle W1-vs-W2 ordering. Recommendation: yes. Cheap, decisive.
2. **W1.a + W1.b bundled or sequential?** Bundled halves benchmark cycle time. Separating allows isolated lift attribution. Recommendation: bundle for speed, ablate post-ship if curious.
3. **W2 instruction-string philosophy** — how much of Daemon's trust taxonomy to encode. Long instruction = more leverage, higher latency variance.
4. **W3 re-embed strategy** — freeze-and-migrate (downtime) vs rolling re-embed (mixed-key period). Rolling cleaner operationally; complicates benchmark.
5. **W4 vs W3 ordering reassessment after W1/W2 ship** — TR concentrated headroom may justify W4 ahead of W3. Decide based on post-W2 per-category data.
6. **W5 backfill strategy** — null-legacy vs budgeted LLM re-extract. Cost call.
7. **W6 — investigate or close pre-investigation?** MEMORY_LAYER.md evidence is strong that current semantics are already ADD-only. Could kill the wave with ~30 min of audit and skip directly to W7.
8. **W7 gate treatment** — accept benchmark-neutral ship (legitimate exception), or require contrived TR-subset improvement?
9. **Insert W9.5 for Hindsight-style 4-way parallel retrieval (semantic + BM25 + graph + temporal) with RRF+CE rerank?** Single most-replicated frontier pattern; Daemon has 3 of 4 signals. Research ranks as 4-6 week structural investment, not wave item. Decide whether post-W9 effort or separate roadmap.
