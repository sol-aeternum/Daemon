import { getAuthGeneration, getAuthHeader, refreshIfNeeded } from './auth';
import { isolateSuggestionBody } from './suggestionSubmission';
import { pendingSubmission, registerSubmission } from './pendingSubmission';

export const DURABLE_CLIENT_FEATURES = ['task-cancel', 'task-reset'];

/** The lifetime belongs to this queued send, not to the mutable active chat. */
export async function chatTransportFetch(
  requestInput: RequestInfo | URL,
  init: RequestInit | undefined,
  scope: {
    model: string;
    conversationId: string | null;
    onGeneration: (generation: number) => void;
    /**
     * Receives the idempotency key this request is sent with (none for
     * suggestions) and the conversation it was queued in, as captured here,
     * whatever the route has become since.
     */
    onSubmissionKey?: (
      key: string | null,
      conversationId: string | null,
    ) => void;
  },
): Promise<Response> {
  const body: Record<string, unknown> =
    typeof init?.body === 'string' ? JSON.parse(init.body) : {};
  const generation = body.suggestion_id
    ? body.__suggestionAuthGeneration
    : getAuthGeneration();
  delete body.__suggestionAuthGeneration;
  const assertCurrent = () => {
    if (
      typeof generation !== 'number' ||
      generation !== getAuthGeneration() ||
      init?.signal?.aborted
    ) {
      throw new Error(
        'Authentication changed before sending. Please review your draft.',
      );
    }
  };
  assertCurrent();
  scope.onGeneration(generation as number);
  await refreshIfNeeded();
  assertCurrent();
  body.model = scope.model;
  body.id = scope.conversationId;
  // This client can drive durable tasks: it cancels explicitly on Stop and
  // replaces text on a generation reset. The chat proxy forwards this to the
  // backend; an older cached bundle never declares it and stays request-bound.
  body.client_features = DURABLE_CLIENT_FEATURES;
  if (typeof body.suggestion_id === 'string' && body.suggestion_id) {
    // Suggestion acceptance is request-bound and never deduplicated by key:
    // record no pending submission that the request would not carry.
    delete body.idempotency_key;
    scope.onSubmissionKey?.(null, null);
    return send(requestInput, init, isolateSuggestionBody(body));
  }
  // The key belongs to the submitted draft (the page passes it): resending a
  // held draft reuses its key, so the backend replays the task it already
  // accepted instead of creating a second one. Other sends get a fresh key.
  const key =
    typeof body.idempotency_key === 'string' && body.idempotency_key
      ? body.idempotency_key
      : crypto.randomUUID();
  body.idempotency_key = key;
  const recorded = pendingSubmission(key);
  if (recorded) {
    // A resend of a request the backend may already have accepted replays it
    // exactly: its (possibly absent) conversation id, and the model and
    // provider it was sent with, even if the picker has since reset.
    body.id = recorded.requestConversationId;
    body.model = recorded.model ?? 'auto';
    if (recorded.provider == null) delete body.provider;
    else body.provider = recorded.provider;
  } else {
    registerSubmission(key, {
      scope: scope.conversationId,
      requestConversationId: typeof body.id === 'string' ? body.id : null,
      model: body.model,
      provider: body.provider,
    });
  }
  scope.onSubmissionKey?.(body.idempotency_key as string, scope.conversationId);
  return send(requestInput, init, body);
}

function send(
  requestInput: RequestInfo | URL,
  init: RequestInit | undefined,
  body: Record<string, unknown>,
): Promise<Response> {
  const headers = new Headers(init?.headers);
  const authHeader = getAuthHeader();
  if (authHeader) headers.set('Authorization', authHeader);
  return fetch(requestInput, { ...init, headers, body: JSON.stringify(body) });
}
