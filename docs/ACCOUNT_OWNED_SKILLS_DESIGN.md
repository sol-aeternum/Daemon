# Account-owned skills design

**Status:** Proposed design for review. It implements nothing, changes no feature
status, and authorises no migration, deployment or API change. Each change it
describes still needs approval under the Rules of Engagement in `AGENTS.md`.

**Supersedes:** §4 "Published skills and portable editing" of the earlier
supporting-source design (#601 working draft), which bundled skills with
reminders, contextual home and memory publication.

**Related:** [#91](https://github.com/sol-aeternum/Daemon/issues/91) (skill
format and on-demand loading), [#696](https://github.com/sol-aeternum/Daemon/issues/696)
(skill authority, fixed in #697), `docs/ACCOUNT_DELETION_DESIGN.md` §4 (export
and erasure of learned skills).

## 1. Owner decisions (2026-11-12)

| ID | Decision | Consequence |
| --- | --- | --- |
| S1 | Skills are **account-private**. The only shared skills are read-only ones **shipped** with the application. | No promotion of an account's skill to shared, so no operator publication, review queue or installation-wide revocation barrier. |
| S2 | **PostgreSQL is authoritative.** `SKILL.md` with YAML frontmatter is the import and export format. | Skill content is encrypted at rest and follows account erasure. #91's "not single-row in DB" anti-goal is amended (§9). |
| S3 | **Save is publish, with revision history.** | One transaction per save. No drafts, separate publish step, export job or Git sidecar. |
| S4 | #91's on-demand loading, path scoping and description cap stay a **separate change**. | This design reserves the fields and changes #91's storage criteria only. |

## 2. Current state

Evidence is from `main` at `5507abd5`.

| Fact | Evidence |
| --- | --- |
| Markdown files in one directory are canonical. `skill_projections` is a derived index keyed by filename stem, with no account column. | `migrations/028_skill_projection.sql`, `orchestrator/skills_store.py` (`SKILLS_DIR`, `list_skills`) |
| Every authenticated device can list, read, create, edit, enable, delete and download every skill. | `orchestrator/routes/skills.py`: all routes use `require_device_auth` and no owner filter |
| The skill index injected into a prompt is built without an account. | `build_skill_index(db_pool=db_pool)` at `orchestrator/main.py:1623`, `:3003` and `orchestrator/tasks/runner.py:658` |
| The evaluator derives skills from one account's completed turn and writes them into the shared directory. Deduplication and consolidation search all skills. | `orchestrator/skill_evaluator.py` (`_create_skill`, `_find_best_match`), `orchestrator/memory/store.py:3360` (`get_autonomous_skill_candidates`) |
| If the database read fails, the index silently falls back to the files. | `build_skill_index`, `except Exception` branch |
| The account writer catalogue records the skill store as unowned and uncovered by erasure. | `tests/fixtures/account_writer_catalogue.json` (`skill_projections`: "Uncovered") |
| No shipped skills exist. `data/repo_skills` is not in the repository, and `load_document_skill` has no caller. | `git ls-files`, `orchestrator/skills_loader.py` |
| The upgrade service assumes shipped skills are copied into the shared directory and edited there, and reconciles the two with a manifest, snapshots and a pending-update state. | `orchestrator/skills_upgrade.py` (790 lines) |

The consequence that matters: with more than one account, text derived from one
account's conversation reaches every other account's prompt, and no skill is
covered by account export or erasure.

**Inventory.** The live `data/skills` directory holds one file,
`pending-skill.md`. The count of `skill_projections` rows in the live database
has not been queried; it is a cutover step (§8).

## 3. Data model

Two tables replace `skill_projections`. Column names are proposals.

**`skills`** — one row per skill, holding the current version.

| Column | Notes |
| --- | --- |
| `id UUID PRIMARY KEY` | Internal identity. |
| `user_id UUID NULL REFERENCES users(id) ON DELETE CASCADE` | Owner. `NULL` means shipped. |
| `slug_encrypted TEXT NOT NULL` | The existing normalised id (`normalize_skill_id`). It is derived from the name, so it is content and is not stored in plaintext. |
| `slug_lookup BYTEA NOT NULL` | Keyed digest of the slug (§3.1), used for lookup and uniqueness: one unique index on `(user_id, slug_lookup)` for account skills and one partial unique index on `(slug_lookup) WHERE user_id IS NULL`. |
| `name_encrypted`, `description_encrypted`, `body_encrypted`, `trigger_conditions_encrypted` | Fernet, as for messages and memories. |
| `revision INTEGER NOT NULL` | Current revision number, starting at 1. |
| `source_type skill_source_type` | Existing enum. Set by the server, never by the caller. |
| `allow_autonomous_edit BOOLEAN NOT NULL DEFAULT FALSE` | Changed only by the owner's explicit grant, as fixed in #697. |
| `enabled BOOLEAN NOT NULL DEFAULT TRUE` | Account skills only; shipped enablement is in §5. |
| `embedding VECTOR(1024)` | Plaintext for pgvector, the existing convention. Residual leakage in §3.1. |
| `embedding_revision INTEGER` | The revision the embedding was computed from. |
| `use_count`, `last_used_at`, `complexity_origin` | Carried over unchanged. Counts and a timestamp; `complexity_origin` is a tool-call count. |
| `created_at`, `updated_at` | |

**`skill_revisions`** — one row per save.

| Column | Notes |
| --- | --- |
| `skill_id UUID REFERENCES skills(id) ON DELETE CASCADE`, `revision INTEGER` | Primary key together. |
| `name_encrypted`, `description_encrypted`, `body_encrypted`, `trigger_conditions_encrypted` | The saved content. |
| `actor TEXT` | `user`, `autonomous`, `consolidation` or `shipped`. |
| `created_at` | |

Revision retention is a fixed count per skill (20, §10 O3). Deleting a
skill deletes its revisions; deleting an account deletes both through the
cascade. Reverting writes the chosen old content as a **new** revision; the
revision number never moves backward.

### 3.1 What is stored in plaintext

Every column that can carry text written by a person or a model is encrypted.
The audit below covers each persisted field, including the existing tables this
design touches.

| Field | Treatment | Reason |
| --- | --- | --- |
| Name, description, body, trigger conditions | Encrypted in `skills` and `skill_revisions`. | `trigger_conditions` is generated by the evaluator from the turn and falls back to the description (`orchestrator/skill_evaluator.py:386–398`); today it is plaintext `TEXT`. |
| Slug | Encrypted, with a keyed digest for lookup. | Derived from the name. The digest is HMAC-SHA-256 over the owner id and slug, under a key derived from the content-encryption key, so equal slugs in different accounts do not match and a guess cannot be confirmed without the key. |
| Content hash | **Not stored.** | An unkeyed hash of private content lets anyone with database or backup access confirm a guessed body. Its only uses were drift detection against files, which no longer exists, and guarding the embedding write, which `embedding_revision` now does. Export computes a hash on the fly if a client wants one. |
| `origin_url`, `created_by`, `source_file_path`, `pending_update` | Dropped. | No remaining use. `pending_update` carried skill content as JSONB. |
| `skill_consolidation_log.skill_name`, `.skill_description`, `.reason` | Name and description columns dropped; the row references the skill id. `reason` becomes an enumerated code. | Existing plaintext copies of skill content in an account-owned audit table (`migrations/029_skill_consolidation_nudge.sql`). |
| `source_type`, `actor`, `enabled`, `allow_autonomous_edit`, `revision`, counts, timestamps | Plaintext. | Enumerations, flags, numbers and times; no free text. |

**Residual leakage, accepted and documented.** The embedding is stored in
plaintext because pgvector cannot search ciphertext; this is the same
convention and the same exposure as memory embeddings. An embedding can reveal
the topic of a skill and, with effort, approximate its wording to someone who
holds the database or a backup. Row counts, sizes of ciphertext, usage counts
and timestamps are also visible. Account erasure removes all of them with the
row; backups follow `docs/BACKUP_RETENTION.md`.

Limits (proposed, refused with a stable reason like the reminder limits): 200
skills per account, body 64,000 characters, description 1,536 characters
(matching #91).

## 4. Write path

One service owns every write. HTTP routes, the `skill_manage` tool, the
evaluator, consolidation and the shipped-skill loader all call it; none writes
SQL or files directly.

A save is one transaction:

1. Lock the `skills` row `FOR UPDATE` (or the `users` row when creating, to
   bound the per-account count, as `ReminderStore.add` does).
2. Check that the authenticated account owns the row. A shipped row refuses
   every account write.
3. For an autonomous or consolidation actor, require `allow_autonomous_edit`
   to be exactly true, read under the lock. This keeps the #697 boundary: a
   grant revoked before the lock is taken stops the write.
4. If the caller sent an expected revision and it differs, refuse with a
   conflict and change nothing.
5. Insert the new `skill_revisions` row, update the head row, and trim
   revisions beyond the retention count.

The embedding is computed after commit and stored, with `embedding_revision`,
only if the head revision is still the one it was computed from, so a slow embedding call never overwrites a newer save. A skill with no
embedding stays usable; it is only excluded from similarity search.

Authority never comes from the request. The tool keeps refusing
`caller_autonomous`, `allow_autonomous_edit` and `source_type` arguments, and
the same fields in imported frontmatter are ignored.

## 5. Read path

- `build_skill_index` takes the account and returns that account's enabled
  skills plus enabled shipped skills. Selection order and the token budget are
  unchanged. All three call sites pass the account of the request or task.
- A database failure yields an empty index and a logged error. There is no
  filesystem fallback.
- Tool listing, viewing and similarity search are scoped to the account.
  Evaluator deduplication matches only the same account's skills.
- An account skill and a shipped skill may share a slug; the account's skill
  wins in the index and in slug lookups.

**Shipped skills.** The loader reads the repository directory at startup and
writes rows with `user_id NULL`, `source_type 'system'`, `actor 'shipped'`. A
changed file becomes a new revision; a removed file deletes the row. Because
accounts can no longer edit a shipped skill in place, the manifest, snapshot and
pending-update machinery in `skills_upgrade.py` has nothing left to reconcile
and is removed (§10 O1). An account that wants a modified copy saves one as its
own skill. An account can switch a shipped skill off (§10 O2).

## 6. Autonomous writers

- The evaluator creates skills in the account of the turn it evaluated. It may
  refine only that account's skills that carry the grant. It never changes a
  shipped skill; a proposed refinement of one is dropped.
- Consolidation selects and merges candidates within one account. The current
  global candidate query gains an account filter, and the merge runs through
  the write service so it is recorded as a revision.

## 7. Import, export and API

- **Format.** `SKILL.md`: YAML frontmatter (`name`, `description`, `version`,
  and `paths` reserved for #91) followed by the Markdown body. Frontmatter is
  parsed with `safe_load` and bounded before parsing.
- **Import** creates a skill or a new revision of the caller's own skill. No
  path in the file or filename is used for storage.
- **Export** renders the current revision on request. Nothing is written to
  disk at runtime, which removes the path-safety surface in `skills_store.py`.
- **Account export and erasure.** Skills and their revisions join the full
  context export, and the writer catalogue entries change from "Uncovered" to
  owned through `users`.

API changes, each needing approval before implementation:

| Change | Kind |
| --- | --- |
| Existing routes keep their paths and address a skill by slug within the caller's account. | Behaviour: results are now per account. |
| Responses add `scope` (`account` or `shipped`) and `revision`. | Additive. |
| Writes to a shipped skill return 403. | New refusal. |
| `PUT` accepts an optional expected revision and returns 409 on mismatch. | Additive. |
| `GET /skills/{slug}/revisions` and `POST /skills/{slug}/revert`. | New endpoints. |
| `POST /skills/{slug}/pending-update` and `POST /skills/admin/sync`. | Removed with the upgrade machinery (§10 O1). |

No SSE event type changes. The Feature Matrix row "Skill Management (CRUD)" is
updated when the cutover ships, not by this document.

## 8. Cutover and legacy data

Existing files and `skill_projections` rows have no owner, and none can be
inferred. Proposed handling, consistent with the reminders fix (#698):

1. **Count, do not import.** A read-only step reports the number of files in
   `data/skills` and rows in `skill_projections`. The known live state is one
   file.
2. **No automatic assignment.** Legacy files are never read by the new code. An
   operator who wants one kept uploads it into a named account through the
   normal import.
3. **Switch readers and writers together.** The routes, tool, evaluator,
   consolidation and index move to the new service in one change, so there is
   never a period where one path writes the database and another reads files.
4. **Drop later.** `skill_projections` and the sync, drift and upgrade modules
   are removed in a following change, after the cutover has run in production.

The tools do not refuse while unowned legacy files remain; the count is logged
at startup (§10 O4).

Proposed sequence of small changes:

| Step | Content | Ships behaviour? |
| --- | --- | --- |
| 1 | Migration, write service and store, PostgreSQL tests. | No |
| 2 | Cutover of all readers and writers; account scoping; catalogue update. | Yes |
| 3 | Revision list and revert endpoints, settings UI. | Yes |
| 4 | Shipped-skill loader; removal of projection, sync and upgrade code. | Cleanup |

## 9. Reconciliation with #91

#91 was written when files were canonical. Under S2 and S4:

| #91 item | Outcome |
| --- | --- |
| `SKILL.md` with frontmatter; `safe_load`; 1,536-character description cap | Kept. The format and cap are defined here; enforcement on load stays in #91. |
| Description always in context, body loaded on use; rehydration after compaction | Unchanged and still #91's work. The loader reads the body from the database. |
| `path_patterns` field | Reserved as frontmatter `paths`; the column is added by #91. |
| "Migration: row-per-metadata + filesystem body" | Replaced: metadata and body are both rows. |
| Anti-goal "single-row-in-DB (not diffable, not shareable)" | Amended. Diffing and sharing are met by export and by revision history. |

The issue text is not edited until this design is approved.

## 10. Further owner decisions (accepted 2026-11-12)

The owner accepted the proposed answer to each question below.

| ID | Question | Decision |
| --- | --- | --- |
| O1 | Remove the shipped-skill upgrade machinery (manifest, snapshots, pending updates, admin sync)? | Yes. It exists to reconcile in-place edits that S1 removes, and no shipped skill exists today. |
| O2 | Can an account disable a shipped skill? | Yes, through a small per-account state table, added only when the first shipped skill is. |
| O3 | Revision retention. | Last 20 per skill; no time limit. |
| O4 | Refuse skill tools while unowned legacy files remain? | No. Log the count at startup. Unlike reminders, nothing presents the legacy files as the account's own data once the readers are switched. |
| O5 | Encrypt shipped skill content? | Yes, for one code path, although the content is public. |

Raised in review and **not yet decided by the owner**:

| ID | Question | Proposed |
| --- | --- | --- |
| O6 | How is a skill addressed without a plaintext slug? | Keyed lookup digest (§3.1), which keeps the existing route paths. The alternative is to address skills by UUID, which is simpler to store but changes every skill route and the settings UI. |
| O7 | Existing `skill_consolidation_log` rows hold plaintext names and descriptions. | Drop the two columns in the cutover migration; the history loses its captions but keeps actions, times and skill ids. |

## 11. Tests required before cutover

| ID | Test |
| --- | --- |
| T1 | Account A's skill never appears in account B's list, view, index, tool listing, similarity search, export or download; guessed slugs and ids return not found. |
| T2 | Evaluator output from A's turn is stored under A only; deduplication does not match B's or shipped skills. |
| T3 | Every account write to a shipped skill is refused, through HTTP, the tool, the evaluator and consolidation. |
| T4 | Save, grant revocation and delete race in both orders against PostgreSQL; a revoked grant never yields an autonomous revision (carried over from #697). |
| T5 | Expected-revision mismatch changes nothing; concurrent saves produce consecutive revisions with no gap or duplicate. |
| T6 | Revert creates a new revision; retention trimming never removes the current one. |
| T7 | Database failure gives an empty index and no file read. |
| T8 | Import ignores authority fields and oversized or malformed frontmatter is refused; export then import round-trips content. |
| T9 | Account deletion removes skills and revisions; the writer catalogue matches discovery. |
| T10 | Per-account and per-skill limits at one below, equal and one above. |
| T11 | At rest, against PostgreSQL: after the evaluator creates and refines a skill from a turn containing marker strings, and after consolidation, export and revert, no marker appears in any column of `skills`, `skill_revisions` or `skill_consolidation_log` read as raw text; no unkeyed digest of the content is stored; after account erasure no row, revision, digest or embedding for that account remains. |

## 12. Out of scope

Operator publication of account skills, sharing between accounts, drafts, Git
working-directory sync, on-demand body loading and path scoping (#91), and any
change to how the evaluator decides that a turn deserves a skill.
