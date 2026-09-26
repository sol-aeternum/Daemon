# Daemon vision — dated Stage 0 reconciliation

Assessment: 26 September 2026, before vision integration. Source vision: external v0.1. Repository snapshot: HEAD `f5a72150` **plus existing staged, unstaged and untracked changes**. This is historical working-tree evidence, not a release certification or a current feature inventory. Current implementation facts remain in code/tests and [FEATURE_MATRIX.md](FEATURE_MATRIX.md).

This repository summary preserves the findings of the original external reconciliation. The interview subsequently selected broad continuity (DEC10), superseding the original narrow document-first recommendation. This report does not claim the missing capabilities became implemented through documentation integration.

**PR-port note:** current main at `2bf65150` includes newer repairs absent from this old checkout, including authenticated artifact namespaces in `orchestrator/artifacts.py`. The ownership finding below is historical, not a claim that current main still serves another account's artifacts. See the integration report for current-branch validation.

## Main finding

Daemon has hosted account identity, a web/PWA client, saved conversations and memory, deterministic CSV/DOCX generation, internal background maintenance and a working-tree account-compute policy implementation. It does not yet establish a general durable user-task/workspace/artifact lifecycle or a resource-scoped device companion.

Authenticated client devices are not filesystem companions. Internal maintenance jobs are not a general task engine. Compute reservations are not task checkpoints.

## Gap map

| Vision/defaults | Assessment at the snapshot | Evidence and boundary |
| --- | --- | --- |
| V01; D05 | Partial | `orchestrator/routes/conversations.py` and `frontend/hooks/useConversationHistory.ts` reload saved conversation state; no shared general task/approval state. |
| V02; D01 | Partial | Backend/PostgreSQL/arq exist; chat is request-bound, no general execution sandbox found. Deployment containers do not establish per-run containment. |
| V03 | Partial | Hosted identity/PWA and text attachments exist; no demonstrated phone-only durable background result workflow. Native clients are not implemented. |
| V04; D02/D03 | Partial identity foundation; companion absent | `orchestrator/routes/auth_setup.py` enrolls client sessions, not selected folders or local operations. |
| V05; D06/D09 | Partial | Account/provider/budget enforcement exists; resource/destination/action grants and artifact ownership remain incomplete. |
| V06; D05 | Partial | Chat can invoke tools; `frontend/app/projects/page.tsx` is a placeholder, not a common project/task/artifact revision model. |
| V07; D04 | Partial | Conversation/memory persistence and generated disk files exist; no general user-task/checkpoint/source-snapshot registry. |
| D07 | Companion access absent | No on-demand companion resource protocol or sync/writeback system found. |
| D08/D10 | Aligned partial foundation | Account limits/deadlines and common privacy/continuity contract exist; durable waiting/budget-paused tasks do not. |

## Acceptance catalogue at the snapshot

| ID | Assessment | Remaining evidence/capability |
| --- | --- | --- |
| AC01 | Partial | Owned durable artifacts and real phone-only completion/reopen check. |
| AC02 | Partial | Shared task/resource revision context and supported document ingestion. |
| AC03 | Partial | Durable accepted work across clients, beyond history reload. |
| AC04 | Partial identity foundation | Resource grants and companion identity/enrolment lifecycle. |
| AC05 | Absent end to end | Laptop source acquisition, editable presentation generation/validation and provenance-backed delivery. |
| AC06 | Partial | Resource/action controls and adversarial enforcement; artifact ownership gap. |
| AC07 | Partial | Per-source policy propagation and live qualification, beyond global approved-route filtering. |
| AC08 | Partial identity foundation | Resource revocation bounds, offline waiting and authorised labelled snapshots. |
| AC09 | Partial | User-task recovery, output publication and external-effect reconciliation. |
| AC10 | Absent | Conflict-safe companion writeback/move and transfer recovery. |
| AC11 | Partial | Account boundaries exist; artifact ownership, restricted-project enforcement and complete deletion policy are missing. |
| AC12 | Partial | Request limits exist; durable pauses, controlled resume and task-mandate enforcement do not. |

No whole acceptance criterion was certified end to end. The revised AC11 follows the later DEC02–DEC03 shared-by-default project decision; original universal project-isolation wording is superseded.

