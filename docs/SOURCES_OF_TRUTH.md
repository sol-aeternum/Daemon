# Documentation Sources of Truth

This document defines the canonical hierarchy, precedence, and governance rules for Daemon's documentation layer. It establishes which files serve as the authoritative source for specific facts and how drift between code and documentation is managed.

## 1. Source Hierarchy and Precedence

Daemon uses a tiered hierarchy to resolve contradictions. When two sources disagree, the higher-tier source (lower number) always wins.

**Precedence: T0 > T1 > T3 > T2**

| Tier | Label | Description | Examples |
|------|-------|-------------|----------|
| **T0** | **Code & Config** | The ultimate truth. If the code does X, any doc saying Y is stale. | `orchestrator/config.py`, `migrations/*.sql`, `docker-compose.yml`, `orchestrator/routes/*.py` |
| **T1** | **Curated Gated Specs** | High-fidelity technical specifications that are manually maintained but strictly gated against T0 drift. | `MEMORY_LAYER.md`, `docs/FEATURE_MATRIX.md`, `docs/TECHNICAL_SPECS.md` |
| **T3** | **Operational Rollups** | Curated summaries of live operational data. They derive from raw logs but provide a higher-level view. | `docs/CURRENT_ISSUES.md` |
| **T2** | **Narrative Status Docs** | Low-fidelity status updates, roadmaps, and project context. These are the most likely to drift and should be treated as secondary to T1. | `docs/ROADMAP.md`, `docs/PROJECT_CONTEXT.md` |

### The Role of CURRENT_ISSUES.md
`docs/CURRENT_ISSUES.md` is classified as a **T3 Operational Rollup**. It is the curated interface for understanding active system anomalies. Its primary input is the raw `TRIAGE.md` log, but it is not a narrative document (T2) or a static spec (T1).

---

## 2. Documentation Mapping

Every markdown file in the repository is classified under this hierarchy to determine its governance and gating requirements.

| File | Tier | Owner/Source | Classification | Derived-from/Authority |
|------|------|--------------|----------------|------------------------|
| `MEMORY_LAYER.md` | T1 | Engineering | gated | `orchestrator/config.py`, `orchestrator/memory/` |
| `docs/FEATURE_MATRIX.md` | T1 | Product/Eng | gated | Code implementation state |
| `docs/TECHNICAL_SPECS.md` | T1 | Engineering | gated | `orchestrator/`, `migrations/`, `docker-compose.yml` |
| `docs/PROJECT_CONTEXT.md` | T1 | Engineering | gated | `truth_set.md`, `docs/FEATURE_MATRIX.md` |
| `docs/OPEN_QUESTIONS.md` | T1 | Engineering | gated | Decision log / `truth_set.md` |
| `docs/MEMORY_UPGRADE_ROADMAP.md` | T1 | Engineering | gated | `MEMORY_LAYER.md`, Wave plans |
| `docs/CURRENT_ISSUES.md` | T3 | Engineering | operational-rollup | `TRIAGE.md` |
| `docs/ROADMAP.md` | T2 | Product | pointer | `.sisyphus/plans/`, `docs/MEMORY_UPGRADE_ROADMAP.md` |
| `TRIAGE.md` | N/A | Engineering | raw-log | Diagnostic capture |
| `frontend/PWA_CHECKLIST.md` | N/A | Engineering | raw-log | Build environment report |
| `README.md` | N/A | Engineering | ungated-reference | Project overview |
| `QUICKSTART.md` | N/A | Engineering | ungated-reference | Setup instructions |
| `AGENTS.md` | N/A | Engineering | ungated-reference | Agent behavior rules |
| `.github/pull_request_template.md` | N/A | Engineering | ungated-reference | PR template |
| `docs/interactive-artifact-examples.md` | N/A | Engineering | ungated-reference | Example code |
| `frontend/PWA_SETUP.md` | N/A | Engineering | ungated-reference | PWA guide |
| `docs/PROJECT_BRIEF.md` | N/A | Product | ungated-reference | Product narrative |

---

## 3. Volatile Fact Governance

Volatile facts are high-confidence data points that change as the project evolves. These are the primary targets for automated drift gating.

### Volatile Fact Classes
- **Model Assignments**: Tier-to-model mappings in `config.py`.
- **Embeddings**: Model names and dimensions for document/query slots.
- **Thresholds**: Vector similarity dedup and consolidation values.
- **Migration Counts**: Total count and latest filename in `migrations/`.
- **Pricing**: Credit costs per second or per duration.
- **Providers**: Registered LLM, image, and video provider names.
- **Feature States**: Implementation status (stable, experimental, not started).
- **Routes/Endpoints**: Registered API paths and methods.
- **Env Vars**: Required and optional environment variable names.

### The Derive-or-Reference Rule
Documentation should never "freeze" a volatile fact by hardcoding it without a source citation.
1. **Prefer Pointers**: Link to the T0/T1 source instead of duplicating the value.
2. **Derive with Citations**: If a value must be included for readability, it must cite the source `file:line` and be gated by the drift linter.
3. **No Stale Values**: Hardcoding a value that contradicts T0/T1 is a blocking build failure.

---

## 4. Drift Gating and Exceptions

The `scripts/check_doc_freshness.py` linter enforces alignment between T0/T1 sources and gated documentation.

### Linter Scope
The linter gates **high-confidence structured facts only** (e.g., version numbers, counts, specific config values) and does **NOT** semantically validate prose or general narrative claims.

### Staleness Budget and Freshness
- **Gated Docs (T1)**: Must be 100% aligned with T0 at every commit.
- **Operational Rollups (T3)**: Should reflect the current state of raw logs within a 24-hour window.
- **Narrative Docs (T2)**: Should be reviewed for drift during every major wave or milestone.

### Conflict Resolution
- **Same-Tier Conflict**: If two T1 docs disagree, the conflict must be flagged and resolved by a human.
- **Cross-Tier Conflict**: The higher-tier source (e.g., T0 over T1) is automatically considered the truth.

### Missing or Renamed Sources
- **Fail Mode**: If a source file or line cited in a doc is missing or renamed, the linter will exit with an error.
- **Report Mode**: The linter will issue a warning but exit with code 0.

### Intentional Exceptions
When drift is intentional or unavoidable, use the following syntax to suppress linter errors:

`<!-- DOC_FRESHNESS_EXCEPTION: <check_id> expires=YYYY-MM-DD reason="..." -->`

**Requirements for Exceptions:**
- **check_id**: The specific linter check to suppress (e.g., `migration_count`).
- **expires**: A future date in `YYYY-MM-DD` format. Expired exceptions cause build failures.
- **reason**: A non-empty explanation for why the drift is allowed.
- **Scope**: One comment per check. Do not use generic "suppress all" comments.
- **Visibility**: Exceptions are visible in linter reports to ensure they are not forgotten.
