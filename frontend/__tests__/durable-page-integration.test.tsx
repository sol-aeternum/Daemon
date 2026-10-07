import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import type { DaemonMessage } from '../lib/chatMessages';
import * as auth from '../lib/auth';
import {
  getChatDraft,
  openChatDraft,
  setChatDraftInput,
  setChatDraftAttachments,
} from '../lib/chatDrafts';

const state = vi.hoisted(() => ({
  send: vi.fn(),
  stop: vi.fn(),
  replace: vi.fn(),
  currentId: null as string | null,
  setMessages: null as null | ((messages: DaemonMessage[]) => void),
  conversation: null as unknown,
  refresh: vi.fn(),
  cancelTask: vi.fn(),
  taskForKey: vi.fn(),
  taskStatus: vi.fn(),
}));
const candidate = {
  id: 'candidate',
  summary: 'Plan the release',
  prompt: 'Write a release plan with concrete acceptance tests.',
  source: { conversation_id: 'source', title: 'Release discussion' },
  expires_at: '2099-01-01T00:00:00Z',
};

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: state.replace }),
}));
vi.mock('@ai-sdk/react', async () => {
  const { useState } = await import('react');
  return {
    useChat: () => {
      const [messages, setMessages] = useState<DaemonMessage[]>([]);
      state.setMessages = setMessages;
      return {
        messages,
        setMessages,
        sendMessage: state.send,
        stop: state.stop,
        status: 'ready',
      };
    },
  };
});
vi.mock('../components/ConversationHistoryProvider', () => ({
  ConversationHistoryProvider: ({ children }: { children: ReactNode }) =>
    children,
  useConversationHistoryContext: () => ({
    currentId: state.currentId,
    conversations: [],
    getCurrentConversation: () => state.conversation,
    refreshCurrentConversation: state.refresh,
    cancelTask: state.cancelTask,
    taskForKey: state.taskForKey,
    taskStatus: state.taskStatus,
    createConversation: vi.fn(),
    updateConversation: vi.fn(),
    setConversationModel: vi.fn(),
    switchConversation: vi.fn(),
    fetchConversationById: vi.fn(),
    refreshConversations: vi.fn(),
  }),
}));
vi.mock('../components/AudioPlaybackProvider', () => ({
  AudioPlaybackProvider: ({ children }: { children: ReactNode }) => children,
}));
vi.mock('../components/WelcomeScreen', () => ({
  WelcomeScreen: ({
    composer,
    onSuggestionSelect,
  }: {
    composer: ReactNode;
    onSuggestionSelect: (suggestion: typeof candidate) => void;
  }) => (
    <div>
      {composer}
      <button onClick={() => onSuggestionSelect(candidate)}>
        Plan the release
      </button>
    </div>
  ),
}));
vi.mock('../components/ChatInputBar', () => ({
  ChatInputBar: ({
    input,
    onInputChange,
    onSubmit,
    isLoading,
    onStop,
  }: {
    input: string;
    onInputChange: (event: React.ChangeEvent<HTMLTextAreaElement>) => void;
    onSubmit: () => void;
    isLoading: boolean;
    onStop: () => void;
  }) => (
    <div>
      <textarea aria-label="Composer" value={input} onChange={onInputChange} />
      {isLoading ? (
        <button onClick={onStop}>Stop</button>
      ) : (
        <button type="button" onClick={() => onSubmit()}>
          Send
        </button>
      )}
    </div>
  ),
}));
vi.mock('../components/ConversationList', () => ({
  ConversationList: () => null,
}));
vi.mock('../components/ConversationDetails', () => ({
  ConversationDetails: () => null,
  useWideDetails: () => false,
}));
vi.mock('../components/ChatHeaderActions', () => ({
  ChatHeaderActions: () => null,
}));
vi.mock('../components/ConnectionStatus', () => ({
  ConnectionStatus: () => null,
}));
vi.mock('../components/TtsPlaybackBar', () => ({ TtsPlaybackBar: () => null }));
vi.mock('../components/TextToSpeechButton', () => ({
  TextToSpeechButton: () => null,
}));
vi.mock('../components/MarkdownMessage', () => ({ default: () => null }));
vi.mock('../hooks/useChatScroll', () => ({
  useChatScroll: () => ({
    scrollContainerRef: { current: null },
    messagesEndRef: { current: null },
    isScrolledUp: false,
    onScroll: vi.fn(),
    jumpToLatest: vi.fn(),
  }),
}));
vi.mock('react-resizable-panels', () => ({
  Group: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Panel: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Separator: () => null,
}));

import ChatPage from '../app/page';

