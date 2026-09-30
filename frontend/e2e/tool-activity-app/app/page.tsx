'use client';

import { useState } from 'react';
import { ToolCallLog } from '../../../components/ToolCallBlock';
import MarkdownMessage from '../../../components/MarkdownMessage';
import { buildMessageCitationSources } from '../../../lib/messageSources';
import type { ChatEvent } from '../../../lib/events';

// Synthetic only: no auth, conversation history or provider calls. These are
// the exact production components used by the chat page, never a UI copy.
const initialEvents: ChatEvent[] = [];
function completed(
  id: string,
  name: string,
  result: unknown,
  args: Record<string, unknown> = {},
) {
  initialEvents.push(
    { type: 'tool_call', name, arguments: args, tool_call_id: id },
    { type: 'tool_result', name, result, tool_call_id: id },
  );
}
for (let i = 1; i <= 3; i += 1) {
  completed(
    `search-${i}`,
    'web_search',
    {
      results: Array.from({ length: 5 }, (_, j) => ({
        title: `Source ${j + 1}`,
        url: `https://source${j + 1}.example/guide`,
      })),
    },
    { query: `Synthetic search ${i}` },
  );
}
for (let i = 1; i <= 2; i += 1) {
  completed(
    `read-${i}`,
    'web_fetch',
    {
      url: `https://source${i}.example/guide`,
      content: 'A bounded section of this synthetic source.',
      complete: false,
      has_more: true,
    },
    { url: `https://source${i}.example/guide` },
  );
}
completed(
  'agent',
  'spawn_agent',
  { answer: 'Synthetic agent output' },
  { context: { mode: 'research' } },
);
completed(
  'failed',
  'web_fetch',
  { error: 'Synthetic fetch failure', url: 'https://failed.example/' },
  { url: 'https://failed.example/' },
);
initialEvents.push({
  type: 'tool_call',
  name: 'get_time',
  arguments: { note: 'x'.repeat(400) },
  tool_call_id: 'pending',
});

export default function Page() {
  const [events, setEvents] = useState(initialEvents);
  const [hideTools, setHideTools] = useState(false);
  return (
    <main
      style={{ maxWidth: 720, padding: 16, margin: '32px auto', minWidth: 0 }}
    >
      <h1>Tool activity production fixture</h1>
      <section aria-label="Assistant response">
        {!hideTools && <ToolCallLog events={events} />}
        <MarkdownMessage
          content="The [first source](https://source1.example/guide#details) supports this summary. An [ordinary link](https://unreturned.example/reference) stays an ordinary link."
          sources={buildMessageCitationSources(events)}
        />
      </section>
      <div
        style={{ marginTop: 24, display: 'flex', flexWrap: 'wrap', gap: 16 }}
      >
        <button
          type="button"
          onClick={() =>
            setEvents([
              ...initialEvents,
              {
                type: 'tool_result',
                name: 'get_time',
                result: { time: '12:00 synthetic' },
                tool_call_id: 'pending',
              },
            ])
          }
        >
          Finish pending action
        </button>
        <label>
          <input
            type="checkbox"
            checked={hideTools}
            onChange={(event) => setHideTools(event.target.checked)}
          />{' '}
          Hide tool calls
        </label>
      </div>
    </main>
  );
}
