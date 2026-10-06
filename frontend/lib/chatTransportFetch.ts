import { getAuthGeneration, getAuthHeader, refreshIfNeeded } from './auth';
import { isolateSuggestionBody } from './suggestionSubmission';
import { keyForSubmission } from './pendingSubmission';

export const DURABLE_CLIENT_FEATURES = ['task-cancel', 'task-reset'];

function lastUserText(messages: unknown): string {
  if (!Array.isArray(messages)) return '';
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index] as {
      role?: unknown;
      content?: unknown;
      parts?: Array<{ type?: unknown; text?: unknown }>;
    };
    if (message?.role !== 'user') continue;
    if (typeof message.content === 'string') return message.content;
    return (message.parts ?? [])
      .filter((part) => part.type === 'text' && typeof part.text === 'string')
      .map((part) => part.text as string)
      .join('');
  }
  return '';
}

/** The lifetime belongs to this queued send, not to the mutable active chat. */
export async function chatTransportFetch(
  requestInput: RequestInfo | URL,
  init: RequestInit | undefined,
  scope: {
    model: string;
    conversationId: string | null;
    onGeneration: (generation: number) => void;
    /** Receives the idempotency key this request is sent with. */
    onSubmissionKey?: (key: string) => void;
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
  // This client can drive durable tasks: it cancels explicitly on Stop and
  // replaces text on a generation reset. The chat proxy forwards this to the
  // backend; an older cached bundle never declares it and stays request-bound.
  body.client_features = DURABLE_CLIENT_FEATURES;
  // One key per submission, kept until its outcome is known, so a retry
  // after a lost response or a reload replays the accepted task instead of
  // creating a second one.
  if (typeof body.idempotency_key !== 'string') {
    const pending = keyForSubmission(
      {
        text: lastUserText(body.messages),
        model: body.model,
        provider: body.provider,
        attachments: body.attachments,
      },
      typeof body.id === 'string' ? body.id : null,
    );
    body.idempotency_key = pending.key;
    // A resend of a new chat that the backend already accepted must replay
    // the original request exactly, including its (absent) conversation id.
    body.id = pending.requestConversationId;
  }
  scope.onSubmissionKey?.(body.idempotency_key as string);
  body = isolateSuggestionBody(body);
  const headers = new Headers(init?.headers);
  const authHeader = getAuthHeader();
  if (authHeader) headers.set('Authorization', authHeader);
  return fetch(requestInput, { ...init, headers, body: JSON.stringify(body) });
}