function runningConversation(active: boolean) {
  return {
    id: 'conv-1',
    title: 'Opened on another device',
    messages: [
      { id: 'u', role: 'user', parts: [{ type: 'text', text: 'hello' }] },
      {
        id: 'a',
        role: 'assistant',
        status: active ? 'streaming' : 'complete',
        parts: [{ type: 'text', text: active ? 'Partial' : 'Final answer' }],
      },
    ],
    createdAt: '',
    updatedAt: '',
    pinned: false,
    title_locked: false,
    status: 'active',
    metadata: {},
    activeTask: active
      ? {
          id: 'task-1',
          status: 'running',
          content: 'Partial',
          cancelRequested: false,
        }
      : null,
  };
}

beforeEach(() => {
  vi.stubGlobal('localStorage', window.localStorage);
  vi.clearAllMocks();
  auth.clearLocalAuthState();
  auth.setAccessToken('fixture', Date.now() + 120_000);
  state.currentId = 'conv-1';
  state.conversation = runningConversation(true);
  state.cancelTask.mockResolvedValue('cancelled');
  state.taskForKey.mockResolvedValue(null);
  state.taskStatus.mockResolvedValue('running');
  state.refresh.mockResolvedValue(runningConversation(true));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('a durable task reopened on another device', () => {
  it('is busy and cancellable by its stored task id until the server finishes', async () => {
    render(<ChatPage />);
    const stop = await screen.findByRole('button', { name: 'Stop' });
    expect(screen.queryByRole('button', { name: 'Send' })).toBeNull();

    // Send stays blocked while the server task runs.
    fireEvent.change(screen.getByLabelText('Composer'), {
      target: { value: 'another question' },
    });
    expect(state.send).not.toHaveBeenCalled();

    fireEvent.click(stop);
    await waitFor(() =>
      expect(state.cancelTask).toHaveBeenCalledWith('task-1'),
    );
    // The server accepted the cancel but the task is still stopping: the
    // conversation stays busy rather than inviting a conflicting submission.
    expect(screen.getByRole('button', { name: 'Stop' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Send' })).toBeNull();

    // The server reaches a terminal state: the result shows, input is free.
    state.refresh.mockResolvedValue(runningConversation(false));
    await waitFor(
      () => expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy(),
      { timeout: 5000 },
    );
  });

  it('waits for the pending confirmation when Stop is pressed again', async () => {
    // Review of #467: a repeated press must not count as a stop of its own.
    state.cancelTask.mockResolvedValue('cancelling');
    render(<ChatPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Stop' }));
    await waitFor(() => expect(state.cancelTask).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    await act(async () => {});
    expect(state.cancelTask).toHaveBeenCalledTimes(1);
  });

  it('keeps only the stopping conversation busy', async () => {
    state.cancelTask.mockResolvedValue('cancelling');
    const view = render(<ChatPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Stop' }));
    await waitFor(() =>
      expect(state.cancelTask).toHaveBeenCalledWith('task-1'),
    );
    // conv-1's task is still stopping; the user opens another conversation.
    const other = {
      ...runningConversation(false),
      id: 'conv-2',
      title: 'Another chat',
    };
    state.currentId = 'conv-2';
    state.conversation = other;
    state.refresh.mockResolvedValue(other);
    view.rerender(<ChatPage />);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy(),
    );
  });

  it('withdraws Stop and reports it when cancellation is not confirmed', async () => {
    state.cancelTask.mockResolvedValue('unconfirmed');
    render(<ChatPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Stop' }));
    await waitFor(() =>
      expect(
        screen.getByText(/Stop could not be confirmed/, { exact: false }),
      ).toBeTruthy(),
    );
  });
});

describe('a submission whose outcome is unknown (#476)', () => {
  it('is resent from the restored draft under its own key', async () => {
    const { restoreHeldSubmission } = await import('../lib/chatDrafts');
    state.conversation = runningConversation(false);
    state.refresh.mockResolvedValue(runningConversation(false));
    state.send.mockResolvedValue(undefined);
    render(<ChatPage />);
    const composer = await screen.findByLabelText('Composer');
    fireEvent.change(composer, { target: { value: 'book the table' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(state.send).toHaveBeenCalledTimes(1));
    const firstKey = state.send.mock.calls[0][1].body.idempotency_key;
    expect(firstKey).toMatch(/^[0-9a-f-]{36}$/);

    // The response was lost and no task was found: the draft comes back.
    await waitFor(() =>
      expect(
        (screen.getByLabelText('Composer') as HTMLTextAreaElement).value,
      ).toBe(''),
    );
    act(() => {
      restoreHeldSubmission(firstKey);
    });
    await waitFor(() =>
      expect(
        (screen.getByLabelText('Composer') as HTMLTextAreaElement).value,
      ).toBe('book the table'),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(state.send).toHaveBeenCalledTimes(2));
    expect(state.send.mock.calls[1][1].body.idempotency_key).toBe(firstKey);
  });
});
