import { describe, expect, it } from 'vitest';
import {
  CAPABILITIES,
  LIMIT_KEYS,
  PLAN_IDS,
  PLAN_ORDER,
  PLAN_SURFACE,
  TRIAL_STATES,
  formatLimit,
  hasCapability,
  isCapability,
  isPlanId,
  isTrialState,
  parseEntitlements,
  type Entitlements,
} from '@/lib/entitlements';

function parseOk(payload: unknown): Entitlements {
  const result = parseEntitlements(payload);
  if (!result.ok) {
    throw new Error(`expected a parsed snapshot, got: ${result.error}`);
  }
  return result.entitlements;
}

const FREE_SNAPSHOT = {
  plan: 'free',
  capabilities: ['chat', 'video_generation'],
  trial: { state: 'active', source: 'grant', remaining_microusd: 500 },
  limits: {
    max_context_tokens: 128000,
    monthly_budget_microusd: 0,
    budget_remaining_microusd: null,
    budget_source: 'trial',
  },
};

describe('plan vocabulary', () => {
  it('recognises exactly the three commercial plans', () => {
    expect([...PLAN_IDS]).toEqual(['free', 'pro', 'power']);
    for (const planId of PLAN_IDS) {
      expect(isPlanId(planId)).toBe(true);
    }
  });

  it('rejects the retired tier names instead of treating them as plans', () => {
    for (const retired of ['starter', 'max', 'byok', 'enterprise', 'premium']) {
      expect(isPlanId(retired)).toBe(false);
    }
  });

  it('is case- and type-sensitive, so a mangled plan cannot slip through', () => {
    expect(isPlanId('Pro')).toBe(false);
    expect(isPlanId(' pro ')).toBe(false);
    expect(isPlanId(3)).toBe(false);
    expect(isPlanId(null)).toBe(false);
  });

  it('has display copy for every plan and orders the ladder low to high', () => {
    for (const planId of PLAN_IDS) {
      const surface = PLAN_SURFACE[planId];
      expect(surface.label.length).toBeGreaterThan(0);
      expect(surface.headline.length).toBeGreaterThan(0);
      expect(surface.blurb.length).toBeGreaterThan(0);
    }
    expect([...PLAN_ORDER]).toEqual([...PLAN_IDS]);
  });
});

describe('capability vocabulary', () => {
  it('recognises the agreed names, including the base product capabilities', () => {
    const base = [
      'chat',
      'web_research',
      'deep_research',
      'image_generation',
      'video_generation',
      'audio_generation',
      'premium_routing',
      'extended_agents',
      'large_context',
      'large_file_processing',
      'scheduled_tasks',
      'parallel_agents',
      'priority_execution',
    ];
    for (const name of base) {
      expect(isCapability(name)).toBe(true);
    }
  });

  it('does not treat a supplied key as a funding bypass', () => {
    // `byok` parses so the value is representable, but nothing in the client
    // derives an entitlement from it.
    expect(isCapability('byok')).toBe(true);
    const entitlements = parseOk({ plan: 'pro', capabilities: ['byok'] });
    expect(hasCapability(entitlements, 'chat')).toBe(false);
  });

  it('drops names it does not know rather than granting them', () => {
    const entitlements = parseOk({
      plan: 'power',
      capabilities: [
        'chat',
        'unlimited_everything',
        'premium_models',
        42,
        null,
        'chat',
      ],
    });
    expect(entitlements.capabilities).toEqual(['chat']);
  });

  it('never returns a duplicate capability', () => {
    const entitlements = parseOk({
      plan: 'pro',
      capabilities: ['chat', 'chat', 'video_generation', 'video_generation'],
    });
    expect(entitlements.capabilities).toEqual(['chat', 'video_generation']);
  });
});

