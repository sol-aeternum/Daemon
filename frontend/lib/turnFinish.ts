import type { FinishReason } from 'ai';

/**
 * Versioned terminal evidence for one answer (#759).
 *
 * The envelope is evidence, never a dispatch permit or a billing receipt:
 * ``complete`` is only trustworthy when the provider's own wire said
 * ``stop``, the reason is the canonical ``provider_stop`` and no local
 * limit was binding. Anything else is incomplete, failed, cancelled or
 * unknown, and must not be rendered as a normal completion.
 *
 * Legacy records and malformed envelopes fail closed (``undefined``): an
 * absent reason is not a confirmed stop, and neither is EOF.
 */
export const TURN_FINISH_VERSION = 1 as const;

export type TurnFinishOutcome =
  | 'complete'
  | 'incomplete'
  | 'failed'
  | 'cancelled'
  | 'unknown';

export type ProviderFinishState =
  | 'confirmed'
  | 'missing'
  | 'conflicting'
  | 'not_attempted';

export type ProviderFinishReason =
  | 'stop'
  | 'length'
  | 'content_filter'
  | 'tool_calls';

export type LocalLimitUnit =
  | 'tokens'
  | 'microusd'
  | 'seconds'
  | 'attempts'
  | 'rounds'
  | 'requests';

export type TurnFinishProvider = {
  state: ProviderFinishState;
  reason?: ProviderFinishReason;
  provenance?: 'provider_wire';
  dispatch_id?: string;
};

export type TurnFinishLimit = {
  kind: string;
  binding: boolean;
  value?: number;
  unit?: LocalLimitUnit;
};

export type TurnFinish = {
  version: typeof TURN_FINISH_VERSION;
  outcome: TurnFinishOutcome;
  reason: string;
  provider: TurnFinishProvider;
  limits: TurnFinishLimit[];
};

const OUTCOMES: ReadonlySet<string> = new Set([
  'complete',
  'incomplete',
  'failed',
  'cancelled',
  'unknown',
]);
const PROVIDER_STATES: ReadonlySet<string> = new Set([
  'confirmed',
  'missing',
  'conflicting',
  'not_attempted',
]);
const PROVIDER_REASONS: ReadonlySet<string> = new Set([
  'stop',
  'length',
  'content_filter',
  'tool_calls',
]);
const LIMIT_UNITS: ReadonlySet<string> = new Set([
  'tokens',
  'microusd',
  'seconds',
  'attempts',
  'rounds',
  'requests',
]);
const REASON = /^[a-z][a-z0-9_]{0,63}$/;
const DISPATCH_ID = /^[A-Za-z0-9_-]{1,64}$/;
const MAX_LIMITS = 24;

const TURN_KEYS = ['version', 'outcome', 'reason', 'provider', 'limits'];
const PROVIDER_KEYS = ['state', 'reason', 'provenance', 'dispatch_id'];
const LIMIT_KEYS = ['kind', 'binding', 'value', 'unit'];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Mirrors the backend's ``extra="forbid"``: unknown fields fail closed. */
function hasOnlyKeys(
  record: Record<string, unknown>,
  allowed: readonly string[],
): boolean {
  return Object.keys(record).every((key) => allowed.includes(key));
}

function parseProvider(value: unknown): TurnFinishProvider | undefined {
  if (!isRecord(value) || !hasOnlyKeys(value, PROVIDER_KEYS)) return undefined;
  const state = value.state;
  if (typeof state !== 'string' || !PROVIDER_STATES.has(state))
    return undefined;
  const reason = value.reason;
  const provenance = value.provenance;
  const dispatchId = value.dispatch_id;
  if (
    reason !== undefined &&
    (typeof reason !== 'string' || !PROVIDER_REASONS.has(reason))
  )
    return undefined;
  if (provenance !== undefined && provenance !== 'provider_wire')
    return undefined;
  if (
    dispatchId !== undefined &&
    (typeof dispatchId !== 'string' || !DISPATCH_ID.test(dispatchId))
  )
    return undefined;
  if (state === 'confirmed') {
    // Wire evidence only: a claimed reason without provenance is not a
    // confirmed provider outcome.
    if (reason === undefined || provenance !== 'provider_wire')
      return undefined;
  } else if (reason !== undefined || provenance !== undefined) {
    return undefined;
  }
  if (state === 'not_attempted' && dispatchId !== undefined) return undefined;
  return {
    state: state as ProviderFinishState,
    ...(reason !== undefined ? { reason: reason as ProviderFinishReason } : {}),
    ...(provenance !== undefined
      ? { provenance: 'provider_wire' as const }
      : {}),
    ...(dispatchId !== undefined ? { dispatch_id: dispatchId } : {}),
  };
}

