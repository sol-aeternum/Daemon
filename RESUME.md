# Daemon + Open WebUI Setup - Resume State

**Last Updated:** 2026-02-01
**Status:** ✅ Fully Operational

## Services Status

| Service | URL | Status | Notes |
|---------|-----|--------|-------|
| **Daemon API** | http://localhost:8000 | ✅ Running | 88 Venice models |
| **Open WebUI** | http://localhost:8080 | ✅ Running | All models visible |
| **OpenRouter API** | https://openrouter.ai/api | ✅ Connected | Real responses |

## What's Working

### Backend (Daemon)
- ✅ Dynamic model fetching from OpenRouter API
- ✅ 88 models available (Tier-1 sorted: claude-opus-45, kimi-k2-5, kimi-k2-thinking at top)
- ✅ Both `/v1/models` and `/models` endpoints (Open WebUI compatible)
- ✅ Real chat completions with OpenRouter API
- ✅ Streaming responses
- ✅ 5-minute model cache

### Frontend (Open WebUI)
- ✅ All 88 models visible in dropdown
- ✅ Tier-1 models appear first
- ✅ Real-time chat with selected models
- ✅ Model metadata (tier, recommended status)

## Quick Start Commands

```bash
# OpenRouter API key for LLM functionality
OPENROUTER_API_KEY=your_openrouter_api_key_here
```

## Configuration Files

### Daemon Environment (.env)
```
OPENROUTER_API_KEY=<your-key>
OPENROUTER_BASE_URL=https://openrouter.ai/api/api/v1
OPENROUTER_MODEL=openrouter/auto
TIER1_MODELS=claude-opus-45,kimi-k2-5,kimi-k2-thinking
DAEMON_API_KEY=sk-test
```

### Open WebUI Environment Variables
```
OPENAI_API_BASE_URLS=http://172.17.0.1:8000
OPENAI_API_KEYS=sk-test
ENABLE_OLLAMA_API=false
ENABLE_OPENAI_API=true
```

## Key Implementation Details

### Critical Fix Applied
**Problem:** Open WebUI calls `/models` but Daemon only had `/v1/models`
**Solution:** Added `/models` → `/v1/models` redirect in `main.py`

### Model Response Format
```json
{
  "object": "list",
  "data": [
    {
      "id": "openrouter/claude-opus-45",
      "object": "model",
      "created": 1234567890,
      "owned_by": "openrouter",
      "metadata": {
        "recommended": true,
        "tier": "premium",
        "capabilities": ["chat", "streaming"]
      }
    }
  ]
}
```

## Network Configuration

- **Daemon** listens on `0.0.0.0:8000`
- **Open WebUI** container connects via `172.17.0.1:8000` (Docker bridge gateway)
- **Browser** connects to `localhost:8080`

## Troubleshooting

### No Models in Open WebUI
1. Check Daemon is running: `curl http://localhost:8000/v1/models`
2. Check Open WebUI connection: `docker exec open-webui curl http://172.17.0.1:8000/models`
3. Restart Open WebUI: `docker restart open-webui`
4. Wait 10-15 seconds for model fetch

### Daemon Won't Start
- Check for port conflicts: `ss -tlnp | grep 8000`
- Check logs: `cat /tmp/daemon.log`
- Verify Python imports work: `cd /home/sol/Daemon && uv run python3 -c "from orchestrator.main import app"`

### Authentication Issues
- Open WebUI sends `Authorization: Bearer sk-test`
- Daemon expects matching key (set in `.env` as `DAEMON_API_KEY=sk-test`)

## File Locations

```
/home/sol/Daemon/
├── orchestrator/
│   ├── main.py          # API endpoints (includes /models redirect)
│   ├── daemon.py        # LiteLLM integration
│   ├── config.py        # Settings (TIER1_MODELS added)
│   ├── models.py        # Pydantic models (metadata field added)
│   └── models_cache.py  # OpenRouter API fetching with cache
├── .env                 # Environment variables
└── openwebui_model_enhancer.user.js  # Browser userscript (optional)
```

## Changes Made

1. **config.py**: Added `TIER1_MODELS` env var, removed hardcoded model validation
2. **main.py**: Added `/models` redirect, dynamic model fetching, Tier-1 sorting
3. **models.py**: Added `metadata` field to OpenAIModelInfo
4. **models_cache.py**: Created new file for OpenRouter API fetching with caching
5. **daemon.py**: Fixed LiteLLM prefix from `openai/` to `openrouter/`

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/models` | GET | List all models (OpenAI compatible) |
| `/models` | GET | Redirect to `/v1/models` |
| `/v1/chat/completions` | POST | Chat completion (streaming supported) |
| `/health` | GET | Health check |

## Credits Usage

- OpenRouter API key is configured
- Each request consumes credits based on model and token count
- Check OpenRouter dashboard for credit balance: https://openrouter.ai/settings

## Next Steps (Optional)

1. **Add more providers**: OpenRouter, OpenCode Zen already configured in config
2. **Custom model filters**: Fork Open WebUI and add built-in search/filter UI
3. **Performance monitoring**: Add response time logging per model
4. **Model-specific settings**: Different temperature/max_tokens per model tier

---

**To resume:** Run the Quick Start commands above
**Full reset:** `docker rm -f open-webui && docker volume rm open-webui`, then restart services