## Important evidence distinctions

- `orchestrator/daemon.py` checks request disconnection while streaming. History reload and keepalive frames are not durable continuation or replay.
- `orchestrator/tools/document.py` supports CSV/DOCX and writes local generated files. `orchestrator/main.py` download handlers authenticate but lacked owner checks. Filename knowledge is not ownership.
- Attachment handling in `orchestrator/main.py` incorporates text, represents unsupported binaries as markers, and does not establish arbitrary Office/PDF extraction. Current compute bounds reject unpriced multimodal input.
- `orchestrator/worker/` provides maintenance, enqueue deduplication and failure audits, not task acceptance, checkpoint recovery or output-commit semantics.
- `orchestrator/memory/store.py` has user-scoped memory and source-type/conversation provenance, not a complete source-version/project policy model. A `local_only` field is not local inference.
- `orchestrator/compute_runtime.py`, `orchestrator/entitlements/`, policy JSON files and migration `039_entitlements_commercial.sql` were untracked additions at inspection. They implement significant approval, reservation, deadline and settlement controls; their presence does not certify migration/deployment.
- Shipped `config/inference_policy.json` approves no routes/services. Deployment overrides and live provider terms were not inspected.

## Open decisions reconciled

O01/O02 companion transport/trust and O03 additional platforms remain open. O04 project defaults and O05 upload/memory defaults were subsequently answered by DEC01–DEC03/DEC07–DEC08; enforcement and lifecycle detail remain open. O06 containment is not established by Docker. O07 has an existing stronger provider contract but requires live qualification and per-resource policy. O08 upload/snapshot semantics precede later sync/writeback. O09 already has commercial vocabulary/accounting; launch economics/task budgets remain open. O10 needs transaction/recovery/service design despite approved continuity behaviour. O11 extensions are not initial prerequisites.

## Findings tracked separately

| Issue | Finding at assessment |
| --- | --- |
| [#310](https://github.com/sol-aeternum/Daemon/issues/310) | Worker/advisor removals left callers/tests unresolved; gate blocker. |
| [#312](https://github.com/sol-aeternum/Daemon/issues/312) | Generated artifacts lacked authenticated owner checks. |
| [#315](https://github.com/sol-aeternum/Daemon/issues/315) | Active narrative documentation overstated retired media/voice execution. |
| [#316](https://github.com/sol-aeternum/Daemon/issues/316) | Explicit disconnect could fall through to complete-message persistence. |
| [#317](https://github.com/sol-aeternum/Daemon/issues/317) | Memory-list search compared plaintext terms to ciphertext. |
| [#318](https://github.com/sol-aeternum/Daemon/issues/318) | Memory edits preserved obsolete embeddings. |

These are historical findings, not assertions that every issue remains unchanged after later work. Follow issue evidence and the [integration report](VISION_INTEGRATION_REPORT.md) for subsequent validation.

## Verification at assessment time

Using the existing isolated locked environment with `PYTHONPATH=.` and `uv run --no-sync`:

- `tests/test_entitlements_policy.py tests/test_compute_runtime.py`: **117 passed**.
- `tests/test_generate_document.py tests/test_chat_history.py tests/test_streaming_message_persistence.py tests/test_enrollment_flow.py tests/test_device_management.py tests/test_auth_user_scoping.py`: **64 passed, 1 failed**. Failure imported removed `SharedEncryptionFailureCounter` through worker startup. Mock-coroutine warnings limit integration confidence.
- `python scripts/lint_feature_matrix.py`: **72 rows validated**.
- `python scripts/check_doc_freshness.py --mode fail`: **No drift detected**, despite semantic prose conflicts.

Total across those non-overlapping groups: **181 passed, 1 failed**. Full release gates were not rerun for the read-only assessment; earlier gate blockers were recorded in `SUBSCRIPTION_MIGRATION_REPORT.md`. No live providers, production migrations, cross-device runtime tests or worker-kill experiments were exercised.

The original assessment proposed a text-to-CSV/DOCX continuity slice. **DEC10 replaces that recommendation with broad assistant continuity**, while retaining bounded test cases and approval before schema/API changes. See [DURABLE_REQUEST_DESIGN.md](DURABLE_REQUEST_DESIGN.md).
