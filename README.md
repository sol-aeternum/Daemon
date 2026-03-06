# Daemon

**Multi-provider LLM orchestration platform with intelligent routing, persistent memory, and subagent architecture.**

Daemon is not another ChatGPT wrapper. It's an orchestration layer that sits between multiple LLM providers and multiple frontends, adding capabilities that no single provider offers: cross-provider routing with failover, persistent conversational memory via pgvector, subagent spawning for task decomposition, and a unified API surface compatible with any OpenAI-standard frontend.

## Why This Exists

Commercial LLM products lock you into a single provider, a single model, and their memory implementation. Daemon inverts that: you own the orchestration, the memory, and the routing logic. Switch providers without losing conversation history. Route different query types to different models. Spawn specialised subagents for complex tasks. Run it locally, keep your data.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Frontends                         │
│   Open WebUI  ·  Custom Client  ·  API Direct       │
└──────────────────────┬──────────────────────────────┘
                       │ OpenAI-compatible API
┌──────────────────────▼──────────────────────────────┐
│                  Daemon Core                         │
│                                                      │
│  ┌─────────┐  ┌──────────┐  ┌────────────────────┐  │
│  │ Router  │→ │ Provider │→ │ LiteLLM Streaming  │  │
│  │         │  │ Registry │  │ (SSE)              │  │
│  └─────────┘  └──────────┘  └────────────────────┘  │
│       │                                              │
│  ┌────▼────────────┐  ┌──────────────────────────┐  │
│  │ Memory Layer    │  │ Subagent Orchestrator    │  │
│  │ (pgvector)      │  │ (task decomposition)     │  │
│  └─────────────────┘  └──────────────────────────┘  │
└──────────────────────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│               LLM Providers                          │
│  OpenRouter · Local Models · Custom Endpoints        │
└──────────────────────────────────────────────────────┘
```

### Key Design Decisions

**Multi-provider routing over single-provider lock-in.** LiteLLM abstracts provider differences. Any OpenAI-compatible endpoint can be added via environment variables. Default provider is configurable per-deployment; per-request overrides allow model selection at query time.

**Persistent memory via pgvector.** Conversations are embedded and stored in PostgreSQL with vector similarity search. This enables context retrieval across sessions — the system remembers prior interactions without relying on provider-side memory (which you don't control and can't inspect). See [MEMORY_LAYER.md](MEMORY_LAYER.md) for implementation details.

**Subagent spawning for task decomposition.** Complex requests can be broken into subtasks handled by specialised agents, each potentially using different models optimised for their task type. The orchestrator manages coordination and result synthesis.

**OpenAI API compatibility as integration strategy.** By implementing `/v1/models` and `/v1/chat/completions`, Daemon works with Open WebUI, any OpenAI SDK client, or custom frontends without modification. This is a deliberate architectural choice — compatibility with the dominant API standard maximises frontend optionality.

**SSE streaming with keepalive.** Real-time token streaming with configurable ping intervals prevents connection drops on slow generations.

## Project Structure

```
Daemon/
├── orchestrator/       # Core: routing, provider config, streaming, API
│   ├── main.py         # FastAPI app + OpenAI-compatible endpoints
│   ├── daemon.py       # LiteLLM streaming integration
│   ├── config.py       # Multi-provider configuration
│   ├── router.py       # Message routing logic
│   └── models.py       # Pydantic schemas (inc. OpenAI-compatible types)
├── backend/            # Backend services and data layer
├── frontend/           # Web frontend
├── migrations/         # Database migrations
├── tests/              # Test suite
├── scripts/            # Utility scripts
├── .sisyphus/          # Agent workflow configuration
├── .envsitter/         # Environment management tooling
├── MEMORY_LAYER.md     # Memory system design document
├── QUICKSTART.md       # Quick setup guide
├── docker-compose.yml  # Full-stack deployment
└── Dockerfile          # Container build
```

## Quick Start

**Prerequisites:** [uv](https://github.com/astral-sh/uv) installed.

```bash
# Local development
uv run uvicorn orchestrator.main:app --reload --host 0.0.0.0 --port 8000

# Docker (full stack)
cp .env.example .env    # Configure providers
docker compose up --build
```

Verify: `curl http://localhost:8000/health`

See [QUICKSTART.md](QUICKSTART.md) for detailed setup.

## API

### Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/v1/models` | GET | List available models (OpenAI-compatible) |
| `/v1/chat/completions` | POST | Chat completion, streaming and non-streaming |
| `/chat` | POST | Native Daemon chat with SSE streaming |
| `/providers` | GET | List configured providers and default |
| `/health` | GET | Health check |

### Provider Configuration

Daemon supports any OpenAI-compatible provider via environment variables:

```bash
# Built-in: OpenRouter
DEFAULT_PROVIDER=openrouter
OPENROUTER_API_KEY=your-key

# Custom provider (any OpenAI-compatible API)
PROVIDER_MYSERVICE_BASE_URL=https://api.example.com/v1
PROVIDER_MYSERVICE_API_KEY=your-key
PROVIDER_MYSERVICE_MODEL=model-name
```

Per-request provider override:
```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"hello","provider":"myservice"}'
```

See [.env.example](.env.example) for all configuration options.

## Open WebUI Integration

```bash
# Start Daemon, then:
docker run -d -p 3000:8080 \
  -e OPENAI_API_BASE_URL="http://host.docker.internal:8000/v1" \
  -e OPENAI_API_KEY="your-daemon-api-key" \
  -e ENABLE_OLLAMA_API=False \
  -e ENABLE_OPENAI_API=True \
  ghcr.io/open-webui/open-webui:main
```

## Status

Active development. Core orchestration, multi-provider routing, OpenAI-compatible API, and Open WebUI integration are functional. Memory layer and subagent system in progress.

## License

[Specify your license]
