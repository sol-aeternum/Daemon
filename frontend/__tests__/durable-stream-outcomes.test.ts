import { describe, expect, it } from 'vitest';

import type { ChatEvent } from '../lib/events';
import {
  TERMINAL_TASK_STATUSES,
  getDaemonDataEvents,
  getDaemonMessageText,
  type DaemonMessage,
} from '../lib/chatMessages';

const frame = (data: ChatEvent) =>
  ({ type: 'data-event', data }) as DaemonMessage['parts'][number];

const textPart = (text: string) =>
  ({ type: 'text', text }) as DaemonMessage['parts'][number];

const messageWith = (parts: DaemonMessage['parts']): DaemonMessage =>
  ({ id: 'm', role: 'assistant', parts }) as DaemonMessage;

describe('live regeneration disclosure (#477)', () => {
  it('appends a notice only for a positive count on the live reset frame', () => {
    const regenerated = messageWith([
      textPart('The first attempt was long'),
      frame({
        type: 'task_reset',
        task_id: 't',
        regenerated_after_interruption: 1,
      }),
      textPart('Regenerated answer'),
    ]);
    // Only the text written after the reset is current, and the disclosed
    // count is what makes it a regeneration.
    expect(getDaemonMessageText(regenerated)).toBe(
      'Regenerated answer\n\nThis answer was regenerated after an interruption.',
    );

    const sameGeneration = messageWith([
      frame({
        type: 'task_reset',
        task_id: 't',
        regenerated_after_interruption: 0,
      }),
      textPart('Corrected answer'),
    ]);
    expect(getDaemonMessageText(sameGeneration)).toBe('Corrected answer');

    const bare = messageWith([
      frame({ type: 'task_reset', task_id: 't' }),
      textPart('Deferred answer'),
    ]);
    expect(getDaemonMessageText(bare)).toBe('Deferred answer');
  });

  it('takes the maximum count across frames but not from bare lifecycle evidence', () => {
    const message = messageWith([
      frame({
        type: 'task',
        task_id: 't',
        status: 'running',
        lifecycle_kind: 'regenerate',
        lifecycle_epoch: 3,
      }),
      textPart('partial'),
      frame({
        type: 'task_reset',
        task_id: 't',
        regenerated_after_interruption: 1,
      }),
      textPart('second'),
      frame({
        type: 'task',
        task_id: 't',
        status: 'running',
        regenerated_after_interruption: 2,
      }),
      textPart(' answer'),
    ]);
    expect(getDaemonMessageText(message)).toBe(
      'second answer\n\nThis answer was regenerated after an interruption.',
    );
  });
});

describe('attempt-scoped events after a reset (#477)', () => {
  it('keeps request-scoped events but drops the replaced attempt\u2019s progress', () => {
    const message = messageWith([
      frame({ type: 'tool_call', name: 'web_search', arguments: {} }),
      frame({ type: 'conversation', conversation_id: 'conv-2' }),
      frame({ type: 'task', task_id: 't', status: 'running' }),
      frame({ type: 'request_bound' }),
      frame({ type: 'task_reset', task_id: 't' }),
      frame({ type: 'tool_call', name: 'web_fetch', arguments: {} }),
    ]);
    const types = getDaemonDataEvents([message]).map((event) => event.type);
    expect(types).toEqual([
      'conversation',
      'task',
      'request_bound',
      'tool_call',
    ]);
  });
});

describe('shared terminal status contract', () => {
  it('matches what the route bridge treats as settled', () => {
    for (const status of [
      'completed',
      'failed',
      'cancelled',
      'needs_attention',
    ]) {
      expect(TERMINAL_TASK_STATUSES.has(status)).toBe(true);
    }
    expect(TERMINAL_TASK_STATUSES.has('running')).toBe(false);
  });
});
