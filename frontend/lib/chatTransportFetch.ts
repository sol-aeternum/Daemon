import { getAuthGeneration, getAuthHeader, refreshIfNeeded } from './auth';
import { isolateSuggestionBody } from './suggestionSubmission';

/** The lifetime belongs to this queued send, not to the mutable active chat. */
export async function chatTransportFetch(
  requestInput: RequestInfo | URL,
  init: RequestInit | undefined,
  scope: {
    model: string;
    conversationId: string | null;
    onGeneration: (generation: number) => void;
  },
): Promise<Response> {
  let body: Record<string, unknown> =
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
  body = isolateSuggestionBody(body);
  const headers = new Headers(init?.headers);
  const authHeader = getAuthHeader();
  if (authHeader) headers.set('Authorization', authHeader);
  return fetch(requestInput, { ...init, headers, body: JSON.stringify(body) });
}
