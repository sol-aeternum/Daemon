# Daemon Frontend - Implementation Documentation

**Status:** Phase 2 Waves 1-2 Complete (Mobile + PWA + Subagent UI)
**Date:** 2026-02-08

---

## Quick Start

```bash
cd /home/sol/Daemon/frontend
npm install
npm run dev        # localhost:3000
npm run build      # Production build with PWA
```

---

## Architecture Overview

**Stack:** Next.js 16 + React 19 + TypeScript + Tailwind CSS + Vercel AI SDK + shadcn/ui
**PWA:** Serwist with a source-controlled TypeScript service worker
**State:** localStorage for conversations, React hooks for UI state
**Events:** Server-Sent Events (SSE) for streaming chat

---

## Phase 1: Core Chat (Already Complete)

### Components

| File                   | Purpose                                  |
| ---------------------- | ---------------------------------------- |
| `ChatInterface.tsx`    | Main chat UI with message list and input |
| `MessageList.tsx`      | Render messages with markdown support    |
| `StreamingText.tsx`    | Typewriter effect for bot responses      |
| `CloudLocalToggle.tsx` | Switch between cloud/local LLM           |

### Error Handling

| File                   | Purpose                                        |
| ---------------------- | ---------------------------------------------- |
| `ErrorToast.tsx`       | Floating error notifications (auto-dismiss 5s) |
| `ConnectionStatus.tsx` | Live connection state with retry               |
| `ErrorProvider.tsx`    | Global error context                           |

### Conversation History

| File                        | Purpose                                          |
| --------------------------- | ------------------------------------------------ |
| `ConversationList.tsx`      | Sidebar with conversations (new, select, delete) |
| `useConversationHistory.ts` | localStorage persistence                         |
| `useLocalStorage.ts`        | Generic localStorage hook                        |

### Tool Visualization

| File                    | Purpose                                |
| ----------------------- | -------------------------------------- |
| `ToolCallBlock.tsx`     | Expandable tool calls (args + results) |
| `ThinkingIndicator.tsx` | Collapsible thinking/thought display   |

---

## Phase 2 Wave 1: Mobile Responsive UI

### MobileHeader Component

**File:** `app/components/MobileHeader.tsx`

**Purpose:** Hamburger menu for mobile navigation

**Features:**

- Logo + hamburger icon on mobile (< 768px)
- Full-width slide-out menu with conversation list
- Safe area support (iPhone notch/dynamic island)
- Backdrop blur when menu open
- Auto-close on conversation selection

**Usage:**

```tsx
<MobileHeader onMenuToggle={setSidebarOpen} />
```

### useMediaQuery Hook

**File:** `app/hooks/useMediaQuery.ts`

**Purpose:** Responsive breakpoint detection

**API:**

```ts
const isMobile = useMediaQuery('(max-width: 768px)');
const isTablet = useMediaQuery('(max-width: 1024px)');
```

**Breakpoints Used:**

- Mobile: < 768px (collapsible sidebar)
- Tablet: 768px - 1024px
- Desktop: > 1024px (persistent sidebar)

### Page Layout Changes

**File:** `app/page.tsx`

**Mobile-First Structure:**

```
┌─────────────────────────────────┐
│  MobileHeader (mobile only)     │  ← 56px height, safe area
├─────────────────────────────────┤
│  ┌──────────┐  ┌─────────────┐  │
│  │ Sidebar  │  │  Chat Area   │  │  ← Sidebar overlays on mobile
│  │(overlay) │  │             │  │
│  └──────────┘  └─────────────┘  │
└─────────────────────────────────┘
```

**CSS Adjustments:**

- Touch targets: minimum 44px
- Font sizes: 16px minimum (prevents zoom on iOS)
- Safe areas: `env(safe-area-inset-*)` for iPhone
- Sidebar width: 280px mobile, 320px desktop
- Z-index layering: sidebar (40), header (50), modal (100)

---

## Phase 2 Wave 1: Subagent Status Components

### AgentStatusCard

**File:** `app/components/AgentStatusCard.tsx`

**Purpose:** Individual agent status indicator

**Props:**

```ts
interface AgentStatusCardProps {
  type: 'research' | 'image' | 'code' | 'reader';
  status: 'pending' | 'running' | 'completed' | 'error';
  task: string;
  progress?: number; // 0-100
  result?: string;
  onDismiss?: () => void;
  autoDismiss?: boolean; // Default: true for completed
}
```

**Icons:**

- 🔍 @research (Brave search)
- 🖼️ @image (Gemini 2.5 Flash Image; Max: Gemini 3 Pro Image Preview)
- 💻 @code (code tasks)
- 📄 @reader (document reading)

