import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useStopGeneration } from '../hooks/useStopGeneration';

type Message = { id: string; role: string; content: string };

function Harness({
  messages,
  stop,
  archiveEvents,
  conversationId,
}: {
  messages: Message[];
  stop: () => void;
  archiveEvents: (messageId: string) => void;
  conversationId: string | null;
}) {
  const { stoppedMessageIds, stopGeneration, assignConversationId } =
    useStopGeneration({
      messages,
      stop,
      archiveEvents,
      conversationId,
    });

  return (
    <>
      {messages.map((message) => (
        <div key={message.id}>
          <span>{message.content}</span>
          {stoppedMessageIds.has(message.id) && <span>(stopped)</span>}
        </div>
      ))}
      <button type="button" onClick={stopGeneration}>
        Stop
      </button>
      <button
        type="button"
        onClick={() => assignConversationId('conv-assigned')}
      >
        Assign conversation
      </button>
    </>
  );
}

describe('useStopGeneration', () => {
  it('invokes stop and preserves and labels the latest partial assistant message', () => {
    const stop = vi.fn();
    const archiveEvents = vi.fn();
    render(
      <Harness
        messages={[
          { id: 'partial', role: 'assistant', content: 'Partial answer' },
        ]}
        stop={stop}
        archiveEvents={archiveEvents}
        conversationId="conv-1"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));

    expect(stop).toHaveBeenCalledTimes(1);
    expect(archiveEvents).toHaveBeenCalledWith('partial');
    expect(screen.getByText('Partial answer')).not.toBeNull();
    expect(screen.getByText('(stopped)')).not.toBeNull();
  });

  it('retains a stopped marker when a new chat receives its backend ID', () => {
    const stop = vi.fn();
    const archiveEvents = vi.fn();
    const messages = [
      { id: 'partial', role: 'assistant', content: 'Partial answer' },
    ];
    const { rerender } = render(
      <Harness
        messages={messages}
        stop={stop}
        archiveEvents={archiveEvents}
        conversationId={null}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    expect(screen.getByText('(stopped)')).not.toBeNull();

    // The conversation SSE event arrives before router.replace updates the
    // URL-backed currentId. The marker must be promoted immediately and then
    // remain visible after the router transition completes.
    fireEvent.click(
      screen.getByRole('button', { name: 'Assign conversation' }),
    );
    expect(screen.getByText('(stopped)')).not.toBeNull();

    rerender(
      <Harness
        messages={messages}
        stop={stop}
        archiveEvents={archiveEvents}
        conversationId="conv-assigned"
      />,
    );
    expect(screen.getByText('(stopped)')).not.toBeNull();
  });

  it('does not mark an older assistant message when the latest message is from the user', () => {
    const stop = vi.fn();
    const archiveEvents = vi.fn();
    render(
      <Harness
        messages={[
          { id: 'old', role: 'assistant', content: 'Complete answer' },
          { id: 'user', role: 'user', content: 'New prompt' },
        ]}
        stop={stop}
        archiveEvents={archiveEvents}
        conversationId="conv-2"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));

    expect(stop).toHaveBeenCalledTimes(1);
    expect(archiveEvents).not.toHaveBeenCalled();
    expect(screen.queryByText('(stopped)')).toBeNull();
  });

  it('preserves stopped markers when the messages array is swapped to a different conversation', () => {
    const stop = vi.fn();
    const archiveEvents = vi.fn();
    function SwitchHarness({ conversation }: { conversation: 'a' | 'b' }) {
      const messagesByConversation: Record<'a' | 'b', Message[]> = {
        a: [{ id: 'a_partial', role: 'assistant', content: 'A partial' }],
        b: [{ id: 'b_partial', role: 'assistant', content: 'B partial' }],
      };
      return (
        <Harness
          messages={messagesByConversation[conversation]}
          stop={stop}
          archiveEvents={archiveEvents}
          conversationId={`conv-${conversation}`}
        />
      );
    }
    const { rerender } = render(<SwitchHarness conversation="a" />);
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    expect(screen.getByText('(stopped)')).not.toBeNull();

    // Switch to conversation B — A's stopped marker should not appear on B.
    rerender(<SwitchHarness conversation="b" />);
    expect(screen.queryByText('(stopped)')).toBeNull();

    // Switch back to A — A's stopped marker should still be present
    // because the hook scopes markers per-conversation and retains them
    // when the conversation re-mounts.
    rerender(<SwitchHarness conversation="a" />);
    expect(screen.getByText('(stopped)')).not.toBeNull();
  });

  it('preserves stopped markers across New Chat and a return to the original conversation', () => {
    const stop = vi.fn();
    const archiveEvents = vi.fn();
    const { rerender } = render(
      <Harness
        messages={[
          { id: 'a_partial', role: 'assistant', content: 'A partial' },
        ]}
        stop={stop}
        archiveEvents={archiveEvents}
        conversationId="conv-a"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    expect(screen.getByText('(stopped)')).not.toBeNull();

    // Simulate `handleNewChat`: the active conversation becomes `null` and
    // a fresh "no ID yet" conversation mounts. The original conversation's
    // markers must NOT be wiped when we later re-mount it.
    rerender(
      <Harness
        messages={[]}
        stop={stop}
        archiveEvents={archiveEvents}
        conversationId={null}
      />,
    );

    // Reopen the original conversation.
    rerender(
      <Harness
        messages={[
          { id: 'a_partial', role: 'assistant', content: 'A partial' },
        ]}
        stop={stop}
        archiveEvents={archiveEvents}
        conversationId="conv-a"
      />,
    );
    expect(screen.getByText('(stopped)')).not.toBeNull();
  });
});
