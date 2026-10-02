import React from 'react';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { POST } from '../app/api/chat/route';
import { ChatActivityStatus } from '../components/ChatActivityStatus';
import { RoutingNotice } from '../components/RoutingNotice';
import type { ChatEvent } from '../lib/events';

type UIMessageChunk = Record<string, unknown> & { type: string };

function encodeFrame(eventType: string, data: Record<string, unknown>): string {
  return `event: ${eventType}\ndata: ${JSON.stringify(data)}`;
}

async function routingEventsFor(routingData: Record<string, unknown>) {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue(
        new Response(
          `${[
            encodeFrame('routing', { id: 'evt_routing', data: routingData }),
            encodeFrame('final', { id: 'evt_final', data: { text: 'Done.' } }),
          ].join('\n\n')}\n\n`,
          { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
        ),
      ),
  );
  const response = await POST(
    new Request('http://test/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: [{ role: 'user', content: 'hello' }],
        id: 'conv_1',
      }),
    }),
  );
  const body = await response.text();
  return body
    .split('\n')
    .filter((line) => line.startsWith('data: '))
    .map((line) => line.slice('data: '.length))
    .filter((payload) => payload !== '[DONE]')
    .map((payload) => JSON.parse(payload) as UIMessageChunk)
    .filter((part) => part.type === 'data-event')
    .map((part) => part.data as Record<string, unknown>)
    .filter((data) => data.type === 'routing');
}

describe('routing event bridge (additive fields)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('forwards profile, reason codes, effort and a disclosed fallback', async () => {
    const [routing] = await routingEventsFor({
      model: 'openrouter/openai/gpt-6-luna',
      tier: 'reasoning',
      reason: 'classification:complex',
      profile: 'routine',
      reason_codes: ['complexity_signal', 'fallback_capability_unavailable'],
      effort: 'low',
      fallback: {
        from_profile: 'reasoning',
        to_profile: 'routine',
        cause: 'capability_unavailable',
      },
    });
    expect(routing).toMatchObject({
      type: 'routing',
      model: 'openrouter/openai/gpt-6-luna',
      reason: 'classification:complex',
      profile: 'routine',
      reason_codes: ['complexity_signal', 'fallback_capability_unavailable'],
      effort: 'low',
      fallback: {
        from_profile: 'reasoning',
        to_profile: 'routine',
        cause: 'capability_unavailable',
      },
    });
  });

  it('keeps the original shape for payloads without the new fields', async () => {
    const [routing] = await routingEventsFor({
      model: 'openrouter/openai/gpt-6-luna',
      reason: 'classification:standard',
    });
    expect(routing).not.toHaveProperty('profile');
    expect(routing).not.toHaveProperty('reason_codes');
    expect(routing).not.toHaveProperty('effort');
    expect(routing).not.toHaveProperty('fallback');
    expect(routing.reason).toBe('classification:standard');
  });

  it('drops malformed optional fields instead of forwarding them', async () => {
    const [routing] = await routingEventsFor({
      model: 'openrouter/openai/gpt-6-luna',
      profile: 7,
      reason_codes: 'complexity_signal',
      effort: '   ',
      fallback: { from_profile: 'reasoning', cause: 'capability_unavailable' },
    });
    expect(routing).not.toHaveProperty('profile');
    expect(routing).not.toHaveProperty('reason_codes');
    expect(routing).not.toHaveProperty('effort');
    expect(routing).not.toHaveProperty('fallback');
  });
});

describe('routing visibility in the UI', () => {
  it('discloses a capability fallback', () => {
    render(
      <RoutingNotice
        fallback={{
          from_profile: 'reasoning',
          to_profile: 'routine',
          cause: 'capability_unavailable',
        }}
      />,
    );
    expect(screen.getByRole('note').textContent).toContain(
      "isn't included in your current plan",
    );
  });

  it('discloses a budget fallback', () => {
    render(
      <RoutingNotice
        fallback={{
          from_profile: 'reasoning',
          to_profile: 'routine',
          cause: 'budget_exceeded',
        }}
      />,
    );
    expect(screen.getByRole('note').textContent).toContain(
      'remaining compute budget',
    );
  });

  it('renders nothing without a fallback', () => {
    const { container } = render(<RoutingNotice />);
    expect(container.innerHTML).toBe('');
  });

  it('shows the effort actually sent next to the model', () => {
    const events: ChatEvent[] = [
      { type: 'routing', model: 'openrouter/openai/gpt-6-luna', effort: 'low' },
    ];
    render(<ChatActivityStatus events={events} isLoading />);
    expect(screen.getByRole('status').textContent).toContain(
      'gpt-6-luna · low',
    );
  });
});
