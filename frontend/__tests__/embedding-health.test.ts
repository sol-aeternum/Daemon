import { describe, expect, it } from 'vitest';

import {
  NO_BUDGET_ADAPTER_REASON,
  embeddingReasonLabel,
  presentEmbeddingHealth,
  resolveEmbeddingHealth,
} from '../lib/embeddingHealth';

function statusEmbeddings(embeddings: unknown): Record<string, unknown> {
  return { db_healthy: true, embeddings };
}

describe('resolveEmbeddingHealth', () => {
  it('stays unknown for an absent or malformed payload', () => {
    for (const raw of [undefined, null, {}, [], 'status']) {
      const health = resolveEmbeddingHealth(raw);
      expect(health.state).toBe('unknown');
      expect(health.reasonCodes).toEqual([]);
      expect(health.lastOutcome).toBeNull();
    }
  });

  it('stays unknown when the embeddings object or configuration is missing', () => {
    const missingObject = statusEmbeddings(undefined);
    const missingField = statusEmbeddings({
      observation_scope: 'backend_process',
    });
    const malformedConfiguration = statusEmbeddings({
      configuration: 'operational',
      reason_codes: [],
    });
    for (const raw of [missingObject, missingField, malformedConfiguration]) {
      expect(resolveEmbeddingHealth(raw).state).toBe('unknown');
    }
  });

  it('resolves unavailable with every safe reason code', () => {
    const health = resolveEmbeddingHealth(
      statusEmbeddings({
        observation_scope: 'backend_process',
        configuration: 'unavailable',
        reason_codes: [
          'missing_credentials',
          'route_unapproved',
          'budget_adapter_unavailable',
        ],
        last_outcome: 'never_attempted',
      }),
    );
    expect(health.state).toBe('unavailable');
    expect(health.reasonCodes).toEqual([
      'missing_credentials',
      'route_unapproved',
      'budget_adapter_unavailable',
    ]);
    expect(health.lastOutcome).toBe('never_attempted');
    expect(health.observationScope).toBe('backend_process');
  });

  it('treats eligible configuration as unverified, never operational-ready', () => {
    const withBudgetGap = resolveEmbeddingHealth(
      statusEmbeddings({
        observation_scope: 'backend_process',
        configuration: 'eligible',
        reason_codes: [NO_BUDGET_ADAPTER_REASON],
      }),
    );
    expect(withBudgetGap.state).toBe('unverified');
    expect(withBudgetGap.reasonCodes).toEqual([NO_BUDGET_ADAPTER_REASON]);
    // Even without any budget reason, eligible is not a provider test.
    const bare = resolveEmbeddingHealth(
      statusEmbeddings({ configuration: 'eligible', reason_codes: [] }),
    );
    expect(bare.state).toBe('unverified');
  });

  it('ignores unrecognized outcome values instead of trusting them', () => {
    const health = resolveEmbeddingHealth(
      statusEmbeddings({
        configuration: 'unavailable',
        reason_codes: [],
        last_outcome: 'all_systems_green',
      }),
    );
    expect(health.state).toBe('unavailable');
    expect(health.lastOutcome).toBeNull();
  });

  it('tolerates a missing reason list on eligible payloads', () => {
    const health = resolveEmbeddingHealth(
      statusEmbeddings({ configuration: 'eligible' }),
    );
    expect(health.state).toBe('unverified');
    expect(health.reasonCodes).toEqual([]);
  });
});

describe('presentEmbeddingHealth', () => {
  it('labels disabled/unverified/unknown states explicitly', () => {
    const unavailable = presentEmbeddingHealth(
      resolveEmbeddingHealth(
        statusEmbeddings({
          configuration: 'unavailable',
          reason_codes: ['missing_credentials', NO_BUDGET_ADAPTER_REASON],
        }),
      ),
    );
    expect(unavailable.label).toBe('Embeddings unavailable');
    expect(unavailable.className).toContain('bg-status-error-bg');
    expect(unavailable.detail).toContain('Embedding credentials missing');
    expect(unavailable.detail).toContain(
      'No account-budgeted embedding adapter',
    );

    const unverified = presentEmbeddingHealth(
      resolveEmbeddingHealth(
        statusEmbeddings({
          configuration: 'eligible',
          reason_codes: [NO_BUDGET_ADAPTER_REASON],
        }),
      ),
    );
    expect(unverified.label).toBe('Embeddings unverified');
    expect(unverified.className).toContain('bg-status-warning-bg');
    expect(unverified.detail).toContain('eligible but unverified');

    const unknown = presentEmbeddingHealth(resolveEmbeddingHealth(undefined));
    expect(unknown.label).toBe('Embeddings unknown');
    expect(unknown.className).toContain('bg-bg-tertiary');
  });

  it('renders unknown reason codes with a fixed neutral label', () => {
    expect(embeddingReasonLabel('route_unapproved')).toBe(
      'Embedding route not approved',
    );
    expect(embeddingReasonLabel('brand_new_code')).toBe(
      'Embedding reason unavailable',
    );
    for (const code of ['constructor', 'toString', '__proto__']) {
      expect(embeddingReasonLabel(code)).toBe('Embedding reason unavailable');
      const presentation = presentEmbeddingHealth(
        resolveEmbeddingHealth(
          statusEmbeddings({
            configuration: 'unavailable',
            reason_codes: [code],
          }),
        ),
      );
      expect(presentation.detail).toBe(
        'Embeddings unavailable: Embedding reason unavailable',
      );
    }
  });

  it('does not invent never-attempted history after an observed success', () => {
    const presentation = presentEmbeddingHealth(
      resolveEmbeddingHealth(
        statusEmbeddings({
          configuration: 'eligible',
          last_outcome: 'success',
          reason_codes: [NO_BUDGET_ADAPTER_REASON],
        }),
      ),
    );
    expect(presentation.label).toBe('Embeddings unverified');
    expect(presentation.detail).not.toContain('no provider dispatch has run');
    expect(presentation.detail).toContain('do not establish qualification');
  });
});
