import { describe, expect, it } from 'vitest';

import {
  getDaemonMessageText,
  getTurnFinish,
  getTurnFinishNotice,
  normalizeDaemonMessage,
  splitDaemonMessageNotice,
  type DaemonMessage,
} from '../lib/chatMessages';
import type { ChatEvent } from '../lib/events';
import type { TurnFinish } from '../lib/turnFinish';

const complete: TurnFinish = {
  version: 1,
  outcome: 'complete',
  reason: 'provider_stop',
  provider: { state: 'confirmed', reason: 'stop', provenance: 'provider_wire' },
  limits: [],
};

const providerLength: TurnFinish = {
  version: 1,
  outcome: 'incomplete',
  reason: 'provider_length',
  provider: {
    state: 'confirmed',
    reason: 'length',
    provenance: 'provider_wire',
  },
  limits: [{ kind: 'output', binding: true, value: 4096, unit: 'tokens' }],
};

const evidenceMissing: TurnFinish = {
  version: 1,
  outcome: 'unknown',
  reason: 'finish_evidence_missing',
  provider: { state: 'missing' },
  limits: [],
};

const cancelled: TurnFinish = {
  version: 1,
  outcome: 'cancelled',
  reason: 'cancelled',
  provider: { state: 'missing' },
  limits: [],
};

const frame = (data: ChatEvent) =>
  ({ type: 'data-event', data }) as DaemonMessage['parts'][number];
const textPart = (text: string) =>
  ({ type: 'text', text }) as DaemonMessage['parts'][number];

describe('persisted finish evidence (#759)', () => {
  it('shows a provider-length notice without touching saved content', () => {
    const message = normalizeDaemonMessage({
      id: 'a',
      role: 'assistant',
      content: 'Half an answer',
      status: 'error',
      metadata: { turn_finish: providerLength },
    })!;
    expect(getDaemonMessageText(message)).toBe('Half an answer');
    expect(message.parts).toEqual([{ type: 'text', text: 'Half an answer' }]);
    expect(getTurnFinish(message)).toEqual(providerLength);
    expect(getTurnFinishNotice(message)).toBe(
      'The provider reached its output limit. This reply may be incomplete.',
    );
  });

  it('leaves an unclosed HTML fence intact and keeps the finish notice separate', () => {
    const original = '```html:interactive\n<p>Partial HTML';
    const saved = normalizeDaemonMessage({
      id: 'html',
      role: 'assistant',
      content: original,
      status: 'error',
      metadata: {
        terminal_reason: 'provider_length',
        turn_finish: providerLength,
      },
    })!;
    expect(
      splitDaemonMessageNotice(saved, getDaemonMessageText(saved)),
    ).toEqual({ source: original, notice: '' });
    expect(getTurnFinishNotice(saved)).toMatch(/reached its output limit/);
  });

  it('does not duplicate the reply when a snapshot is reopened', () => {
    const message = normalizeDaemonMessage({
      id: 'a',
      role: 'assistant',
      content: 'Saved answer',
      metadata: { terminal_status: 'complete', turn_finish: complete },
    })!;
    expect(getDaemonMessageText(message)).toBe('Saved answer');
    expect(message.parts).toEqual([{ type: 'text', text: 'Saved answer' }]);
    expect(getTurnFinishNotice(message)).toBeUndefined();
  });

  it('uses the missing-evidence notice when the reason is unknown', () => {
    const message = normalizeDaemonMessage({
      id: 'a',
      role: 'assistant',
      content: 'Partial',
      metadata: { terminal_status: 'error', turn_finish: evidenceMissing },
    })!;
    expect(getTurnFinishNotice(message)).toBe(
      'The reply ended without a confirmed completion reason.',
    );
  });

  it('prefers an explicit control code over the generic finish reason', () => {
    const message = normalizeDaemonMessage({
      id: 'a',
      role: 'assistant',
      content: 'Sending the reminder',
      metadata: {
        terminal_status: 'error',
        terminal_reason: 'uncertain_effect',
        turn_finish: evidenceMissing,
      },
    })!;
    expect(getTurnFinishNotice(message)).toMatch(/may already have happened/);
  });

  it('rejects a length envelope that conflicts with the persisted failure', () => {
    const message = normalizeDaemonMessage({
      id: 'a',
      role: 'assistant',
      content: 'Half an answer',
      status: 'error',
      metadata: {
        terminal_reason: 'internal_error',
        turn_finish: providerLength,
      },
    })!;
    expect(getTurnFinish(message)).toBeUndefined();
    expect(getTurnFinishNotice(message)).toBe(
      'This request could not be completed.',
    );
  });

  it('keeps a cancel visible without appending it to saved content', () => {
    const message = normalizeDaemonMessage({
      id: 'a',
      role: 'assistant',
      content: 'Half an answer',
      metadata: { terminal_status: 'cancelled', turn_finish: cancelled },
    })!;
    expect(getDaemonMessageText(message)).toBe('Half an answer');
    expect(getTurnFinishNotice(message)).toBe(
      'Stopped before the answer was finished.',
    );
  });

  it('keeps the legacy code-in-content notice when no envelope exists', () => {
    const message = normalizeDaemonMessage({
      id: 'a',
      role: 'assistant',
      content: 'Half an answer',
      metadata: { terminal_reason: 'cancelled' },
    })!;
    expect(getDaemonMessageText(message)).toBe(
      'Half an answer\n\nStopped before the answer was finished.',
    );
    // The legacy path has no versioned evidence to render separately.
    expect(getTurnFinishNotice(message)).toBeUndefined();
  });
});

