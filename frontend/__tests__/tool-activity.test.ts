import { describe, expect, it } from 'vitest';
import type { ChatEvent } from '../lib/events';
import { buildMessageCitationSources } from '../lib/messageSources';
import {
  buildToolActivitySummary,
  pairToolExecutions,
  canonicalUrlKey,
  toolResultOutcome,
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

describe('#477 bounded tool outcomes', () => {
  const resultEvent = (
    payload: unknown,
    outcome?: 'succeeded' | 'failed' | 'unknown',
  ): ChatEvent => ({
    type: 'tool_result',
    name: 'web_search',
    result: payload,
    ...(outcome ? { outcome } : {}),
  });

  it('reports running for absent results and the recorded bounded outcome when present', () => {
    expect(toolResultOutcome(undefined)).toBe('running');
    expect(toolResultOutcome(resultEvent('anything', 'succeeded'))).toBe(
      'succeeded',
    );
    expect(toolResultOutcome(resultEvent('oops', 'failed'))).toBe('failed');
    // An explicit unknown is honest despite a result body being present.
    expect(toolResultOutcome(resultEvent('body', 'unknown'))).toBe('unknown');
    // A payload-recorded outcome wins when the field is missing up top.
    expect(
      toolResultOutcome(resultEvent(JSON.stringify({ outcome: 'failed' }))),
    ).toBe('failed');
  });

  it('stays truthful for legacy rows without an outcome field', () => {
    // Recorded failure evidence reads as failed.
    expect(toolResultOutcome(resultEvent({ error: 'denied' }))).toBe('failed');
    expect(toolResultOutcome(resultEvent({ success: false, data: {} }))).toBe(
      'failed',
    );
    // A replay that lost its recorded result never reads as success.
    expect(toolResultOutcome(resultEvent(''))).toBe('unknown');
    expect(toolResultOutcome(resultEvent('   '))).toBe('unknown');
    expect(toolResultOutcome(resultEvent(null))).toBe('unknown');
    // Anything with recorded content did succeed.
    expect(toolResultOutcome(resultEvent('raw text'))).toBe('succeeded');
    expect(toolResultOutcome(resultEvent({ ok: true }))).toBe('succeeded');
    // A non tool_result event is unknown, not a success.
    expect(
      toolResultOutcome({ type: 'text', content: 'hi' } as ChatEvent),
    ).toBe('unknown');
  });

  it('counts unknown legacy replays as neutral, never as successes or failures', () => {
    const events: ChatEvent[] = [];
    for (const [id, payload] of [
      ['a', ''],
      ['b', '   '],
      ['c', JSON.stringify({ lost: true, outcome: 'unknown' })],
    ] as const) {
      events.push(
        {
          type: 'tool_call',
          name: 'web_search',
          arguments: {},
          tool_call_id: id,
        },
        {
          type: 'tool_result',
          name: 'web_search',
          result: payload,
          tool_call_id: id,
          ...(payload.includes('unknown')
            ? { outcome: 'unknown' as const }
            : {}),
        },
      );
    }
    const summary = buildToolActivitySummary(pairToolExecutions(events));
    expect(summary).toMatchObject({
      searchCount: 0,
      pageCount: 0,
      otherCount: 0,
      runningCount: 0,
      errorCount: 0,
      unknownCount: 3,
      segments: [],
      accessibleSummary: '3 not recorded',
    });
  });

  it('summarises mixed bounded outcomes with each part still visible', () => {
    const events: ChatEvent[] = [
      {
        type: 'tool_call',
        name: 'web_search',
        arguments: {},
        tool_call_id: 'ok',
      },
      {
        type: 'tool_result',
        name: 'web_search',
        result: JSON.stringify({
          results: [{ url: 'https://source.example/one' }],
        }),
        tool_call_id: 'ok',
        outcome: 'succeeded',
      },
      {
        type: 'tool_call',
        name: 'web_fetch',
        arguments: {},
        tool_call_id: 'bad',
      },
      {
        type: 'tool_result',
        name: 'web_fetch',
        result: 'lost',
        tool_call_id: 'bad',
        outcome: 'failed',
      },
      { type: 'tool_call', name: 'web_search', arguments: {} }, // still running
      { type: 'tool_call', name: 'get_time', arguments: {}, tool_call_id: 'u' },
      {
        type: 'tool_result',
        name: 'get_time',
        result: '',
        tool_call_id: 'u',
        outcome: 'unknown',
      },
    ];
    const summary = buildToolActivitySummary(pairToolExecutions(events));
    expect(summary).toMatchObject({
      searchCount: 1,
      pageCount: 0,
      otherCount: 0,
      runningCount: 1,
      errorCount: 1,
      unknownCount: 1,
      accessibleSummary:
        'Searched 1 time, 1 in progress, 1 not recorded, 1 issue',
    });
  });
});