function parseLimit(value: unknown): TurnFinishLimit | undefined {
  if (!isRecord(value) || !hasOnlyKeys(value, LIMIT_KEYS)) return undefined;
  const kind = value.kind;
  if (typeof kind !== 'string' || !REASON.test(kind)) return undefined;
  if (typeof value.binding !== 'boolean') return undefined;
  const hasValue = value.value !== undefined;
  const hasUnit = value.unit !== undefined;
  if (hasValue !== hasUnit) return undefined;
  if (hasValue) {
    if (
      typeof value.value !== 'number' ||
      !Number.isFinite(value.value) ||
      value.value < 0
    )
      return undefined;
    if (typeof value.unit !== 'string' || !LIMIT_UNITS.has(value.unit))
      return undefined;
  }
  return {
    kind,
    binding: value.binding,
    ...(hasValue ? { value: value.value as number } : {}),
    ...(hasUnit ? { unit: value.unit as LocalLimitUnit } : {}),
  };
}

/**
 * Accept only a coherent version-1 envelope. Malformed, truncated or
 * contradictory evidence returns ``undefined`` rather than a guess.
 */
export function parseTurnFinish(value: unknown): TurnFinish | undefined {
  if (!isRecord(value) || !hasOnlyKeys(value, TURN_KEYS)) return undefined;
  // ``true`` is numerically 1 in loose checks; the wire type is an integer.
  if (
    typeof value.version !== 'number' ||
    value.version !== TURN_FINISH_VERSION
  )
    return undefined;
  const outcome = value.outcome;
  if (typeof outcome !== 'string' || !OUTCOMES.has(outcome)) return undefined;
  const reason = value.reason;
  if (typeof reason !== 'string' || !REASON.test(reason)) return undefined;
  const provider = parseProvider(value.provider);
  if (provider === undefined) return undefined;
  if (!Array.isArray(value.limits) || value.limits.length > MAX_LIMITS)
    return undefined;
  const limits: TurnFinishLimit[] = [];
  for (const candidate of value.limits) {
    const limit = parseLimit(candidate);
    if (limit === undefined) return undefined;
    limits.push(limit);
  }
  if (
    outcome === 'complete' &&
    (reason !== 'provider_stop' ||
      provider.reason !== 'stop' ||
      limits.some((limit) => limit.binding))
  ) {
    // Completion requires the provider's own stop and no local cap.
    return undefined;
  }
  return {
    version: TURN_FINISH_VERSION,
    outcome: outcome as TurnFinishOutcome,
    reason,
    provider,
    limits,
  };
}

/** A valid envelope must also agree with the enclosing terminal record. */
export function readTerminalFinish(
  value: unknown,
  status: unknown,
  reason?: unknown,
): TurnFinish | undefined {
  const finish = parseTurnFinish(value);
  if (!finish) return undefined;
  const { outcome } = finish;
  switch (status) {
    case 'completed':
    case 'complete':
      return outcome === 'unknown' || (outcome === 'complete' && !reason)
        ? finish
        : undefined;
    case 'cancelled':
      return outcome === 'unknown' || outcome === 'cancelled'
        ? finish
        : undefined;
    case 'failed':
    case 'needs_attention':
    case 'error':
      if (outcome === 'unknown') return finish;
      if (outcome !== 'incomplete' && outcome !== 'failed') return undefined;
      if (!reason || reason === finish.reason) return finish;
      if (status !== 'failed' && reason === 'uncertain_effect') return finish;
      // The native error adapter retains the existing capacity classification
      // while recording the positively observed deadline as turn evidence.
      return status === 'error' &&
        reason === 'capacity_unavailable' &&
        finish.reason === 'deadline_exceeded' &&
        finish.limits.some(({ kind, binding }) => kind === 'time' && binding)
        ? finish
        : undefined;
    default:
      return undefined;
  }
}

/**
 * The SDK's finish reason for a turn. Only evidence-backed outcomes claim
 * ``stop``; a legacy or ambiguous terminal status stays ``other``.
 */
export function finishReasonForTurnFinish(
  finish: TurnFinish | undefined,
): FinishReason {
  if (finish === undefined) return 'other';
  switch (finish.outcome) {
    case 'complete':
      return 'stop';
    case 'incomplete':
      return finish.provider.reason === 'length' ? 'length' : 'other';
    case 'failed':
      if (finish.provider.reason === 'content_filter') return 'content-filter';
      if (finish.provider.reason === 'tool_calls') return 'tool-calls';
      return 'error';
    default:
      return finish.provider.reason === 'tool_calls' ? 'tool-calls' : 'other';
  }
}

/**
 * Pinned user-facing wording for the finish reasons the contract names.
 * Existing local/control codes keep their own notices (see
 * ``chatMessages.TASK_TERMINAL_NOTICES``).
 */
export const TURN_FINISH_NOTICE_TEXTS = {
  provider_length:
    'The provider reached its output limit. This reply may be incomplete.',
  provider_content_filter: 'The provider filtered this reply.',
  finish_evidence_missing:
    'The reply ended without a confirmed completion reason.',
  finish_evidence_conflict:
    "Conflicting completion information was received; this reply's completion is unknown.",
} as const;

export function turnFinishNoticeForReason(reason: string): string | undefined {
  return Object.prototype.hasOwnProperty.call(TURN_FINISH_NOTICE_TEXTS, reason)
    ? TURN_FINISH_NOTICE_TEXTS[reason as keyof typeof TURN_FINISH_NOTICE_TEXTS]
    : undefined;
}
