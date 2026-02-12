# Daemon Orchestrator (Phase 1)

FastAPI service that streams chat responses over SSE and routes LLM calls via LiteLLM.

## Features

- **Multi-Provider LLM Support**: Venice AI (default), OpenRouter, OpenCode Zen, or any custom provider
- **OpenAI-Compatible API**: Works with Open WebUI and other OpenAI-compatible frontends
- **Per-Request Provider Selection**: Override default provider in each chat request
- **Anonymous Mode**: Venice AI works without API key (privacy-focused, no persistent cloud context)
- **SSE Streaming**: Real-time token streaming with keepalive pings
- **Flexible Configuration**: Environment-based provider setup with custom provider support

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

List available providers:
```bash
curl -H 'Authorization: Bearer YOUR_KEY' http://localhost:8000/providers
```

SSE chat (streaming):
```bash
curl -N -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer YOUR_KEY' \
  -d '{"message":"hello"}'
```

SSE chat with specific provider:
```bash
curl -N -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer YOUR_KEY' \
  -d '{"message":"hello","provider":"venice"}'
```

## Docker

```bash
cd daemon
cp .env.example .env
docker compose up --build
```

## Configuration

### Default Provider

Set `DEFAULT_PROVIDER` in `.env`:
- `venice` (default) - Privacy-focused, no account required
- `openrouter` - Multi-provider gateway
- `opencode_zen` - OpenCode AI
- Any custom provider name configured via `PROVIDER_*` vars

### Venice AI (Default)

Venice AI is the default provider. Requires:
1. Free API key: https://venice.ai/settings/api
2. Account balance: https://venice.ai/settings/billing (402 error = needs credits)

```env
DEFAULT_PROVIDER=venice
VENICE_API_KEY=your-venice-api-key-here
VENICE_MODEL=venice-uncensored
```

**Note:** "Anonymous mode" means Venice doesn't store conversation history on their servers, not that you can call their API without authentication or credits.

### OpenRouter

```env
DEFAULT_PROVIDER=openrouter
OPENROUTER_API_KEY=your-key-here
LITELLM_MODEL=openrouter/anthropic/claude-opus-4.5
```

### OpenCode Zen

```env
DEFAULT_PROVIDER=opencode_zen
OPENCODE_API_KEY=your-key-here
OPENCODE_MODEL=opencode/claude-opus-4-5
```

### Custom Providers

Add any OpenAI-compatible provider:

```env
PROVIDER_CUSTOM_BASE_URL=https://api.custom-ai.com/v1
PROVIDER_CUSTOM_API_KEY=your-api-key
PROVIDER_CUSTOM_MODEL=custom-model-name
PROVIDER_CUSTOM_REQUIRES_AUTH=true
```

Then use it:
```bash
curl -X POST http://localhost:8000/chat \
  -H 'Authorization: Bearer YOUR_KEY' \
  -d '{"message":"hello","provider":"custom"}'
```

## API Reference

### POST /chat

Stream chat completion with SSE.

**Request:**
```json
{
  "conversation_id": "optional-conversation-id",
  "message": "Hello, Daemon!",
  "metadata": {},
  "provider": "venice"  // optional, defaults to DEFAULT_PROVIDER
}
```

**Response:** Server-Sent Events stream with `token`, `final`, and `done` events.

### GET /providers

List all configured providers and the current default.

**Response:**
```json
{
  "providers": ["venice", "openrouter", "opencode_zen"],
  "default": "venice"
}
```

### GET /health

Health check endpoint.

**Response:** `{"status": "ok"}`

## Environment Variables

See `.env.example` for all available options.

Key variables:
- `DEFAULT_PROVIDER` - Default LLM provider
- `DAEMON_API_KEY` - API authentication (optional)
- `MOCK_LLM` - Use mock responses for testing
- `REQUEST_TIMEOUT_S` - Request timeout
- `STREAM_PING_INTERVAL_S` - SSE keepalive interval

## Open WebUI Integration

Daemon provides OpenAI-compatible endpoints for seamless integration with Open WebUI.

### Setup

1. Start Daemon:
```bash
cd daemon
uv run uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000
```

2. Run Open WebUI with Daemon as the backend:
```bash
docker run -d -p 3000:8080 \
  -e OPENAI_API_BASE_URL="http://host.docker.internal:8000/v1" \
  -e OPENAI_API_KEY="your-daemon-api-key-or-empty" \
  -e ENABLE_OLLAMA_API=False \
  -e ENABLE_OPENAI_API=True \
  ghcr.io/open-webui/open-webui:main
```

3. Open http://localhost:3000 and start chatting

**Note:** On Linux, use host network mode or the actual IP instead of `host.docker.internal`.

### OpenAI-Compatible Endpoints

Daemon implements the following OpenAI-compatible endpoints:

- `GET /v1/models` - List available models
- `POST /v1/chat/completions` - Chat completions (streaming and non-streaming)

**Test the OpenAI endpoint:**
```bash
# List models
curl http://localhost:8000/v1/models

# Chat completion (non-streaming)
curl -X POST http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "venice-uncensored",
    "messages": [{"role": "user", "content": "Say hello"}],
    "stream": false
  }'

# Chat completion (streaming)
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "venice-uncensored",
    "messages": [{"role": "user", "content": "Say hello"}],
    "stream": true
  }'
```

## Architecture

- **config.py**: Multi-provider configuration with dynamic provider loading
- **daemon.py**: LiteLLM streaming integration with provider-specific handling
- **main.py**: FastAPI endpoints including OpenAI-compatible `/v1/*` routes
- **models.py**: Pydantic models including OpenAI-compatible request/response types
- **router.py**: Message routing logic