**States:**

- **Pending:** Pulsing icon, "Waiting..."
- **Running:** Animated progress bar, live status updates
- **Completed:** Checkmark, expandable result preview
- **Error:** Red X, error message, retry button

**Auto-Dismiss:** Completed cards auto-dismiss after 5 seconds (configurable)

### AgentStatusList

**File:** `app/components/AgentStatusList.tsx`

**Purpose:** Floating panel showing all active agents

**Position:** Bottom-right corner (16px from edges)

**Features:**

- Stacks multiple AgentStatusCards vertically
- Max height: 400px with scroll
- Collapse/expand toggle
- Clear all completed button

**Usage:**

```tsx
<AgentStatusList
  agents={activeAgents}
  onAgentDismiss={(id) => removeAgent(id)}
/>
```

### useAgentStatus Hook

**File:** `app/hooks/useAgentStatus.ts`

**Purpose:** Subscribe to SSE agent events and manage agent state

**API:**

```ts
const { agents, isLoading, error } = useAgentStatus();

// agents: Array of agent states with metadata
// isLoading: Boolean for any pending agents
// error: Error message if SSE connection fails
```

**Event Handling:**

- `agent_spawn` → Add new agent to list (status: pending)
- `agent_status` → Update progress/status
- `agent_complete` → Mark complete, show result
- `agent_error` → Mark error, show retry option

**Integration with Chat:**
Hook runs automatically when chat streaming starts. No manual initialization needed.

---

## Phase 2 Wave 1: PWA Foundation

### Manifest

**File:** `public/manifest.json`

```json
{
  "name": "Daemon AI Chat",
  "short_name": "Daemon",
  "description": "AI chat with subagent orchestration",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#ffffff",
  "theme_color": "#3b82f6",
  "icons": [
    { "src": "/icons/icon-192.png", "sizes": "192x192" },
    { "src": "/icons/icon-512.png", "sizes": "512x512" }
  ]
}
```

### Icons

**Files:**

