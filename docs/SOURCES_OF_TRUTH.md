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

| File | Classification | Role |
|------|----------------|------|
| `MEMORY_LAYER.md` | **T1** | Authoritative memory architecture spec. |
| `docs/FEATURE_MATRIX.md` | **T1** | Authoritative user-visible feature state. |
| `docs/TECHNICAL_SPECS.md` | **T1** | Technical implementation details (schema, API, tiers). |
| `docs/PROJECT_CONTEXT.md` | **T1** | Architecture summary and implementation status. |
| `docs/OPEN_QUESTIONS.md` | **T1** | Decision log and unresolved technical questions. |
| `docs/MEMORY_UPGRADE_ROADMAP.md` | **T1** | Technical research and wave planning for memory. |
| `docs/PROJECT_BRIEF.md` | **ungated-reference** | High-level project overview. |
| `docs/CURRENT_ISSUES.md` | **T3** | **Operational-rollup** of active system issues. |
| `docs/ROADMAP.md` | **T2** | **Pointer** to active plans and product direction. |
| `TRIAGE.md` | **raw-log** | Append-only diagnostic capture. |
| `frontend/PWA_CHECKLIST.md` | **raw-log** | Build failure and environment report. |
| `README.md` | **ungated-reference** | High-level project overview. |
| `QUICKSTART.md` | **ungated-reference** | Procedural setup instructions. |
| `AGENTS.md` | **ungated-reference** | Agent instructions and behavior rules. |
| `.github/pull_request_template.md` | **ungated-reference** | PR checklist template. |
| `docs/interactive-artifact-examples.md` | **ungated-reference** | Example code and artifact usage. |
| `frontend/PWA_SETUP.md` | **ungated-reference** | Procedural PWA setup guide. |

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
