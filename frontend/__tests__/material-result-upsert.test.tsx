import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
} from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ToolCallLog } from '../components/ToolCallBlock';
import { POST } from '../app/api/chat/route';
import { useEventArchive } from '../hooks/useEventArchive';
import { getConversationOutputs } from '../lib/conversationDetails';
import { normalizeChatEvents, type ChatEvent } from '../lib/events';
import {
  buildToolActivitySummary,
  pairToolExecutions,
} from '../lib/toolActivity';

const summary = {
  type: 'tool_result',
  name: 'generate_document',
  request_id: 'request',
  task_id: '11111111-1111-4111-8111-111111111111',
  content_generation: 1,
  operation_id: '22222222-2222-4222-8222-222222222222',
  outcome: 'succeeded',
  payload_state: 'summary',
  result: { success: true, outcome: 'succeeded' },
} as ChatEvent;
const full = {
  ...summary,
  payload_state: 'full',
  result: { success: true, file_url: '/generated-files/fixture.csv' },
} as ChatEvent;
const call: ChatEvent = {
  type: 'tool_call',
  name: 'generate_document',
  request_id: 'request',
  arguments: {},
};

describe('material operation live/archive projection', () => {
  it('enriches the same operation across observer requests without duplicate results', () => {
    const reattached = { ...full, request_id: 'reattached' } as ChatEvent;
    const { result, rerender } = renderHook(
      ({ data }) => useEventArchive({ data, isLoading: true }),
      { initialProps: { data: [call, summary] } },
    );
    act(() => result.current.archiveCurrentEvents('same-message'));
    rerender({ data: [call, summary, reattached] });
    expect(
      result.current.events.filter((e) => e.type === 'tool_result'),
    ).toEqual([reattached]);
    const saved = result.current.getEventsForMessage('same-message', false);
    expect(saved.filter((e) => e.type === 'tool_result')).toEqual([full]);
    expect(getConversationOutputs(saved)).toHaveLength(1);
    rerender({
      data: [
        call,
        summary,
        reattached,
        { ...summary, request_id: 'reattached' },
      ],
    });
    expect(
      result.current.events.filter((e) => e.type === 'tool_result'),
    ).toEqual([reattached]);
  });
  it('upserts summary/full/duplicates before every activity consumer and archives enrichment', () => {
    const { result, rerender } = renderHook(
      ({ data }) => useEventArchive({ data, isLoading: true }),
      { initialProps: { data: [call, summary] } },
    );
    expect(result.current.events).toHaveLength(2);
    act(() => result.current.archiveCurrentEvents('early-message'));
    rerender({ data: [call, summary, full, full, summary] });
    expect(result.current.events).toHaveLength(2);
    expect(result.current.events[1]).toEqual(full);
    expect(result.current.getEventsForMessage('early-message', false)).toEqual([
      call,
      full,
    ]);
    expect(pairToolExecutions(result.current.events)).toHaveLength(1);
    expect(getConversationOutputs(result.current.events)).toEqual([
      {
        fileUrl: '/generated-files/fixture.csv',
        filename: 'fixture.csv',
        fileType: 'csv',
        fileSize: undefined,
      },
    ]);
    expect(
      buildToolActivitySummary(pairToolExecutions(result.current.events))
        .otherCount,
    ).toBe(1);
    act(() => result.current.archiveCurrentEvents('message'));
    expect(result.current.getEventsForMessage('message', false)).toEqual([
      call,
      full,
    ]);
    rerender({ data: [summary] });
    expect(result.current.getEventsForMessage('message', false)).toEqual([
      call,
      full,
    ]);
  });

  it('renders one enriched action and never regresses on older summary updates', () => {
    const events = normalizeChatEvents([call, summary, full, summary, full]);
    render(<ToolCallLog events={events} />);
    expect(screen.getByRole('button', { name: /tool/i }).textContent).toContain(
      '+1 other tool',
    );
    fireEvent.click(screen.getByRole('button', { name: /tool/i }));
    expect(
      screen.getAllByRole('button', { name: 'generate_document' }),
    ).toHaveLength(1);
    expect(events[1]).toEqual(full);
  });

  it('bridge preserves approved identity/state metadata on the existing event', async () => {
    const frames = [summary, full]
      .map(
        (event) =>
          `event: tool_result\ndata: ${JSON.stringify({ type: 'tool_result', request_id: 'request', data: event })}\n\n`,
      )
      .join('');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(frames, {
          headers: { 'Content-Type': 'text/event-stream' },
        }),
      ),
    );
    try {
      const response = await POST(
        new Request('http://test/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            messages: [{ role: 'user', content: 'fixture' }],
          }),
        }),
      );
      const events = (await response.text())
        .split('\n')
        .filter((line) => line.startsWith('data: ') && !line.includes('[DONE]'))
        .map((line) => JSON.parse(line.slice(6)))
        .filter((chunk) => chunk.type === 'data-event')
        .map((chunk) => chunk.data);
      expect(events.filter((e) => e.type === 'tool_result')).toEqual([
        summary,
        full,
      ]);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('same-generation reset retains full; new generation isolates results; conflicts cannot replace it', () => {
    const reset: Extract<ChatEvent, { type: 'task_reset' }> = {
      type: 'task_reset',
      task_id: summary.type === 'tool_result' ? summary.task_id : '',
      content_generation: 1,
      request_id: 'request',
    };
    const conflict = { ...full, name: 'other_tool' } as ChatEvent;
    expect(normalizeChatEvents([full, reset, summary, conflict])).toEqual([
      full,
      reset,
    ]);
    expect(
      normalizeChatEvents([full, { ...reset, content_generation: 2 }]),
    ).toEqual([{ ...reset, content_generation: 2 }]);
  });

  it('does not coalesce unrelated operations, generations, tasks or legacy no-ID results', () => {
    const otherRequest = {
      ...full,
      request_id: 'other',
      task_id: '44444444-4444-4444-8444-444444444444',
    } as ChatEvent;
    const otherGeneration = { ...full, content_generation: 2 } as ChatEvent;
    const otherTask = {
      ...full,
      task_id: '33333333-3333-4333-8333-333333333333',
    } as ChatEvent;
    const legacy = {
      type: 'tool_result',
      name: 'generate_document',
      result: {},
    } as ChatEvent;
    const { result } = renderHook(() =>
      useEventArchive({
        data: [full, otherRequest, otherGeneration, otherTask, legacy, legacy],
        isLoading: true,
      }),
    );
    expect(result.current.events).toHaveLength(5); // old generation removed only within its scope
    expect(result.current.events).toContainEqual(otherRequest);
    expect(result.current.events).toContainEqual(otherGeneration);
    expect(result.current.events).toContainEqual(otherTask);
  });
});
