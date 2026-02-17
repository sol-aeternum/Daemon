# Daemon Quick Start Guide

## Setup (2 minutes)

### 1. Add Required API Keys

Edit `.env`:

```bash
# Required - for LLM chat
OPENROUTER_API_KEY=your_key_here

# Optional - for @research agent web search
BRAVE_API_KEY=your_key_here

# Optional - for push notifications
NTFY_TOPIC=your_topic_here
```

**Get keys:**
- OpenRouter: https://openrouter.ai/keys (required)
- Brave Search: https://brave.com/search/api/ (optional)

### 2. Start the App

```bash
cd /home/sol/daemon
docker-compose up -d
```

**Access:**
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000

### 3. Run Without API Keys (Graceful Degradation)

The app starts without keys - features degrade gracefully:

| Missing Key | Behavior |
|-------------|----------|
| `OPENROUTER_API_KEY` | Chat shows "API key required" error |
| `BRAVE_API_KEY` | @research agent shows "Search unavailable" |
| Both | UI loads, shows connection status |

## Features Available

### Core Chat
- 💬 Streaming responses via SSE
- 🛠️ 7 built-in tools (time, calculate, web search, HTTP, notifications, reminders)
- 🧠 spawn_agent for @research, @image subagents

### Mobile & PWA
- 📱 Mobile-first responsive UI
- 📲 PWA installable (add to home screen)
- 🔌 Offline support with caching

### Voice I/O
- 🎤 Voice input (Speech Recognition)
- 🔊 Text-to-Speech for responses

### Subagents
- 🔍 @research - Parallel web search + synthesis
- 🖼️ @image - Image generation (Gemini 2.5 Flash Image; Max: Gemini 3 Pro Image Preview)
- 💻 @code - Code review (stub)
- 📄 @reader - Document reading (stub)

## Architecture

```
┌─────────────────────────────────────────┐
│  Frontend (Next.js 15 + PWA)            │
│  - Chat UI, Voice I/O, Mobile responsive │
│  - http://localhost:3000                 │
└──────────────────┬──────────────────────┘
                   │ SSE
┌──────────────────▼──────────────────────┐
│  Backend (FastAPI)                      │
│  - Chat streaming, Tool framework       │
│  - Subagent spawning (@research, @image)│
│  - http://localhost:8000                │
└──────────────────┬──────────────────────┘
                   │
         ┌─────────┴──────────┐
         │                    │
   OpenRouter API      Brave Search API
   (LLM models)        (web search)
```

## Troubleshooting

**"Cannot connect to Docker daemon"**
```bash
# Start Docker service
sudo systemctl start docker
# Or on macOS: open Docker Desktop
```

**"BRAVE_API_KEY not configured"**
- Add key to `.env` or ignore (research agent will show error)

**"OPENROUTER_API_KEY not configured"**
- Required for chat - add key to `.env`

**Provider still showing Venice**
- Fixed! Changed DEFAULT_PROVIDER to "openrouter" in your .env
- Restart: `docker-compose restart orchestrator`

## Model Tiers

| Tier | Price | Orchestrator | Subagents |
|------|-------|--------------|-----------|
| free | $0 | Kimi K2.5 | Qwen/Gemini |
| starter | $9/mo | Kimi K2.5 | Sonnet/Gemini |
| pro | $19/mo | Kimi K2.5 | Better subagents |
| max | $29/mo | Claude Opus | Best quality |
| byok | $9/mo | Your key | Your key |

Set tier via `DEFAULT_TIER` in `.env` (default: "pro")
