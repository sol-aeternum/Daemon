import type { ChatEvent } from './events';
import {
  dedupeSources,
  extractSourcesFromResult,
  parseToolResultPayload,
  isAdvisorScoped,
  MAX_UNIQUE_SOURCES,
  type ToolSource,
} from './toolActivity';

/**
 * Build the citation-source list for one assistant message from its event
 * stream. Sources come only from returned tool results (success shape);
 * guessed/failed-call URLs never qualify. Intentionally independent from
 * the debug-tool visibility preference so inline citations keep working
 * when tool cards are hidden.
 */
export function buildMessageCitationSources(
  events: ChatEvent[] | undefined,
): ToolSource[] {
  if (!events || events.length === 0) return [];
  const flat: ToolSource[] = [];
  for (const event of events) {
    if (
      event.type !== 'tool_result' ||
      event.result == null ||
      isAdvisorScoped(event)
    )
      continue;
    if (event.name !== 'web_search' && event.name !== 'web_fetch') continue;
    // Skip advisor-internal results: only top-level response sources count.
    const payload = parseToolResultPayload(event.result);
    flat.push(...extractSourcesFromResult(payload));
    const bounded = dedupeSources(flat);
    flat.splice(0, flat.length, ...bounded);
    if (flat.length >= MAX_UNIQUE_SOURCES) break;
  }
  return dedupeSources(flat);
}
