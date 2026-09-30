import type { ChatEvent } from './events';

/**
 * Shared logic for the compact grouped tool activity UI:
 * pairing, classification, safe parsing, source extraction, and summaries.
 *
 * Design rules (user-approved "Grouped activity" direction):
 * - One collapsed activity row per assistant response.
 * - Count searches/pages/issues honestly; unique source URLs are counted
 *   separately from pages actually read.
 * - Sources come only from returned tool results (never from guessed or
 *   failed-call argument URLs); only absolute http(s) URLs qualify.
 * - Malformed results never crash; opaque text stays escaped (no raw HTML).
 */

export interface ToolExecution {
  call: ChatEvent;
  result?: ChatEvent;
}

export type ToolActivityKind = 'search' | 'page' | 'other';

export interface ToolSource {
  url: string;
  /** Display host, e.g. `example.com` (no favicons, text-only pill). */
  domain: string;
  title?: string;
}

export interface ToolActivitySummary {
  searchCount: number;
  pageCount: number;
  otherCount: number;
  runningCount: number;
  errorCount: number;
  /** Visible segments joined with ' · ', e.g. "Searched 3 times · Read 2 pages". */
  segments: string[];
  /** Accessible name including counts so SR users get the full picture. */
  accessibleSummary: string;
}

export function classifyToolName(name: string): ToolActivityKind {
  if (name === 'web_search') return 'search';
  if (name === 'web_fetch') return 'page';
  return 'other';
}

export function isAdvisorScoped(event: ChatEvent): boolean {
  return 'advisor_id' in event && Boolean(event.advisor_id);
}

/**
 * Pair tool_result events to their tool_call using tool_call_id first
 * (repeat same-name calls stay distinct), falling back to latest unpaired
 * call with the same name — the same fallback the previous ToolCallLog used,
 * so persisted history keeps pairing. Results with no matching call are
 * dropped from the log rather than rendered as fake executions.
 */
export function pairToolExecutions(events: ChatEvent[]): ToolExecution[] {
  const executions: ToolExecution[] = [];

  for (const event of events) {
    if (isAdvisorScoped(event)) continue;
    if (event.type === 'tool_call') {
      executions.push({ call: event });
      continue;
    }
    if (event.type !== 'tool_result') continue;

    const resultEvent = event as ChatEvent & {
      type: 'tool_result';
      name: string;
    };
    for (let i = executions.length - 1; i >= 0; i -= 1) {
      if (executions[i].result) continue;
      const call = executions[i].call;
      if (call.type !== 'tool_call') continue;
      const matches = resultEvent.tool_call_id
        ? call.tool_call_id === resultEvent.tool_call_id
        : call.name === resultEvent.name;
      if (matches) {
        executions[i].result = event;
        break;
      }
    }
  }

  return executions;
}