describe('live finish evidence (#759)', () => {
  it('reads the authoritative task snapshot when no metadata arrived', () => {
    const message: DaemonMessage = {
      id: 'a',
      role: 'assistant',
      metadata: {},
      parts: [
        textPart('Half an answer'),
        frame({
          type: 'task',
          task_id: 't',
          status: 'failed',
          turn_finish: providerLength,
          terminal_code: 'provider_length',
        }),
      ],
    } as DaemonMessage;
    expect(getTurnFinish(message)).toEqual(providerLength);
    expect(getDaemonMessageText(message)).toBe('Half an answer');
    expect(getTurnFinishNotice(message)).toBe(
      'The provider reached its output limit. This reply may be incomplete.',
    );
  });

  it('clears provisional evidence when a later terminal frame has none', () => {
    const message: DaemonMessage = {
      id: 'a',
      role: 'assistant',
      metadata: {},
      parts: [
        frame({
          type: 'task',
          task_id: 't',
          status: 'completed',
          turn_finish: complete,
        }),
        frame({ type: 'task', task_id: 't', status: 'completed' }),
      ],
    } as DaemonMessage;
    expect(getTurnFinish(message)).toBeUndefined();
    expect(getTurnFinishNotice(message)).toBeUndefined();
  });

  it('ignores evidence on a snapshot that is not terminal yet', () => {
    const message: DaemonMessage = {
      id: 'a',
      role: 'assistant',
      metadata: {},
      parts: [
        frame({
          type: 'task',
          task_id: 't',
          status: 'running',
          turn_finish: complete,
        }),
      ],
    } as DaemonMessage;
    expect(getTurnFinish(message)).toBeUndefined();
  });

  it('renders a live explicit terminal code without an envelope', () => {
    const message: DaemonMessage = {
      id: 'a',
      role: 'assistant',
      metadata: { terminal_reason: 'inference_dispatch_unresolved' },
      parts: [textPart('Partial')],
    } as DaemonMessage;
    expect(getTurnFinishNotice(message)).toMatch(
      /provider may still be working/,
    );
    // Persisted rows already carry the legacy notice in their content.
    const persisted = normalizeDaemonMessage({
      id: 'a',
      role: 'assistant',
      content: 'Partial',
      metadata: { terminal_reason: 'inference_dispatch_unresolved' },
    })!;
    expect(getTurnFinishNotice(persisted)).toBeUndefined();
  });

  it('drops evidence from an attempt a reset replaced', () => {
    const message: DaemonMessage = {
      id: 'a',
      role: 'assistant',
      metadata: {},
      parts: [
        frame({
          type: 'task',
          task_id: 't',
          status: 'completed',
          turn_finish: providerLength,
        }),
        frame({ type: 'task_reset', task_id: 't' }),
        textPart('Regenerated'),
      ],
    } as DaemonMessage;
    expect(getTurnFinish(message)).toBeUndefined();
    expect(getDaemonMessageText(message)).toBe('Regenerated');
  });

  it('never treats an invalid saved envelope as a completion', () => {
    const message: DaemonMessage = {
      id: 'a',
      role: 'assistant',
      metadata: {
        turn_finish: { ...complete, provider: { state: 'missing' } },
      },
      parts: [textPart('text')],
    } as DaemonMessage;
    expect(getTurnFinish(message)).toBeUndefined();
    expect(getTurnFinishNotice(message)).toBeUndefined();
  });
});