- `public/icons/icon.svg` - Source SVG (blue #3b82f6, white "D")
- `public/icons/icon-192.png` - 192x192 PNG
- `public/icons/icon-512.png` - 512x512 PNG
- `public/icons/apple-touch-icon.png` - iOS home screen

**Generation:**

```bash
# Convert SVG to PNGs
npx svg-to-png public/icons/icon.svg --width 192 --height 192 -o public/icons/icon-192.png
npx svg-to-png public/icons/icon.svg --width 512 --height 512 -o public/icons/icon-512.png
```

### Layout Metadata

**File:** `app/layout.tsx`

**Added PWA Tags:**

```tsx
<link rel="manifest" href="/manifest.json" />
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png" />
<meta name="theme-color" content="#3b82f6" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-status-bar-style" content="default" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
```

---

## Phase 2 Wave 2: Service Worker

### Serwist Configuration

**Files:** `next.config.mjs`, `app/sw.ts`, `lib/pwaCaching.ts`

`next.config.mjs` wires Serwist into production webpack builds, registers the
generated worker automatically, and leaves service workers disabled outside
production. `app/sw.ts` is the source of truth for precaching, activation, and
runtime cache policy. Generated worker bundles are ignored and recreated by the
locked production build.

**Caching Strategies:**

1. **Same-origin API Routes** (`/api/*`): NetworkOnly
   - Never caches authenticated API responses
   - Explicitly excluded from the general runtime-cache fallback
   - Removes cache names used by legacy API-caching configurations on activation

2. **Static Assets** (JS, CSS): CacheFirst
   - Serve from cache immediately
   - Fetch only on cache miss or after expiration
   - 30-day expiration

3. **Images**: StaleWhileRevalidate
   - Serve cached version immediately
   - Fetch fresh version in background
   - Next request gets updated image

4. **Fonts**: CacheFirst
   - Aggressive caching (1 year)
   - Fonts rarely change

### Generated Service Worker

**Files:** `public/sw.js` and any supporting `public/swe-worker-*.js` chunks
(generated by Serwist on build)

**Build Command:**

```bash
npm run build
# Generates ignored worker bundles in public/
# Registers service worker in browser
```

**Features:**

- Automatic service worker registration
- Precache manifest for static assets
- Network-only handling for same-origin APIs
- Runtime caching for non-API content
- Skip waiting (immediate activation)

---

## Phase 2 Wave 2: Offline Support

### OfflineIndicator

**File:** `app/components/OfflineIndicator.tsx`

**Purpose:** Banner showing offline status

**Appearance:**

- Fixed position top of screen
- Red background: "You're offline. Some features may be unavailable."
- Yellow background (slow connection): "Connection is slow..."
- Auto-hide when back online

**States:**

- `online` → Hidden
- `offline` → Red banner visible
- `slow` → Yellow banner visible

### useOnlineStatus Hook

**File:** `app/hooks/useOnlineStatus.ts`

**API:**

```ts
const { isOnline, isSlowConnection, checkConnection } = useOnlineStatus();

// isOnline: Boolean based on navigator.onLine + heartbeat
// isSlowConnection: Boolean if ping > 5000ms
// checkConnection: Manual recheck function
```

**Implementation:**

- Listens to `online`/`offline` events
- Heartbeat ping every 30s to verify actual connectivity
- Considers connection slow if ping > 5s
- Handles false positives (browser says online but no actual connection)

### RetryButton

**File:** `app/components/RetryButton.tsx`

**Purpose:** Retry failed network requests

**Usage:**

```tsx
<RetryButton
  onRetry={() => sendMessage(message)}
  error="Failed to send message"
  retryCount={2}
  maxRetries={3}
/>
```

**Features:**

- Shows error message
- Exponential backoff delay (1s, 2s, 4s)
- Progress indicator during retry
- Disabled when max retries reached

### Offline Message Queue

**File:** `app/hooks/useOfflineQueue.ts` (optional enhancement)

**Purpose:** Queue messages when offline, send when back online

**Behavior:**

1. User sends message while offline
2. Message stored in queue (localStorage)
3. UI shows "Queued - will send when online"
4. When connection restored, auto-send queued messages
5. Remove from queue on success

---

## File Structure

```
frontend/
├── app/
│   ├── page.tsx                    # Main layout with mobile sidebar
│   ├── layout.tsx                  # Root layout with PWA meta
│   ├── sw.ts                       # Serwist service-worker source
│   ├── globals.css                 # Tailwind + safe area variables
│   ├── api/
│   │   └── chat/
│   │       └── route.ts            # SSE proxy to backend
│   ├── components/
│   │   # Phase 1 Core
│   │   ├── ChatInterface.tsx
│   │   ├── MessageList.tsx
│   │   ├── StreamingText.tsx
│   │   ├── CloudLocalToggle.tsx
│   │   # Phase 1 Error Handling
│   │   ├── ErrorToast.tsx
│   │   ├── ConnectionStatus.tsx
│   │   ├── ErrorProvider.tsx
│   │   # Phase 1 Conversation
│   │   ├── ConversationList.tsx
│   │   # Phase 1 Tools
│   │   ├── ToolCallBlock.tsx
│   │   ├── ThinkingIndicator.tsx
│   │   # Phase 2 Mobile
│   │   ├── MobileHeader.tsx        # NEW
│   │   # Phase 2 Subagent
│   │   ├── AgentStatusCard.tsx     # NEW
│   │   ├── AgentStatusList.tsx     # NEW
│   │   ├── AgentStatusBadge.tsx    # NEW
│   │   # Phase 2 Offline
│   │   ├── OfflineIndicator.tsx    # NEW
│   │   ├── RetryButton.tsx         # NEW
│   │   └── ConnectionStatus.tsx    # Enhanced
│   └── hooks/
│       ├── useConversationHistory.ts
│       ├── useLocalStorage.ts
│       ├── useMediaQuery.ts        # NEW
│       ├── useAgentStatus.ts       # NEW
│       └── useOnlineStatus.ts      # NEW
├── components/ui/                  # shadcn components
├── lib/
│   ├── events.ts                   # SSE event types
│   └── pwaCaching.ts               # Runtime-cache boundary predicates
├── public/
│   ├── manifest.json               # PWA manifest
│   └── icons/                      # App icons
│       ├── icon.svg
│       ├── icon-192.png
│       ├── icon-512.png
│       └── apple-touch-icon.png
├── next.config.mjs                 # Serwist/Next.js config
└── package.json
```

---

## Event Types (lib/events.ts)

**Backend → Frontend SSE Events:**

```ts
type EventType =
  | 'text' // Regular text response
  | 'thinking' // Model reasoning
  | 'tool_call' // Tool execution start
  | 'tool_result' // Tool execution complete
  | 'agent_spawn' // Subagent created
  | 'agent_status' // Subagent progress update
  | 'agent_complete' // Subagent finished
  | 'agent_error' // Subagent failed
  | 'image_ready' // Image generation complete
  | 'pipeline_switch' // Local/Cloud toggle
  | 'error'; // General error
```

**Subagent Events:**

```ts
interface AgentSpawnEvent {
  type: 'agent_spawn';
  agent_id: string;
  agent_type: 'research' | 'image' | 'code' | 'reader';
  task: string;
  timestamp: string;
}

interface AgentStatusEvent {
  type: 'agent_status';
  agent_id: string;
  status: 'pending' | 'running' | 'completed' | 'error';
  progress?: number;
  message?: string;
  timestamp: string;
}
```

---

## Environment Variables

**Required:**

```bash
# .env.local
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
```

**Optional:**

```bash
# Features
NEXT_PUBLIC_ENABLE_VOICE=false    # Wave 3: Speech I/O
NEXT_PUBLIC_ENABLE_MEMORY=false   # Wave 4: PostgreSQL persistence
```

---

## Build & Deploy

### Development

```bash
npm run dev
# localhost:3000
# Service worker disabled in dev
```

### Production Build

```bash
npm run build
# Generates:
# - Optimized static files
# - public/sw.js (service worker)
# - optional public/swe-worker-*.js support chunks

npm run start
# localhost:3000 with full PWA
```

### PWA Testing

```bash
# Build first
npm run build

# Test service worker
# 1. Open Chrome DevTools > Application > Service Workers
# 2. Check "Update on reload" for development
# 3. Go offline (Network tab > Offline checkbox)
# 4. Refresh page - should load from cache

# Lighthouse audit
npx lighthouse http://localhost:3000 \
  --chrome-flags="--headless" \
  --output=html \
  --output-path=./lighthouse-report.html
```

---

## Browser Support

**Minimum:**

- Chrome 90+
- Firefox 88+
- Safari 14+ (iOS 14+)
- Edge 90+

**PWA Requirements:**

- HTTPS (localhost exempt for development)
- Service Worker support
- Manifest support

**Mobile:**

- iOS 14.5+ (PWA install, limited push notification support)
- Android 10+ (Full PWA support)

---

## Known Limitations

1. **iOS PWA:** No background sync, limited push notification support
2. **Safari:** No Web Push API (use polling fallback)
3. **Storage:** localStorage limited to ~5MB (Phase 4 PostgreSQL solves this)
4. **Voice:** Wave 3 not implemented (Web Speech API support varies)

---

## Next Steps (Phase 2 Waves 3-4)

**Wave 3: Voice I/O**

- [ ] `useSpeechRecognition.ts` hook
- [ ] `useTextToSpeech.ts` hook
- [ ] `VoiceButton.tsx` component
- [ ] Voice commands: "new chat", "send", "stop"

**Wave 4: Memory Layer**

- [ ] PostgreSQL schema for conversations
- [ ] pgvector for semantic search
- [ ] `useMemory.ts` hook (replaces localStorage)
- [ ] Vector-based context retrieval

---

## Troubleshooting

**Build fails with "Cannot find package '@serwist/next'":**

```bash
npm ci
```

**Service worker not registering:**

- Check `next.config.mjs` enables Serwist for production
- Verify `public/sw.js` exists after build
- Check DevTools > Application > Service Workers

**Mobile sidebar not working:**

- Verify `useMediaQuery` hook working
- Check CSS breakpoint at 768px
- Ensure `MobileHeader` mounted in page.tsx

**Agent status not showing:**

- Check SSE connection in Network tab
- Verify backend sending `agent_spawn` events
- Check `useAgentStatus` hook subscribed to events

**Offline mode not caching:**

- Verify service worker registered
- Check runtime caching rules in `app/sw.ts`
- Test in DevTools > Application > Cache Storage

---

## API Reference

### Chat Endpoint

**POST** `/api/chat`

**Request:**

```json
{
  "messages": [{ "role": "user", "content": "Hello" }],
  "conversation_id": "uuid"
}
```

**Response:** SSE stream with events (text, thinking, tool_call, etc.)

### Agent Events

**Spawn Agent:**

```json
{
  "type": "agent_spawn",
  "agent_id": "research_abc123",
  "agent_type": "research",
  "task": "Research quantum computing"
}
```

**Status Update:**

```json
{
  "type": "agent_status",
  "agent_id": "research_abc123",
  "status": "running",
  "progress": 45,
  "message": "Searching Brave..."
}
```

**Complete:**

```json
{
  "type": "agent_complete",
  "agent_id": "research_abc123",
  "result": "## Quantum Computing...",
  "duration_ms": 3200
}
```

---

_Generated: 2026-02-08_
_Frontend Version: Phase 2 Wave 2_
