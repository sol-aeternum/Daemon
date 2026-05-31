# Project Brief — Daemon

> **Verified-against-commit**: `3155d69fa1eb1939cf5c737018242fc119480d6c`
> **Last updated**: 2026-05-31
> **Upstream Sources**: `tests/benchmark_results/doc-alignment-regeneration/truth_set.md`, `docs/SOURCES_OF_TRUTH.md`, `docs/FEATURE_MATRIX.md`, `MEMORY_LAYER.md`, `orchestrator/config.py`, `docker-compose.yml`, `migrations/`

## Product Thesis

Daemon is a multi-provider LLM orchestration platform designed to provide a unified, intelligent interface for personal AI assistance. It sits between multiple LLM providers and a custom frontend, adding critical capabilities that single-provider solutions lack: tiered cross-provider routing, persistent conversational memory, specialized subagent task decomposition, and a typed real-time event protocol.

## Intended Audience & User Value

Daemon is built for users who prioritize ownership and flexibility in their AI interactions.

- **Provider Independence**: Switch between LLM providers (via OpenRouter) without losing conversation history or changing your interface.
- **Persistent Memory**: Own your conversational context with a pgvector-backed memory pipeline that extracts and retrieves facts across sessions.
- **Intelligent Routing**: Automatically route queries to the most appropriate model based on complexity, cost, and capability tiers.
- **Specialized Subagents**: Delegate complex tasks to dedicated agents for research, media generation, and document processing.
- **Privacy & Control**: Run the entire stack via Docker, with encrypted-at-rest storage and a clear path toward local inference.

## Architecture Overview

Daemon's architecture is designed for durability and modularity, separating orchestration logic from model providers and client surfaces.

- **Backend**: A FastAPI-based orchestrator that manages routing, streaming, subagent spawning, and the memory pipeline.
- **Frontend**: A modern Next.js 16 PWA utilizing the Vercel AI SDK for a responsive, streaming chat experience with integrated voice I/O.
- **Memory Layer**: A PostgreSQL database with the `pgvector` extension for semantic search, supported by Redis and `arq` for background processing.
- **Fetch Service**: A dedicated `crawl4ai` service for robust, multi-strategy web content retrieval.
- **Infrastructure**: Fully containerized deployment via Docker Compose, ensuring a reproducible environment across cloud and local setups.

For detailed technical specifications and infrastructure details, refer to [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) and [TECHNICAL_SPECS.md](TECHNICAL_SPECS.md).

## Capability Overview

Daemon provides a comprehensive suite of features for advanced AI assistance.

- **Chat & Routing**: Real-time token streaming with typed SSE events, supporting both native and OpenAI-compatible endpoints with tier-based model assignment.
- **Persistent Memory**: Automated fact extraction, hybrid semantic/lexical retrieval, and bitemporal memory management. Detailed in [../MEMORY_LAYER.md](../MEMORY_LAYER.md).
- **Subagent Framework**: Specialized agents for web research (`@research`), image and video generation (`@image`), audio processing (`@audio`), and document generation (`@document`).
- **Multimodal Studio**: Dedicated interfaces for high-fidelity media generation with integrated credit management.
- **Voice Interaction**: Streaming text-to-speech and speech-to-text for a hands-free assistant experience.

Current feature implementation status across all platforms is maintained in the [FEATURE_MATRIX.md](FEATURE_MATRIX.md).

## Documentation & Sources of Truth

Daemon maintains a strict documentation hierarchy to ensure accuracy and prevent drift between code and narrative.

- **Feature Scope**: [FEATURE_MATRIX.md](FEATURE_MATRIX.md) defines the authoritative state of all user-visible features.
- **Technical Specs**: [TECHNICAL_SPECS.md](TECHNICAL_SPECS.md) covers API contracts, schemas, and system prompts.
- **Architecture Context**: [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) provides a high-level view of the system's current implementation state.
- **Roadmap**: [ROADMAP.md](ROADMAP.md) outlines the product direction and active execution plans.
- **Governance**: [SOURCES_OF_TRUTH.md](SOURCES_OF_TRUTH.md) defines the documentation hierarchy and freshness rules.

## Getting Started

To set up a local instance of Daemon and begin interacting with the assistant, follow the instructions in the [../QUICKSTART.md](../QUICKSTART.md).