/** Parser accepts a JSON string or a plain object; everything else is null. */
export function parseToolResultPayload(
  raw: unknown,
): Record<string, unknown> | null {
  let candidate: unknown = raw;
  if (typeof raw === 'string') {
    try {
      candidate = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (
    typeof candidate !== 'object' ||
    candidate === null ||
    Array.isArray(candidate)
  ) {
    return null;
  }
  return candidate as Record<string, unknown>;
}

/** Failures: `{ error: string }`, nested `data.error`, or `success: false`. */
export function extractToolFailure(
  payload: Record<string, unknown> | null,
): string | null {
  if (!payload) return null;
  if (typeof payload.error === 'string' && payload.error.length > 0) {
    return payload.error;
  }
  if (payload.error)
    return 'Tool call failed. Continuing with best available information.';
  if (typeof payload.success === 'boolean' && !payload.success) {
    return 'Tool call failed. Continuing with best available information.';
  }
  const nested = payload.data;
  if (
    typeof nested === 'object' &&
    nested !== null &&
    typeof (nested as Record<string, unknown>).error === 'string'
  ) {
    const message = (nested as Record<string, unknown>).error as string;
    if (message.length > 0) return message;
  }
  if (
    typeof nested === 'object' &&
    nested !== null &&
    (nested as Record<string, unknown>).error
  ) {
    return 'Tool call failed. Continuing with best available information.';
  }
  return null;
}

/**
 * Sources only from returned results. Fetch results carry `url`/`final_url`
 * (+ optional `title`); search results carry `results: [{title, url}]`.
 * A failed call's argument URL is not a returned source and never appears.
 */
export function extractSourcesFromResult(
  payload: Record<string, unknown> | null,
): ToolSource[] {
  if (!payload || extractToolFailure(payload)) return [];

  const sources: ToolSource[] = [];
  const seen = new Set<string>();
  const fromUrlField = (value: unknown, title: unknown) => {
    if (typeof value !== 'string') return;
    const url = normalizeSafeHttpUrl(value);
    if (!url || seen.has(url) || sources.length >= MAX_UNIQUE_SOURCES) return;
    seen.add(url);
    sources.push({
      url,
      domain: domainFromUrl(url),
      title: typeof title === 'string' ? title : undefined,
    });
  };

  const listedResults = payload.results;
  if (Array.isArray(listedResults)) {
    for (const hit of listedResults.slice(0, 1000)) {
      if (typeof hit !== 'object' || hit === null) continue;
      const hitRecord = hit as Record<string, unknown>;
      fromUrlField(hitRecord.url, hitRecord.title);
      if (sources.length >= MAX_UNIQUE_SOURCES) break;
    }
  }

  const data =
    typeof payload.data === 'object' && payload.data !== null
      ? (payload.data as Record<string, unknown>)
      : {};
  const finalUrl = payload.final_url ?? data.final_url;
  if (typeof finalUrl === 'string') {
    fromUrlField(finalUrl, payload.title ?? data.title);
  }
  // Both requested and redirected URLs were actually returned. Preserve
  // each for citation matching; neither is inferred from call arguments.
  fromUrlField(payload.url ?? data.url, payload.title ?? data.title);

  return sources;
}

/** Absolute http(s) URLs only; rejects protocols, credentials, opaque values. */
export function normalizeSafeHttpUrl(value: string): string | null {
  if (value.length > 8192 || !/^https?:\/\//i.test(value)) return null;
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
  if (parsed.username || parsed.password) return null;
  // Strip hash so same-page variants count as one source.
  parsed.hash = '';
  return parsed.toString();
}

export function domainFromUrl(url: string): string {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return url;
  }
}

/**
 * Normalize a URL for citation matching without conflating case-sensitive
 * paths/queries: scheme + host are case-insensitive; path and query are
 * compared verbatim. Hash is ignored so same-page anchor links match.
 */
export function canonicalUrlKey(value: string): string | null {
  return normalizeSafeHttpUrl(value);
}

export const MAX_UNIQUE_SOURCES = 100;

/**
 * Deduplicate sources by canonical key, first occurrence wins. Bounded to
 * MAX_UNIQUE_SOURCES unique URLs; very huge result sets never blow up UI.
 */
export function dedupeSources(sources: ToolSource[]): ToolSource[] {
  const seen = new Set<string>();
  const out: ToolSource[] = [];
  for (const source of sources) {
    const key = canonicalUrlKey(source.url);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(source);
    if (out.length >= MAX_UNIQUE_SOURCES) break;
  }
  return out;
}

/** Sources shown inline by default; "+N more" reveals the rest. */
export const DEFAULT_PILL_PREVIEW_COUNT = 3;

/** Aggregated honest counts over executions (searches reported per call). */
export function buildToolActivitySummary(
  executions: ToolExecution[],
): ToolActivitySummary {
  let searchCount = 0;
  let pageCount = 0;
  let otherCount = 0;
  let runningCount = 0;
  let errorCount = 0;
  const readPages = new Set<string>();

  for (const execution of executions) {
    const call = execution.call;
    if (call.type !== 'tool_call') continue;

    let isError = false;
    let isRunning = false;
    if (!execution.result) {
      isRunning = true;
    } else if (execution.result.type === 'tool_result') {
      const failure = extractToolFailure(
        parseToolResultPayload(execution.result.result),
      );
      isError = Boolean(failure);
    }

    if (isRunning) {
      runningCount += 1;
    } else if (isError) {
      errorCount += 1;
    } else {
      const kind = classifyToolName(call.name);
      if (kind === 'search') searchCount += 1;
      else if (
        kind === 'page' &&
        (call.arguments.action ?? 'read') === 'read'
      ) {
        const payload =
          execution.result?.type === 'tool_result'
            ? parseToolResultPayload(execution.result.result)
            : null;
        const source = extractSourcesFromResult(payload)[0];
        if (
          source &&
          ((typeof payload?.content === 'string' &&
            payload.content.length > 0) ||
            (typeof payload?.content_length === 'number' &&
              payload.content_length > 0))
        ) {
          readPages.add(source.url);
        } else {
          otherCount += 1;
        }
      } else otherCount += 1;
    }
  }

  pageCount = readPages.size;
  return {
    searchCount,
    pageCount,
    otherCount,
    runningCount,
    errorCount,
    ...summarizeActivity(
      searchCount,
      pageCount,
      otherCount,
      runningCount,
      errorCount,
    ),
  };
}

function summarizeActivity(
  searchCount: number,
  pageCount: number,
  otherCount: number,
  runningCount: number,
  errorCount: number,
): Pick<ToolActivitySummary, 'segments' | 'accessibleSummary'> {
  const segments: string[] = [];
  if (searchCount === 1) segments.push('Searched 1 time');
  else if (searchCount > 1) segments.push(`Searched ${searchCount} times`);
  if (pageCount >= 1)
    segments.push(`Read ${pageCount} page${pageCount === 1 ? '' : 's'}`);
  if (otherCount >= 1)
    segments.push(`+${otherCount} other tool${otherCount === 1 ? '' : 's'}`);

  const accessibleParts = [...segments];
  if (runningCount > 0) {
    accessibleParts.push(`${runningCount} in progress`);
  }
  if (errorCount > 0) {
    accessibleParts.push(`${errorCount} issue${errorCount === 1 ? '' : 's'}`);
  }

  return {
    segments,
    accessibleSummary:
      accessibleParts.length > 0
        ? accessibleParts.join(', ')
        : 'no tool activity yet',
  };
}
