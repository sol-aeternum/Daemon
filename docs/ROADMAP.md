# Implementation Roadmap — Daemon

> **Verified-against-commit**: `3155d69fa1eb1939cf5c737018242fc119480d6c`
> **Last updated**: 2026-10-09 (active-execution update only; the verification pin above remains historical)
> **Upstream Sources**: `docs/SOURCES_OF_TRUTH.md`, `docs/MEMORY_UPGRADE_ROADMAP.md`, `.sisyphus/plans/`

## 1. Pointer Document Statement

This document serves as a high-level index for Daemon's product direction and active execution plans. It is a T2 Narrative Status document as defined in [SOURCES_OF_TRUTH.md](SOURCES_OF_TRUTH.md). Detailed technical specifications and volatile implementation details are maintained in T0/T1 sources to prevent documentation drift.

## 2. Active Execution

Active implementation work, including current waves and task-level tracking, is managed via Sisyphus plans.

- **Active Plans**: [../.sisyphus/plans/](../.sisyphus/plans/)
- **Product Direction**: [Daemon vision](DAEMON_VISION.md) and [approved interview decisions](DAEMON_VISION_DECISIONS.md).
- **Current Focus**: Integrate the vision, stabilise continuity-critical baseline defects, then implement broad assistant continuity in slices per the approved [durable-request architecture](DURABLE_REQUEST_DESIGN.md). See [integration evidence](VISION_INTEGRATION_REPORT.md); this is not a completed feature milestone.

### Proposed next sequence — 9 October 2026

Revalidated against main `40a8b66c68769678da3ae7751d2f313b933d0ca4` and the linked issue/PR states on this date. This is an execution proposal within [durable-request slices 1–6](DURABLE_REQUEST_DESIGN.md#16-staged-implementation-plan), not new ratified scope or a release claim.

1. **Finish recovery and enablement evidence (slice 1).** [#477](https://github.com/sol-aeternum/Daemon/issues/477) remains open. [#492](https://github.com/sol-aeternum/Daemon/pull/492) is a draft recovery closeout, not landed source. [#491](https://github.com/sol-aeternum/Daemon/pull/491) merged the non-idempotent media-submission retry safeguard only; it does not complete account deletion or durable recovery. Account fence-and-drain [#469](https://github.com/sol-aeternum/Daemon/issues/469) and legacy-cache privacy acceptance [#486](https://github.com/sol-aeternum/Daemon/issues/486) remain separate. Revalidate current-head checks and review, cross-client recovery and restart evidence before any enablement decision; deployment and `DURABLE_CHAT_ENABLED` activation still require owner approval.
2. **Research that returns durable outputs (slices 2–3).** Preserve bounded read retries, freshness, provenance and operation-specific recovery. Resolve [choice C](DURABLE_REQUEST_DESIGN.md#13-design-choice-c--owned-resources-and-legacy-artifact-handling-pending) before registry/storage implementation. [#489](https://github.com/sol-aeternum/Daemon/issues/489) scopes the complete account-owned artifact catalog and replayable media; [#487](https://github.com/sol-aeternum/Daemon/issues/487) stays the separate read-only status/files/chart pilot. Neither issue authorizes storage, schema/API changes, generation-route enablement or deployment.
3. **Budget waiting and safe resumption (slice 4).** Preserve progress on capacity interruption, notify through approved channels, retain the auto-resume opt-out and recheck authority, input freshness and budget before resuming (DEC09). Slice 1's terminal capacity denial and manual retry are not this completed contract. Slices 5–6 remain necessary for clarification, cancellation and uncertain-effect reconciliation before a broad-continuity milestone.

**Proposed evaluation:** test whether a successful repeated task can become an inspectable, versioned reusable workflow, with source provenance, explicit inputs and the same authority/budget checks on every run. Reopening saved output must not execute it. This is an evaluation question, not approval for automatic workflow creation, a new runtime or expanded permissions.

## 3. Memory Evolution

The long-term trajectory for memory system improvements, including benchmark-driven waves for retrieval and extraction uplift, is defined in the specialized memory roadmap.

- **Memory Roadmap**: [MEMORY_UPGRADE_ROADMAP.md](MEMORY_UPGRADE_ROADMAP.md)
- **Core Specification**: [../MEMORY_LAYER.md](../MEMORY_LAYER.md)

## 4. Product Pillars

Daemon's development is guided by five durable product pillars.

### Cloud Orchestration
Maintaining a robust, multi-provider orchestration layer with intelligent routing and typed SSE streaming. This includes the subagent framework for specialized tasks like research, image generation, and audio processing.

### Persistent Memory
Evolving the pgvector-backed memory pipeline to provide seamless, long-term context across conversations. Focus areas include fact extraction precision, hybrid retrieval scoring, and bitemporal data management.

### Multimodal Capabilities
Expanding the assistant's ability to generate and process rich media, including high-fidelity images, video generation via providers like fal/Kling and xAI, and streaming audio I/O.

### Local Pipeline
Local inference is an optional extension, not a transition away from the hosted default or a prerequisite for phone-only use. Companion access and local inference are separate capabilities; see the product vision's non-goals and open decisions.

### Hardening & Governance
Ensuring system reliability through improved test coverage, security hardening, and automated documentation freshness governance via the `check_doc_freshness.py` utility.