describe('parseEntitlements', () => {
  it('accepts a well-formed snapshot', () => {
    const entitlements = parseOk(FREE_SNAPSHOT);
    expect(entitlements.plan).toBe('free');
    expect(entitlements.capabilities).toEqual(['chat', 'video_generation']);
    expect(entitlements.trial?.state).toBe('active');
    expect(entitlements.limits.max_context_tokens).toBe(128000);
  });

  it('fails closed on an unknown plan rather than silently upgrading', () => {
    for (const plan of [
      'starter',
      'max',
      'byok',
      'platinum',
      '',
      7,
      null,
      undefined,
    ]) {
      const result = parseEntitlements({ ...FREE_SNAPSHOT, plan });
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.error).toMatch(/known plan/i);
      }
    }
  });

  it('fails closed when the plan key is missing entirely', () => {
    const { plan: _omitted, ...withoutPlan } = FREE_SNAPSHOT;
    expect(parseEntitlements(withoutPlan).ok).toBe(false);
  });

  it('fails closed on a payload that is not an object', () => {
    for (const payload of [null, undefined, 'free', 42, true, ['free']]) {
      const result = parseEntitlements(payload);
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.error.length).toBeGreaterThan(0);
      }
    }
  });

  it('grants nothing when capabilities are missing or the wrong shape', () => {
    for (const capabilities of [undefined, null, 'chat', 5, { chat: true }]) {
      const entitlements = parseOk({ plan: 'pro', capabilities });
      expect(entitlements.capabilities).toEqual([]);
      expect(hasCapability(entitlements, 'chat')).toBe(false);
      expect(hasCapability(entitlements, 'premium_routing')).toBe(false);
    }
  });

  it('keeps only the known limit names and their scalar values', () => {
    const entitlements = parseOk({
      plan: 'pro',
      limits: {
        max_context_tokens: 400000,
        budget_source: 'plan',
        budget_remaining_microusd: null,
        secret_internal_routing_note: 'do not render',
        max_concurrent_operations: { nested: true },
      },
    });

    expect(entitlements.limits).toEqual({
      max_context_tokens: 400000,
      budget_source: 'plan',
      budget_remaining_microusd: null,
    });
    expect(
      Object.keys(entitlements.limits).every((key) =>
        (LIMIT_KEYS as readonly string[]).includes(key),
      ),
    ).toBe(true);
  });

  it('reads every limit name the server reports', () => {
    const serverLimits = Object.fromEntries(LIMIT_KEYS.map((key) => [key, 1]));
    const entitlements = parseOk({ plan: 'power', limits: serverLimits });
    expect(Object.keys(entitlements.limits).sort()).toEqual(
      [...LIMIT_KEYS].sort(),
    );
  });

  it('yields an empty limit map for a missing or non-object limits field', () => {
    for (const limits of [undefined, null, 'lots', 3, [1, 2]]) {
      expect(parseOk({ plan: 'free', limits }).limits).toEqual({});
    }
  });

  it('reads a trial of either agreed state and keeps the server fields', () => {
    for (const state of TRIAL_STATES) {
      const entitlements = parseOk({
        plan: 'free',
        trial: { state, source: 'grant', remaining_microusd: 0 },
      });
      expect(entitlements.trial?.state).toBe(state);
      expect(entitlements.trial?.source).toBe('grant');
    }
  });

  it('reports no trial for a missing, malformed, or third state', () => {
    for (const trial of [
      undefined,
      null,
      'active',
      {},
      { state: 'expired' },
      { state: 1 },
      [],
    ]) {
      expect(parseOk({ plan: 'free', trial }).trial).toBeNull();
    }
  });

  it('never reports a state the backend does not define', () => {
    // There is no `expired` trial: an allowance is either available or spent.
    expect(isTrialState('expired')).toBe(false);
    expect(TRIAL_STATES).toEqual(['active', 'exhausted']);
  });
});

describe('hasCapability fails closed', () => {
  it('grants nothing without entitlements', () => {
    for (const capability of CAPABILITIES) {
      expect(hasCapability(null, capability)).toBe(false);
      expect(hasCapability(undefined, capability)).toBe(false);
    }
  });

  it('grants only what the server listed', () => {
    const entitlements = parseOk({
      plan: 'free',
      capabilities: ['chat', 'web_research'],
    });
    expect(hasCapability(entitlements, 'chat')).toBe(true);
    expect(hasCapability(entitlements, 'web_research')).toBe(true);
    expect(hasCapability(entitlements, 'video_generation')).toBe(false);
    expect(hasCapability(entitlements, 'premium_routing')).toBe(false);
  });
});

describe('formatLimit', () => {
  it('renders integers exactly and keeps budgets as reported units', () => {
    expect(formatLimit(400000)).toBe('400000');
    expect(formatLimit(1.5)).toBe('1.50');
  });

  it('renders an absent limit as nothing to show', () => {
    expect(formatLimit(null)).toBeNull();
    expect(formatLimit(undefined)).toBeNull();
    expect(formatLimit('   ')).toBeNull();
  });

  it('renders a server-supplied label verbatim', () => {
    expect(formatLimit('trial')).toBe('trial');
  });
});
