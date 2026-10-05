'use client';

type Source = {
  conversation_id: string;
  title: string;
  messages: Array<{ id: string; role: string; content: string }>;
};

function readSources(value: unknown): Source[] {
  if (!value || typeof value !== 'object') return [];
  const envelope = value as { sources?: unknown };
  if (!Array.isArray(envelope.sources)) return [];
  return envelope.sources.slice(0, 6).flatMap((source: unknown) => {
    if (!source || typeof source !== 'object') return [];
    const record = source as Record<string, unknown>;
    if (
      typeof record.conversation_id !== 'string' ||
      typeof record.title !== 'string' ||
      !Array.isArray(record.messages)
    )
      return [];
    const messages = record.messages
      .slice(0, 12)
      .flatMap((message: unknown) => {
        if (!message || typeof message !== 'object') return [];
        const item = message as Record<string, unknown>;
        return typeof item.id === 'string' &&
          (item.role === 'user' || item.role === 'assistant') &&
          typeof item.content === 'string'
          ? [{ id: item.id, role: item.role, content: item.content }]
          : [];
      });
    return [
      {
        conversation_id: record.conversation_id,
        title: record.title,
        messages,
      },
    ];
  });
}

/** Bound source data stays separate from the exact submitted user prompt. */
export function SuggestionSourceContext({ context }: { context: unknown }) {
  const sources = readSources(context);
  if (!sources.length) return null;
  return (
    <details className="mt-3 text-xs text-[var(--color-text-secondary)]">
      <summary className="cursor-pointer min-h-touch flex items-center">
        Context used · {sources.map((source) => source.title).join(', ')}
      </summary>
      <div className="mt-2 max-h-72 overflow-y-auto space-y-3 rounded-lg border border-[var(--color-border-primary)] p-3">
        {sources.map((source) => (
          <section key={source.conversation_id}>
            <h3 className="font-medium">{source.title}</h3>
            {source.messages.map((message) => (
              <div key={message.id} className="mt-2">
                <span className="font-medium capitalize">{message.role}</span>
                <p className="whitespace-pre-wrap break-words">
                  {message.content}
                </p>
              </div>
            ))}
          </section>
        ))}
      </div>
    </details>
  );
}
