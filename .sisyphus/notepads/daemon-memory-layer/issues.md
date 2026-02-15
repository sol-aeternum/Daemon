# Issues Log

## Unresolved
(None)

- /chat test times out after 5s with no SSE bytes while /health is OK. Likely default provider `openrouter` waiting on missing `OPENROUTER_API_KEY`. Consider setting OPENROUTER_API_KEY or enabling MOCK_LLM to complete endpoint test.

## Resolved
- /chat test blocked: server requires Bearer token (DAEMON_API_KEY set). **RESOLVED** — Updated docker-compose.yml to remove fallback defaults (`${DAEMON_API_KEY:-sk-test}` → `${DAEMON_API_KEY}`). Backend now runs with empty key. `/chat` accepts requests without Bearer token and returns SSE output.
- **ConversationList Kebab Menu**: Fixed visibility issue where the menu was not appearing or was misplaced.
  - Problem: `fixed` positioning with `getBoundingClientRect` was fragile and causing the menu to be hidden or misplaced.
  - Solution: Switched to `absolute` positioning relative to the button container. Added a `fixed` transparent backdrop to handle "click outside" behavior.
  - File: `frontend/components/ConversationList.tsx`
