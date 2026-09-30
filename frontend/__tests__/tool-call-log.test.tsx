import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ensureAuthHeader } from '../lib/auth';

import { ToolCallLog } from '../components/ToolCallBlock';
import type { ChatEvent } from '../lib/events';

vi.mock('../lib/auth', () => ({
  ensureAuthHeader: vi
    .fn()
    .mockRejectedValue(new Error('Auth refresh unavailable')),
}));

function assistantToolEvents(
  overrides: Partial<Record<number, ChatEvent | null>> = {},
): ChatEvent[] {
  const events: ChatEvent[] = [
    {
      type: 'tool_call',
      name: 'web_search',
      arguments: { query: 'first' },
      tool_call_id: 's1',
    },
    {
      type: 'tool_result',
      name: 'web_search',
      result: JSON.stringify({
        query: 'first',
        results: [
          { title: 'A', url: 'https://a.example.com/one' },
          { title: 'B', url: 'https://b.example.org/two' },
        ],
        total_found: 2,
      }),
      tool_call_id: 's1',
    },
    {
      type: 'tool_call',
      name: 'web_fetch',
      arguments: { url: 'https://a.example.com/one' },
      tool_call_id: 'f1',
    },
    {
      type: 'tool_result',
      name: 'web_fetch',
      result: JSON.stringify({
        snapshot_id: 'x',
        url: 'https://a.example.com/one',
        final_url: 'https://a.example.com/one',
        title: 'A',
        content_length: 10,
        total_chars: 10,
        start_char: 0,
        end_char: 10,
        next_start_char: 10,
        has_more: false,
      }),
      tool_call_id: 'f1',
    },
  ];
  Object.entries(overrides).forEach(([index, value]) => {
    const i = Number(index);
    if (value === null) events.splice(i, 1);
    else if (value) events[i] = value;
  });
  return events;
}

