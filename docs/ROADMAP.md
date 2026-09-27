# Implementation Roadmap — Daemon

> **Verified-against-commit**: `3155d69fa1eb1939cf5c737018242fc119480d6c`
> **Last updated**: 2026-05-31
> **Upstream Sources**: `docs/SOURCES_OF_TRUTH.md`, `docs/MEMORY_UPGRADE_ROADMAP.md`, `.sisyphus/plans/`

## 1. Pointer Document Statement

This document serves as a high-level index for Daemon's product direction and active execution plans. It is a T2 Narrative Status document as defined in [SOURCES_OF_TRUTH.md](SOURCES_OF_TRUTH.md). Detailed technical specifications and volatile implementation details are maintained in T0/T1 sources to prevent documentation drift.

## 2. Active Execution

Active implementation work, including current waves and task-level tracking, is managed via Sisyphus plans.

- **Active Plans**: [../.sisyphus/plans/](../.sisyphus/plans/)
- **Product Direction**: [Daemon vision](DAEMON_VISION.md) and [approved interview decisions](DAEMON_VISION_DECISIONS.md).
- **Current Focus**: Integrate the vision, stabilise continuity-critical baseline defects, then approve [durable-request architecture](DURABLE_REQUEST_DESIGN.md) before implementing broad assistant continuity. See [integration evidence](VISION_INTEGRATION_REPORT.md); this is not a completed feature milestone.

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
