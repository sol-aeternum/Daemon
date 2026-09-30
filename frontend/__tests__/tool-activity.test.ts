import { describe, expect, it } from 'vitest';
import type { ChatEvent } from '../lib/events';
import { buildMessageCitationSources } from '../lib/messageSources';
import {
  buildToolActivitySummary,
  pairToolExecutions,
  canonicalUrlKey,
} from '../lib/toolActivity';

describe('tool activity event integrity', () => {
  it('preserves original call order, pairs IDs first, and drops orphan/advisor results', () => {
    const events: ChatEvent[] = [
      {
        type: 'tool_call',
        name: 'web_search',
        arguments: {},
        tool_call_id: 'a',
      },
      {
        type: 'tool_call',
        name: 'spawn_agent',
        arguments: {},
        tool_call_id: 'b',
      },
      {
        type: 'tool_call',
        name: 'web_search',
        arguments: {},
        tool_call_id: 'c',
      },
      {
        type: 'tool_result',
        name: 'web_search',
        result: 'orphan',
        tool_call_id: 'unknown',
      },
      {
        type: 'tool_result',
        name: 'web_search',
        result: 'nested',
        tool_call_id: 'a',
        advisor_id: 'nested',
      },
      {
        type: 'tool_result',
        name: 'web_search',
        result: 'third',
        tool_call_id: 'c',
      },
      {
        type: 'tool_result',
        name: 'web_search',
        result: 'first',
        tool_call_id: 'a',
      },
    ];
    const paired = pairToolExecutions(events);
    expect(
      paired.map((e) => e.call.type === 'tool_call' && e.call.tool_call_id),
    ).toEqual(['a', 'b', 'c']);
    expect(
      paired.map((e) =>
        e.result?.type === 'tool_result' ? e.result.result : null,
      ),
    ).toEqual(['first', null, 'third']);
  });

  it('preserves the legacy most-recent-unpaired name fallback without overriding explicit IDs', () => {
    const events: ChatEvent[] = [
      { type: 'tool_call', name: 'get_time', arguments: {} },
      { type: 'tool_call', name: 'get_time', arguments: {} },
      { type: 'tool_result', name: 'get_time', result: 'late' },
      { type: 'tool_result', name: 'get_time', result: 'early' },
    ];
    expect(
      pairToolExecutions(events).map(
        (e) => e.result?.type === 'tool_result' && e.result.result,
      ),
    ).toEqual(['early', 'late']);
  });

  it('counts distinct returned pages, searches, errors and pending actions honestly', () => {
    const events: ChatEvent[] = [];
    const done = (
      id: string,
      name: string,
      args: Record<string, unknown>,
      result: unknown,
    ) =>
      events.push(
        { type: 'tool_call', name, arguments: args, tool_call_id: id },
        { type: 'tool_result', name, result, tool_call_id: id },
      );
    done('s', 'web_search', {}, { results: [] });
    done(
      'p1',
      'web_fetch',
      {},
      { url: 'https://source.example/page', content: 'first section' },
    );
    done(
      'p2',
      'web_fetch',
      { start_char: 10 },
      { url: 'https://source.example/page#section', content: 'next section' },
    );
    done('list', 'web_fetch', { action: 'list' }, { sources: [] });
    done('memory', 'memory_read', {}, { content: 'memory' });
    done('error', 'web_fetch', {}, { data: { error: 'denied' } });
    events.push({ type: 'tool_call', name: 'web_search', arguments: {} });
    expect(buildToolActivitySummary(pairToolExecutions(events))).toMatchObject({
      searchCount: 1,
      pageCount: 1,
      otherCount: 2,
      runningCount: 1,
      errorCount: 1,
      segments: ['Searched 1 time', 'Read 1 page', '+2 other tools'],
    });
  });
});

describe('source provenance and bounds', () => {
  it('rejects credentials/unsafe URLs and excludes advisor and unrelated artifact URLs', () => {
    const source = (
      name: string,
      result: unknown,
      advisor_id?: string,
    ): ChatEvent => ({ type: 'tool_result', name, result, advisor_id });
    const events = [
      source('web_search', {
        results: [
          { url: 'https://user:pass@example.com/a' },
          { url: 'javascript:alert(1)' },
          { url: 'https://example.com/Case?q=A' },
          { url: 'https://example.com/case?q=A' },
          { url: 'https://example.com/Case?q=a' },
          { url: 'https://EXAMPLE.COM/Case?q=A#hash' },
        ],
      }),
      source('web_fetch', { url: 'https://advisor.example/' }, 'a1'),
      source('spawn_agent', { url: 'https://media.example/video' }),
      source('web_fetch', { success: false, url: 'https://failed.example/' }),
    ];
    expect(buildMessageCitationSources(events).map((s) => s.url)).toEqual([
      'https://example.com/Case?q=A',
      'https://example.com/case?q=A',
      'https://example.com/Case?q=a',
    ]);
    expect(
      canonicalUrlKey('https://user:pass@example.com/Case?q=A'),
    ).toBeNull();
    expect(canonicalUrlKey('https:example.com')).toBeNull();
  });

  it('bounds large returned source lists to 100 unique safe URLs', () => {
    const events: ChatEvent[] = [
      {
        type: 'tool_result',
        name: 'web_search',
        result: {
          results: Array.from({ length: 500 }, (_, i) => ({
            url: `https://source.example/${i}`,
          })),
        },
      },
    ];
    expect(buildMessageCitationSources(events)).toHaveLength(100);
  });
});
