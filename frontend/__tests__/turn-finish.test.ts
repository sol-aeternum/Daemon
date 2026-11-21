import { describe, expect, it } from 'vitest';

import {
  finishReasonForTurnFinish,
  parseTurnFinish,
  TURN_FINISH_NOTICE_TEXTS,
  turnFinishNoticeForReason,
  type TurnFinish,
} from '../lib/turnFinish';

const confirmed = (
  reason: 'stop' | 'length' | 'content_filter' | 'tool_calls',
) => ({
  state: 'confirmed' as const,
  reason,
  provenance: 'provider_wire' as const,
});

const complete: TurnFinish = {
  version: 1,
  outcome: 'complete',
  reason: 'provider_stop',
  provider: confirmed('stop'),
  limits: [],
};

const lengthLimit: TurnFinish = {
  version: 1,
  outcome: 'incomplete',
  reason: 'provider_length',
  provider: confirmed('length'),
  limits: [{ kind: 'output', binding: true, value: 4096, unit: 'tokens' }],
};

describe('versioned turn finish parsing (#759)', () => {
  it('accepts a confirmed complete envelope', () => {
    expect(parseTurnFinish(complete)).toEqual(complete);
  });

  it('accepts an incomplete provider-length envelope with a binding limit', () => {
    expect(parseTurnFinish(lengthLimit)).toEqual(lengthLimit);
  });

  it('fails closed on legacy, malformed and contradictory envelopes', () => {
    expect(parseTurnFinish(undefined)).toBeUndefined();
    expect(parseTurnFinish(null)).toBeUndefined();
    expect(parseTurnFinish({ finish_reason: 'stop' })).toBeUndefined();
    expect(parseTurnFinish([])).toBeUndefined();

    // Version must be the integer 1, not a truthy stand-in.
    expect(parseTurnFinish({ ...complete, version: true })).toBeUndefined();
    expect(parseTurnFinish({ ...complete, version: 2 })).toBeUndefined();

    // Completion requires the provider's own confirmed stop...
    expect(
      parseTurnFinish({
        ...complete,
        provider: { state: 'missing' },
      }),
    ).toBeUndefined();
    expect(
      parseTurnFinish({
        ...complete,
        provider: { state: 'confirmed', reason: 'stop' },
      }),
    ).toBeUndefined();
    // ...the canonical reason...
    expect(
      parseTurnFinish({ ...complete, reason: 'provider_length' }),
    ).toBeUndefined();
    // ...and no binding local limit.
    expect(
      parseTurnFinish({
        ...complete,
        limits: [{ kind: 'output', binding: true }],
      }),
    ).toBeUndefined();

    // An unconfirmed provider cannot claim a reason or provenance.
    expect(
      parseTurnFinish({
        ...complete,
        outcome: 'unknown',
        provider: { state: 'missing', reason: 'stop' },
      }),
    ).toBeUndefined();
    // An unattempted answer has no dispatch.
    expect(
      parseTurnFinish({
        ...lengthLimit,
        provider: {
          state: 'not_attempted',
          dispatch_id: 'dispatch-1',
        },
      }),
    ).toBeUndefined();

    // Limits need a unit exactly when they carry a value.
    expect(
      parseTurnFinish({
        ...lengthLimit,
        limits: [{ kind: 'output', binding: true, value: 4096 }],
      }),
    ).toBeUndefined();
    expect(
      parseTurnFinish({
        ...lengthLimit,
        limits: [{ kind: 'output', binding: true, unit: 'tokens' }],
      }),
    ).toBeUndefined();
    expect(
      parseTurnFinish({
        ...lengthLimit,
        limits: [{ kind: 'Output!', binding: true }],
      }),
    ).toBeUndefined();

    // Unknown fields are not silently accepted.
    expect(parseTurnFinish({ ...complete, extra: true })).toBeUndefined();
    expect(
      parseTurnFinish({
        ...lengthLimit,
        provider: { ...lengthLimit.provider, extra: true },
      }),
    ).toBeUndefined();
  });
});

describe('SDK finish reason mapping (#759)', () => {
  it('claims stop only for a confirmed complete turn', () => {
    expect(finishReasonForTurnFinish(complete)).toBe('stop');
    expect(finishReasonForTurnFinish(undefined)).toBe('other');
  });

  it('maps confirmed provider outcomes and fails closed otherwise', () => {
    expect(finishReasonForTurnFinish(lengthLimit)).toBe('length');
    expect(
      finishReasonForTurnFinish({
        version: 1,
        outcome: 'failed',
        reason: 'provider_content_filter',
        provider: confirmed('content_filter'),
        limits: [],
      }),
    ).toBe('content-filter');
    expect(
      finishReasonForTurnFinish({
        version: 1,
        outcome: 'failed',
        reason: 'internal_error',
        provider: { state: 'missing' },
        limits: [],
      }),
    ).toBe('error');
    expect(
      finishReasonForTurnFinish({
        version: 1,
        outcome: 'unknown',
        reason: 'finish_evidence_missing',
        provider: { state: 'missing' },
        limits: [],
      }),
    ).toBe('other');
    expect(
      finishReasonForTurnFinish({
        version: 1,
        outcome: 'cancelled',
        reason: 'cancelled',
        provider: { state: 'missing' },
        limits: [],
      }),
    ).toBe('other');
  });
});

describe('pinned finish notices (#759)', () => {
  it('uses the approved wording and nothing speculative', () => {
    expect(turnFinishNoticeForReason('provider_length')).toBe(
      'The provider reached its output limit. This reply may be incomplete.',
    );
    expect(turnFinishNoticeForReason('finish_evidence_missing')).toBe(
      'The reply ended without a confirmed completion reason.',
    );
    expect(turnFinishNoticeForReason('finish_evidence_conflict')).toBe(
      "Conflicting completion information was received; this reply's completion is unknown.",
    );
    expect(turnFinishNoticeForReason('provider_content_filter')).toBe(
      TURN_FINISH_NOTICE_TEXTS.provider_content_filter,
    );
    expect(turnFinishNoticeForReason('budget_exceeded')).toBeUndefined();
  });
});
