# Daemon Orchestrator (Phase 1)

FastAPI service that streams chat responses over SSE and routes LLM calls via LiteLLM.

## Local dev

Prereqs: `uv` installed.

```bash
cd daemon
uv run uvicorn orchestrator.main:app --reload --host 0.0.0.0 --port 8000
```

Health check:
```bash
curl http://localhost:8000/health
```

SSE chat (streaming):
```bash
curl -N -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer YOUR_KEY' \
  -d '{"message":"hello"}'
```

## Docker

```bash
cd daemon
cp .env.example .env
docker compose up --build
```

## Env vars

See `.env.example`.
