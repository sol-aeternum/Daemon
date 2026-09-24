import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ToolCallLog } from '../components/ToolCallBlock';
import type { ChatEvent } from '../lib/events';

describe('ToolCallLog', () => {
  it('keeps the latest three spawns visible and makes all earlier attempts recoverable', () => {
    const events: ChatEvent[] = Array.from({ length: 5 }, (_, i) => [
      {
        type: 'tool_call' as const,
        name: 'spawn_agent',
        arguments: { attempt: i + 1 },
        tool_call_id: `spawn-${i}`,
      },
      {
        type: 'tool_result' as const,
        name: 'spawn_agent',
        result: { answer: `Attempt ${i + 1}` },
        tool_call_id: `spawn-${i}`,
      },
    ]).flat();
    events.splice(
      2,
      0,
      { type: 'tool_call', name: 'get_time', arguments: {} },
      { type: 'tool_result', name: 'get_time', result: 'noon' },
    );
    render(React.createElement(ToolCallLog, { events }));
    expect(screen.getAllByRole('button', { name: 'spawn_agent' })).toHaveLength(
      3,
    );
    expect(screen.getByRole('button', { name: 'get_time' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Show earlier (2)' }));
    expect(screen.getAllByRole('button', { name: 'spawn_agent' })).toHaveLength(
      5,
    );
    fireEvent.click(screen.getAllByRole('button', { name: 'spawn_agent' })[0]);
    expect(screen.getByText(/"attempt": 1/)).toBeTruthy();
    expect(screen.getByText(/"answer": "Attempt 1"/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Hide earlier (2)' }));
    expect(screen.getAllByRole('button', { name: 'spawn_agent' })).toHaveLength(
      3,
    );
  });

  it('matches out-of-order results to their original attempt', () => {
    const events: ChatEvent[] = [
      {
        type: 'tool_call',
        name: 'spawn_agent',
        arguments: { attempt: 'first' },
        tool_call_id: 'first',
      },
      {
        type: 'tool_call',
        name: 'spawn_agent',
        arguments: { attempt: 'second' },
        tool_call_id: 'second',
      },
      {
        type: 'tool_result',
        name: 'spawn_agent',
        result: 'first result',
        tool_call_id: 'first',
      },
    ];
    render(React.createElement(ToolCallLog, { events }));
    fireEvent.click(screen.getByRole('button', { name: 'spawn_agent' }));
    expect(screen.getByText(/"attempt": "first"/)).toBeTruthy();
    expect(screen.getByText('first result')).toBeTruthy();
    expect(screen.getByText('Creating image...')).toBeTruthy();
  });

  it('keeps advisor-internal tool events out of the top-level tool log', () => {
    const events: ChatEvent[] = [
      {
        type: 'tool_call',
        name: 'consult_advisor',
        arguments: { domain: 'coding', difficulty: 'high' },
        tool_call_id: 'call_consult_1',
      },
      {
        type: 'tool_result',
        name: 'consult_advisor',
        result: { advisor_id: 'advisor_1' },
        tool_call_id: 'call_consult_1',
      },
      {
        type: 'tool_call',
        name: 'get_time',
        arguments: { format: 'iso' },
        advisor_id: 'advisor_1',
        tool_call_id: 'call_nested_1',
      },
      {
        type: 'tool_result',
        name: 'get_time',
        result: { time: '2026-04-20T00:00:00Z' },
        advisor_id: 'advisor_1',
        tool_call_id: 'call_nested_1',
      },
    ];

    render(React.createElement(ToolCallLog, { events }));

    expect(screen.queryByText('consult_advisor')).not.toBeNull();
    expect(screen.queryByText('get_time')).toBeNull();
    expect(screen.queryByText('Step 1')).not.toBeNull();
    expect(screen.queryByText('Step 2')).toBeNull();
  });

  it('matches top-level tool results by tool_call_id before tool name', () => {
    const events: ChatEvent[] = [
      {
        type: 'tool_call',
        name: 'web_search',
        arguments: { query: 'first' },
        tool_call_id: 'call_search_1',
      },
      {
        type: 'tool_call',
        name: 'web_search',
        arguments: { query: 'second' },
        tool_call_id: 'call_search_2',
      },
      {
        type: 'tool_result',
        name: 'web_search',
        result: { hits: ['second'] },
        tool_call_id: 'call_search_2',
      },
    ];

    render(React.createElement(ToolCallLog, { events }));

    expect(screen.queryByText('Running web_search...')).not.toBeNull();
    const toolButtons = screen.getAllByRole('button');
    fireEvent.click(toolButtons[0]);

    expect(screen.queryByText(/"query": "second"/)).not.toBeNull();
    expect(screen.queryByText(/"hits": \[/)).not.toBeNull();
  });
});
