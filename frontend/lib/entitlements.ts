/**
 * Client-side model of the server-authoritative entitlement response.
 *
 * The backend owns plan, capabilities, trial state, and limits. This module only
 * describes the shape the client understands and fails closed: anything the
 * client cannot verify is treated as "not granted" and never as a paid plan.
 */

export const PLAN_IDS = ['free', 'pro', 'power'] as const;
export type PlanId = (typeof PLAN_IDS)[number];

/**
 * The product's agreed capability vocabulary, mirroring the backend's closed
 * `Capability` set. There are deliberately no synonyms: a name the client does
 * not recognise is dropped, never treated as granted.
 *
 * `byok` is recognised so the value parses, but the client must never infer
 * from it that an account can bypass funding: `can()` is presentation only and
 * the server settles every reservation.
 */
export const CAPABILITIES = [
  'chat',
  'web_research',
  'deep_research',
  'scheduled_tasks',
  'parallel_agents',
  'extended_agents',
  'large_file_processing',
  'large_context',
  'priority_execution',
  'premium_routing',
  'image_generation',
  'video_generation',
  'audio_generation',
  'byok',
] as const;
export type Capability = (typeof CAPABILITIES)[number];

/** Trial states are the backend's: a trial ends when its allowance is spent. */
export const TRIAL_STATES = ['active', 'exhausted'] as const;
export type TrialState = (typeof TRIAL_STATES)[number];

export interface EntitlementTrial {
  state: TrialState;
  [key: string]: unknown;
}

export type EntitlementLimit = number | string | boolean | null;

/**
 * Limit names the server reports, mirroring `ResolvedPolicy.limits_dict()`.
 * Budgets are integer microusd; `budget_source` is a label; a `null` remaining
 * budget means the plan has no recurring allowance to draw on.
 */
export const LIMIT_KEYS = [
  'max_concurrent_operations',
  'max_context_tokens',
  'max_output_tokens',
  'max_tool_loop_iterations',
  'requests_per_minute',
  'extended_runs_per_period',
  'extended_run_budget_microusd',
  'monthly_budget_microusd',
  'budget_ceiling_microusd',
  'budget_source',
  'budget_remaining_microusd',
] as const;
export type LimitKey = (typeof LIMIT_KEYS)[number];

export interface Entitlements {
  plan: PlanId;
  capabilities: Capability[];
  trial: EntitlementTrial | null;
  limits: Record<string, EntitlementLimit>;
}

export type EntitlementsParseResult =
  | { ok: true; entitlements: Entitlements }
  | { ok: false; error: string };

export interface PlanSurface {
  label: string;
  headline: string;
  blurb: string;
}

/**
 * Plan framing only. What an account may actually do is decided by the
 * capability list the server returns, never by the plan name alone.
 */
export const PLAN_SURFACE: Record<PlanId, PlanSurface> = {
  free: {
    label: 'Free',
    headline: 'Free answers things',
    blurb: 'Everyday answers with the models and memory Daemon ships with.',
  },
  pro: {
    label: 'Pro',
    headline: 'Pro gets things done',
    blurb:
      'Longer runs and bigger inputs for work that has to reach a finish line.',
  },
  power: {
    label: 'Power',
    headline: 'Power handles workloads',
    blurb:
      'Workload-scale runs with parallel agents, scheduled work, and priority execution.',
  },
};

export const PLAN_ORDER: PlanId[] = ['free', 'pro', 'power'];

export interface TrialSurface {
  status: string;
  detail: string;
}

/**
 * Trial copy stays brief and plan-agnostic: an exhausted trial says what is no
 * longer available and what still works, without claiming a paid plan the
 * server has not confirmed.
 */
export const TRIAL_SURFACE: Record<TrialState, TrialSurface> = {
  active: {
    status: 'Trial active',
    detail:
      'Premium models and extended limits can be used while your trial has allowance and qualified models are available.',
  },
  exhausted: {
    status: 'Trial allowance used',
    detail:
      'The trial allowance is spent. Chat, memory, and your history keep working exactly as before.',
  },
};

export function isPlanId(value: unknown): value is PlanId {
  return (
    typeof value === 'string' && (PLAN_IDS as readonly string[]).includes(value)
  );
}

export function isCapability(value: unknown): value is Capability {
  return (
    typeof value === 'string' &&
    (CAPABILITIES as readonly string[]).includes(value)
  );
}

export function isTrialState(value: unknown): value is TrialState {
  return (
    typeof value === 'string' &&
    (TRIAL_STATES as readonly string[]).includes(value)
  );
}

function toRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function parseCapabilities(value: unknown): Capability[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const granted = value.filter(isCapability);
  return Array.from(new Set(granted));
}

function parseTrial(value: unknown): EntitlementTrial | null {
  const record = toRecord(value);
  if (!record || !isTrialState(record.state)) {
    return null;
  }
  return { ...record, state: record.state };
}

function parseLimits(value: unknown): Record<string, EntitlementLimit> {
  const record = toRecord(value);
  if (!record) {
    return {};
  }
  const limits: Record<string, EntitlementLimit> = {};
  for (const [key, entry] of Object.entries(record)) {
    if (!(LIMIT_KEYS as readonly string[]).includes(key)) {
      continue;
    }
    if (
      entry === null ||
      ['number', 'string', 'boolean'].includes(typeof entry)
    ) {
      limits[key] = entry as EntitlementLimit;
    }
  }
  return limits;
}

/**
 * Parses an entitlement payload without trusting it: an unknown plan is an
 * error (never a silent upgrade) and unknown capability names are dropped.
 */
export function parseEntitlements(payload: unknown): EntitlementsParseResult {
  const record = toRecord(payload);
  if (!record) {
    return { ok: false, error: 'Plan response was not understood.' };
  }
  if (!isPlanId(record.plan)) {
    return { ok: false, error: 'Plan response did not include a known plan.' };
  }

  return {
    ok: true,
    entitlements: {
      plan: record.plan,
      capabilities: parseCapabilities(record.capabilities),
      trial: parseTrial(record.trial),
      limits: parseLimits(record.limits),
    },
  };
}

/** Fails closed: unknown entitlements never grant a capability. */
export function hasCapability(
  entitlements: Entitlements | null | undefined,
  capability: Capability,
): boolean {
  if (!entitlements) {
    return false;
  }
  return entitlements.capabilities.includes(capability);
}

/** Renders a limit for display, or null when the server did not report one. */
export function formatLimit(
  limit: EntitlementLimit | undefined,
): string | null {
  if (limit === null || limit === undefined) {
    return null;
  }
  if (typeof limit === 'boolean') {
    return limit ? 'Unlimited' : 'None';
  }
  if (typeof limit === 'number') {
    return Number.isInteger(limit) ? String(limit) : limit.toFixed(2);
  }
  const trimmed = limit.trim();
  return trimmed.length > 0 ? trimmed : null;
}
