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
    messages: lastUser ? [lastUser] : [],
  };
}
