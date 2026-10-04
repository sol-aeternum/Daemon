/**
 * Semantic state of the memory embedding capability, sourced from the
 * authenticated ``GET /status`` response (additive ``embeddings`` object).
 *
 * The semantic states are deliberately explicit and conservative:
 * ``unavailable`` (configuration blocks dispatch), ``unverified``
 * (configuration is eligible but no provider dispatch has verified it, and
 * current adapters reserve no account compute budget, so this is never
 * operational-ready) and ``unknown`` (no usable status payload: missing
 * fields, malformed payload, a failed fetch, or an unrecognized
 * configuration value). Missing status fields or a failed fetch resolve to
 * ``unknown``, never to a positive state.
 */

export type EmbeddingConfiguration = 'eligible' | 'unavailable';

export type EmbeddingHealthState = 'unavailable' | 'unverified' | 'unknown';

export interface EmbeddingHealth {
  state: EmbeddingHealthState;
  /** Safe reason codes from the status response, in server order. */
  reasonCodes: string[];
  /**
   * Terminal outcome of the backend process' last dispatch attempt, if the
   * response carried a recognizable one. Observation is backend-process
   * scoped; it is never presented as global history.
   */
  lastOutcome: string | null;
  /** Present and recognizable only when the response states the scope. */
  observationScope: string | null;
}

const CONFIGURATION_STATES = new Set(['eligible', 'unavailable']);
const LAST_OUTCOMES = new Set([
  'never_attempted',
  'success',
  'configuration_denied',
  'provider_error',
]);

export const NO_BUDGET_ADAPTER_REASON = 'budget_adapter_unavailable';

/** Human labels for the safe reason codes; unknown codes keep a fixed label. */
export const EMBEDDING_REASON_LABELS: Record<string, string> = {
  missing_credentials: 'Embedding credentials missing',
  route_unapproved: 'Embedding route not approved',
  invalid_configuration: 'Embedding configuration invalid',
  adapter_unavailable: 'Embedding adapter unavailable',
  budget_adapter_unavailable: 'No account-budgeted embedding adapter',
};

export function embeddingReasonLabel(reasonCode: string): string {
  return Object.hasOwn(EMBEDDING_REASON_LABELS, reasonCode)
    ? EMBEDDING_REASON_LABELS[reasonCode]
    : 'Embedding reason unavailable';
}

function unknownHealth(reasonCodes: string[] = []): EmbeddingHealth {
  return {
    state: 'unknown',
    reasonCodes,
    lastOutcome: null,
    observationScope: null,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Resolve the embedding capability's semantic state from a ``/status``
 * payload. Never throws, never assumes health: anything it cannot
 * confidently interpret stays ``unknown``.
 */
export function resolveEmbeddingHealth(raw: unknown): EmbeddingHealth {
  if (!isRecord(raw) || !isRecord(raw.embeddings)) {
    // No ``embeddings`` object at all: unknown, not green.
    return unknownHealth();
  }

  const embeddings = raw.embeddings;
  const configuration = embeddings.configuration;
  if (
    typeof configuration !== 'string' ||
    !CONFIGURATION_STATES.has(configuration)
  ) {
    return unknownHealth();
  }

  const reasonCodes = Array.isArray(embeddings.reason_codes)
    ? embeddings.reason_codes.filter(
        (code): code is string => typeof code === 'string' && code.length > 0,
      )
    : [];
  const observationScope =
    typeof embeddings.observation_scope === 'string'
      ? embeddings.observation_scope
      : null;
  const lastOutcome =
    typeof embeddings.last_outcome === 'string' &&
    LAST_OUTCOMES.has(embeddings.last_outcome)
      ? embeddings.last_outcome
      : null;

  return {
    // ``eligible`` is not a provider test and current adapters reserve no
    // account budget, so eligible never maps to an operational-ready state.
    state: configuration === 'unavailable' ? 'unavailable' : 'unverified',
    reasonCodes,
    lastOutcome,
    observationScope,
  };
}

export interface EmbeddingHealthPresentation {
  label: string;
  detail: string;
  className: string;
}

export function presentEmbeddingHealth(
  health: EmbeddingHealth,
): EmbeddingHealthPresentation {
  if (health.state === 'unavailable') {
    const detail = health.reasonCodes.length
      ? `Embeddings unavailable: ${health.reasonCodes
          .map(embeddingReasonLabel)
          .join('; ')}`
      : 'Embeddings unavailable. Memory search stays lexical.';
    return {
      label: 'Embeddings unavailable',
      detail,
      className: 'bg-status-error-bg text-status-error',
    };
  }
  if (health.state === 'unverified') {
    const hasBudgetReason = health.reasonCodes.includes(
      NO_BUDGET_ADAPTER_REASON,
    );
    return {
      label: 'Embeddings unverified',
      detail: hasBudgetReason
        ? 'Embedding configuration is eligible but unverified: there is no account-budgeted embedding adapter. Backend-process observations do not establish qualification.'
        : 'Embedding configuration is eligible but unverified: static eligibility and backend-process observations do not establish qualification.',
      className: 'bg-status-warning-bg text-status-warning',
    };
  }
  return {
    label: 'Embeddings unknown',
    detail:
      'Embedding capability is currently unknown. Memory search stays lexical.',
    className: 'bg-bg-tertiary text-text-muted',
  };
}
