/** Keep suggestion activation outside the composer/draft submission path. */
export function isolateSuggestionBody(
  body: Record<string, unknown>,
): Record<string, unknown> {
  if (typeof body.suggestion_id !== 'string' || !body.suggestion_id)
    return body;
  const messages = Array.isArray(body.messages) ? body.messages : [];
  const lastUser = [...messages].reverse().find((message) => {
    return (
      typeof message === 'object' &&
      message !== null &&
      (message as { role?: unknown }).role === 'user'
    );
  });
  return {
    id: null,
    model: body.model,
    suggestion_id: body.suggestion_id,
    ...(body.answer_detail !== undefined
      ? { answer_detail: body.answer_detail }
      : {}),
    ...(body.research_effort !== undefined
      ? { research_effort: body.research_effort }
      : {}),
    messages: lastUser ? [lastUser] : [],
    // Suggestions run request-bound; declaring the client's features lets the
    // bridge say so, so Stop knows aborting the request is the cancellation.
    ...(Array.isArray(body.client_features)
      ? { client_features: body.client_features }
      : {}),
  };
}
