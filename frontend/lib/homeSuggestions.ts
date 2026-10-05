/**
 * One contextual suggestion as the home screen renders it. `summary` is the
 * compact row text; `prompt` is the exact detail shown on hover/focus and the
 * text submitted when the row is activated. Both are plaintext, never HTML/markup.
 */
export interface HomeSuggestion {
  id: string;
  summary: string;
  prompt: string;
  source: { conversationId: string; title: string };
  expiresAt: string;
}

/** Shape the response parser accepts; unknown values are unusable. */
export type HomeSuggestionsServerStatus =
  | 'disabled'
  | 'ready'
  | 'empty'
  | 'generating'
  | 'expired'
  | 'unavailable'
  | 'error';

/** Response shaping for POST /home-suggestions/refresh. */
export type HomeSuggestionsRefreshStatus =
  | 'queued'
  | 'unchanged'
  | 'disabled'
  | 'unavailable';

/** Response shaping for PATCH /users/me/settings enabling the feature. */
export interface HomeSuggestionsEnableResult {
  ok: boolean;
  /** True when the PATCH itself could not be delivered; the flag is unknown. */
  networkError?: boolean;
}

/** View-facing rendering states, all truthful; never a fake suggestion. */
export type HomeSuggestionsViewStatus =
  | 'unloaded'
  | 'loading'
  | 'ready'
  | 'generating'
  | 'empty'
  | 'new-user'
  | 'expired'
  | 'unavailable'
  | 'error'
  | 'disabled'
  | 'dismissed';

export interface HomeSuggestionsViewState {
  status: HomeSuggestionsViewStatus;
  suggestions: HomeSuggestion[];
  /** Server-supplied safe reason/message, or a client phrased note. */
  message: string | null;
}

/**
 * A suggestion is trusted only after every field passes shape and length
 * checks. A malformed or oversized field silently drops that row; the list
 * itself must still be an array. The prompt is never treated as markup.
 */
export function parseHomeSuggestion(row: unknown): HomeSuggestion | null {
  if (!row || typeof row !== 'object') return null;
  const record = row as Record<string, unknown>;
  const source =
    record.source && typeof record.source === 'object'
      ? (record.source as Record<string, unknown>)
      : null;
  const id = typeof record.id === 'string' ? record.id : '';
  const summary =
    typeof record.summary === 'string' ? record.summary.trim() : '';
  const prompt = typeof record.prompt === 'string' ? record.prompt : '';
  const conversationId =
    typeof source?.conversation_id === 'string' ? source.conversation_id : '';
  const title = typeof source?.title === 'string' ? source.title.trim() : '';
  const expiresAt =
    typeof record.expires_at === 'string' ? record.expires_at : '';
  const expired = new Date(expiresAt);
  if (
    !id ||
    !summary ||
    !prompt ||
    !conversationId ||
    !title ||
    !expiresAt ||
    Number.isNaN(expired.getTime())
  ) {
    return null;
  }
  if (
    id.length > 256 ||
    summary.length > 600 ||
    prompt.length > 20000 ||
    conversationId.length > 128 ||
    title.length > 400
  ) {
    return null;
  }
  return {
    id,
    summary,
    prompt,
    source: { conversationId, title },
    expiresAt,
  };
}

const KNOWN_SERVER_STATUSES: ReadonlySet<string> = new Set([
  'disabled',
  'ready',
  'empty',
  'generating',
  'expired',
  'unavailable',
  'error',
]);

/**
 * Parses GET /home-suggestions. A body that is not an object at all, a
 * non-boolean `enabled`, a missing or unknown `status`, or a non-array
 * `suggestions` is rejected as a whole (fail closed, never partially shown).
 * Valid but extra rows beyond the three-row cap are ignored, never shown.
 */
export function parseHomeSuggestionsBody(body: unknown): {
  enabled: boolean;
  status: HomeSuggestionsServerStatus;
  suggestions: HomeSuggestion[];
  message: string | null;
} | null {
  if (!body || typeof body !== 'object') return null;
  const record = body as Record<string, unknown>;
  if (typeof record.enabled !== 'boolean') return null;
  if (
    typeof record.status !== 'string' ||
    !KNOWN_SERVER_STATUSES.has(record.status)
  ) {
    return null;
  }
  if (!Array.isArray(record.suggestions)) return null;
  const suggestions: HomeSuggestion[] = [];
  for (const row of record.suggestions) {
    const suggestion = parseHomeSuggestion(row);
    if (suggestion) suggestions.push(suggestion);
    if (suggestions.length >= 3) break;
  }
  return {
    enabled: record.enabled,
    status: record.status as HomeSuggestionsServerStatus,
    suggestions,
    message:
      typeof record.message === 'string' && record.message
        ? record.message
        : null,
  };
}

/** Same parsing for POST /home-suggestions/refresh outcomes. */
export function parseHomeSuggestionsRefreshBody(body: unknown): {
  status: HomeSuggestionsRefreshStatus;
  message: string | null;
} | null {
  if (!body || typeof body !== 'object') return null;
  const record = body as Record<string, unknown>;
  const status = record.status;
  if (
    typeof status !== 'string' ||
    !['queued', 'unchanged', 'disabled', 'unavailable'].includes(status)
  ) {
    return null;
  }
  return {
    status: status as HomeSuggestionsRefreshStatus,
    message:
      typeof record.message === 'string' && record.message
        ? record.message
        : null,
  };
}

/** True strictly when the payload's hour window has passed at `now`. */
export function isHomeSuggestionExpired(
  suggestion: HomeSuggestion,
  now: Date = new Date(),
): boolean {
  const expires = new Date(suggestion.expiresAt);
  return Number.isNaN(expires.getTime()) || expires.getTime() <= now.getTime();
}

export function dropExpiredSuggestions(
  suggestions: HomeSuggestion[],
  now: Date = new Date(),
): HomeSuggestion[] {
  return suggestions.filter((s) => !isHomeSuggestionExpired(s, now));
}

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_URL ||
  (process.env.NODE_ENV === 'development' ? 'http://localhost:8000' : '');

export const HOME_SUGGESTIONS_ENDPOINTS = {
  list: `${apiBaseUrl}/home-suggestions`,
  refresh: `${apiBaseUrl}/home-suggestions/refresh`,
  settings: `${apiBaseUrl}/users/me/settings`,
} as const;