describe('ToolCallLog grouped activity', () => {
  it('collapses many calls into ONE summary row with aria wiring', () => {
    render(React.createElement(ToolCallLog, { events: assistantToolEvents() }));
    // Collapsed: no per-tool buttons, no numbered Step text.
    expect(screen.queryByText(/Step 1/i)).toBeNull();
    expect(screen.queryByRole('button', { name: 'web_search' })).toBeNull();
    const toggle = screen.getByRole('button', { name: /tool/i });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.getAttribute('aria-controls')).toBeTruthy();
    expect(toggle.textContent).toContain('Searched 1 time · Read 1 page');
    expect(
      screen.getByRole('link', { name: 'A (a.example.com)' }),
    ).toBeTruthy();
  });

  it('expands to reveal every action inspector including repeats', () => {
    const events = assistantToolEvents();
    events.push(
      {
        type: 'tool_call',
        name: 'web_search',
        arguments: { query: 'second search' },
        tool_call_id: 's2',
      },
      {
        type: 'tool_result',
        name: 'web_search',
        result: JSON.stringify({ results: [], total_found: 0 }),
        tool_call_id: 's2',
      },
    );
    render(React.createElement(ToolCallLog, { events }));
    fireEvent.click(screen.getByRole('button', { name: /tool/i }));
    // Both same-name search calls now separately visible + expandable.
    expect(screen.getAllByRole('button', { name: 'web_search' })).toHaveLength(
      2,
    );
    expect(screen.getByRole('button', { name: 'web_fetch' })).toBeTruthy();
  });

  it('reports running state while calls lack results', () => {
    render(
      React.createElement(ToolCallLog, {
        events: assistantToolEvents({ 3: null }) /* drop fetch result */,
      }),
    );
    expect(screen.getByText(/Working…/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /working/i }));
    expect(screen.getByText('Running web_fetch...')).toBeTruthy();
  });

  it('reports error counts accessibly when a call fails', () => {
    render(
      React.createElement(ToolCallLog, {
        events: assistantToolEvents({
          3: {
            type: 'tool_result',
            name: 'web_fetch',
            tool_call_id: 'f1',
            result: {
              data: { error: 'denied' },
              url: 'https://failed.example.com',
            },
          },
        }),
      }),
    );
    expect(
      screen.getByRole('button', { name: /1 issue/ }).textContent,
    ).toContain('1 issue');
    expect(screen.queryByRole('link', { name: /failed.example/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /tool/i }));
    fireEvent.click(screen.getByRole('button', { name: 'web_fetch' }));
    expect(screen.getByText('denied')).toBeTruthy();
  });

  it('matches results by tool_call_id even when same-name calls repeat', () => {
    const events: ChatEvent[] = [
      {
        type: 'tool_call',
        name: 'web_search',
        arguments: { query: 'first' },
        tool_call_id: 'r1',
      },
      {
        type: 'tool_call',
        name: 'web_search',
        arguments: { query: 'second' },
        tool_call_id: 'r2',
      },
      {
        type: 'tool_result',
        name: 'web_search',
        result: JSON.stringify({ hits: ['second-result'] }),
        tool_call_id: 'r2',
      },
    ];
    render(React.createElement(ToolCallLog, { events }));
    fireEvent.click(screen.getByRole('button', { name: /working/i }));
    expect(
      screen.getByRole('button', { name: 'Running web_search...' }),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole('button', { name: 'Running web_search...' }),
    );
    expect(screen.getByText(/"query": "first"/)).toBeTruthy();
    expect(screen.queryByText(/second-result/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'web_search' }));
    expect(screen.getByText(/"query": "second"/)).toBeTruthy();
    expect(screen.getByText(/second-result/)).toBeTruthy();
  });

  it('keeps advisor-internal events out of the grouped view', () => {
    const events: ChatEvent[] = [
      {
        type: 'tool_call',
        name: 'consult_advisor',
        arguments: {},
        tool_call_id: 'c1',
      },
      {
        type: 'tool_result',
        name: 'consult_advisor',
        result: JSON.stringify({ advisor_id: 'a1' }),
        tool_call_id: 'c1',
      },
      {
        type: 'tool_call',
        name: 'get_time',
        advisor_id: 'a1',
        arguments: {},
      },
      {
        type: 'tool_result',
        name: 'get_time',
        result: 'noon',
        advisor_id: 'a1',
      },
    ];
    render(React.createElement(ToolCallLog, { events }));
    fireEvent.click(screen.getByRole('button', { name: /tool/i }));
    expect(
      screen.getAllByRole('button', { name: 'consult_advisor' }),
    ).toHaveLength(1);
    expect(screen.queryByText('get_time')).toBeNull();
  });

  it('retains image artifacts and handles a rejected auth refresh', async () => {
    const events: ChatEvent[] = [
      {
        type: 'tool_call',
        name: 'spawn_agent',
        arguments: { context: { mode: 'image', prompt: 'A cat' } },
        tool_call_id: 'sp1',
      },
      {
        type: 'tool_result',
        name: 'spawn_agent',
        result: JSON.stringify({
          data: { image_path: '/generated-images/test.png', prompt: 'A cat' },
          prompt: 'A cat',
        }),
        tool_call_id: 'sp1',
      },
    ];
    render(React.createElement(ToolCallLog, { events }));
    fireEvent.click(screen.getByRole('button', { name: /tool/i }));
    expect(screen.getByText('Image created')).toBeTruthy();
    expect(await screen.findByText('Failed to load image')).toBeTruthy();
    expect(ensureAuthHeader).toHaveBeenCalled();
  });

  it('reveals all spawn attempts inside expanded group', () => {
    const events: ChatEvent[] = [];
    for (let i = 0; i < 5; i += 1) {
      events.push(
        {
          type: 'tool_call',
          name: 'spawn_agent',
          arguments: { i },
          tool_call_id: `sp${i}`,
        },
        {
          type: 'tool_result',
          name: 'spawn_agent',
          result: JSON.stringify({ answer: `Attempt ${i}` }),
          tool_call_id: `sp${i}`,
        },
      );
    }
    render(React.createElement(ToolCallLog, { events }));
    expect(screen.queryByText(/Show earlier/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /tool/i }));
    expect(screen.getAllByRole('button', { name: 'spawn_agent' })).toHaveLength(
      5,
    );
  });

  it('keeps user expansion stable while new events stream in', async () => {
    const { rerender } = render(
      React.createElement(ToolCallLog, {
        events: assistantToolEvents({ 3: null }),
      }),
    );
    fireEvent.click(screen.getByRole('button', { name: /working/i }));
    expect(screen.getByRole('button', { name: 'web_search' })).toBeTruthy();
    rerender(
      React.createElement(ToolCallLog, {
        events: assistantToolEvents() /* result arrives */,
      }),
    );
    // Still expanded — no reset / no focus jump.
    expect(screen.getByRole('button', { name: 'web_search' })).toBeTruthy();
    expect(screen.queryByText(/Working…/)).toBeNull();
  });

  it('keeps the focused pending inspector expanded when its result arrives', () => {
    const { rerender } = render(
      <ToolCallLog events={assistantToolEvents({ 3: null })} />,
    );
    const toggle = screen.getByRole('button', { name: /Show activity/ });
    fireEvent.click(toggle);
    const inspector = screen.getByRole('button', {
      name: 'Running web_fetch...',
    });
    fireEvent.click(inspector);
    inspector.focus();
    rerender(<ToolCallLog events={assistantToolEvents()} />);
    expect(document.activeElement).toBe(inspector);
    expect(inspector.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText(/"snapshot_id":\s*"x"/)).toBeTruthy();
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
  });
});
