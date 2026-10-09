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
  acceptSubmission,
  getChatDraft,
  heldSubmission,
  heldSubmissionsReady,
  holdSubmission,
  openChatDraft,
  reloadChatDraftsForTests,
  setChatDraftInput,
  setChatDraftAttachments,
} from '../lib/chatDrafts';
import {
  setAttachmentStoreForTests,
  type AttachmentStore,
  type PersistedAttachment,
} from '../lib/draftPersistence';
import {
  pendingSubmission,
  registerSubmission,
} from '../lib/pendingSubmission';

const state = vi.hoisted(() => ({
  send: vi.fn(),
  regenerate: vi.fn(),
  stop: vi.fn(),
  replace: vi.fn(),
  currentId: null as string | null,
  setMessages: null as null | ((messages: DaemonMessage[]) => void),
  conversation: null as unknown,
  refresh: vi.fn(),
  cancelTask: vi.fn(),
  taskForKey: vi.fn(),
  taskStatus: vi.fn(),
  switchConversation: vi.fn(),
  messages: [] as DaemonMessage[],
  conversationLoadFailure: null as null | 'permanent' | 'exhausted',
  chatOptions: null as null | {
    onFinish?: (event: Record<string, unknown>) => void;
  },
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
    useChat: (options: typeof state.chatOptions) => {
      const [messages, setMessages] = useState<DaemonMessage[]>([]);
      state.setMessages = setMessages;
      state.messages = messages;
      state.chatOptions = options;
      return {
        messages,
        setMessages,
        sendMessage: state.send,
        regenerate: state.regenerate,
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
    switchConversation: state.switchConversation,
    conversationLoadFailure: state.conversationLoadFailure,
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
  ConnectionStatus: ({ onReconnect }: { onReconnect: () => void }) => (
    <button type="button" onClick={onReconnect}>
      Reconnect
    </button>
  ),
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

const storageValues = new Map<string, string>();
const testStorage: Storage = {
  get length() {
    return storageValues.size;
  },
  key: (index) => [...storageValues.keys()][index] ?? null,
  getItem: (key) => storageValues.get(key) ?? null,
  setItem: (key, value) => {
    storageValues.set(key, value);
  },
  removeItem: (key) => {
    storageValues.delete(key);
  },
  clear: () => storageValues.clear(),
};
beforeEach(() => {
  vi.stubGlobal('localStorage', testStorage);
  vi.resetAllMocks();
  auth.clearLocalAuthState();
  auth.setAccessToken('fixture', Date.now() + 120_000);
  state.currentId = 'conv-1';
  state.conversationLoadFailure = null;
  state.conversation = runningConversation(true);
  state.cancelTask.mockResolvedValue('cancelled');
  state.taskForKey.mockResolvedValue(null);
  state.taskStatus.mockResolvedValue('running');
  state.refresh.mockResolvedValue(runningConversation(true));
  state.send.mockResolvedValue(undefined);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('#477 submission reliability closeout', () => {
  beforeEach(() => {
    state.conversation = runningConversation(false);
    state.refresh.mockResolvedValue(runningConversation(false));
    state.send.mockResolvedValue(undefined);
  });
  function pending(key: string, scope: string | null = 'conv-1', age = 61_000) {
    registerSubmission(
      key,
      { scope, requestConversationId: scope, model: 'auto', provider: null },
      Date.now() - age,
    );
  }
  const task = (status = 'running') => ({
    id: 'loaded-task',
    conversationId: 'loaded-conv',
    status,
  });

  it.each(['running', 'completed'])(
    'opens the accepted new-chat %s answer discovered on Home',
    async (status) => {
      state.currentId = null;
      state.conversation = null;
      pending('home-key', null);
      state.taskForKey.mockResolvedValue(task(status));
      render(<ChatPage />);
      await waitFor(() =>
        expect(state.switchConversation).toHaveBeenCalledWith('loaded-conv'),
      );
    },
  );

  it('does not pull Home into an unrelated conversation pending key', async () => {
    state.currentId = null;
    state.conversation = null;
    pending('other-key');
    state.taskForKey.mockResolvedValue(task());
    render(<ChatPage />);
    await waitFor(() =>
      expect(state.taskForKey).toHaveBeenCalledWith('other-key'),
    );
    expect(state.switchConversation).not.toHaveBeenCalled();
  });

  it('does not apply a late Home lookup after navigation, even when back Home', async () => {
    state.currentId = null;
    state.conversation = null;
    pending('late-home', null);
    let resolve!: (value: ReturnType<typeof task>) => void;
    state.taskForKey.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    const view = render(<ChatPage />);
    await waitFor(() => expect(resolve).toBeTypeOf('function'));
    state.currentId = 'conv-1';
    state.conversation = runningConversation(false);
    view.rerender(<ChatPage />);
    // The new route starts its own reconciliation; keep it unresolved.
    state.taskForKey.mockImplementation(() => new Promise(() => {}));
    state.currentId = null;
    state.conversation = null;
    view.rerender(<ChatPage />);
    await act(async () => resolve(task()));
    expect(state.switchConversation).not.toHaveBeenCalled();
  });

  it('revisits a key that was younger than a minute on mount', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    pending('young', 'conv-1', 10_000);
    render(<ChatPage />);
    await act(async () => {});
    expect(state.taskForKey).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTimeAsync(50_000));
    expect(state.taskForKey).toHaveBeenCalledWith('young');
    expect(pendingSubmission('young')).toBeNull();
  });

  it('cancels age revisits on unmount and never settles failed lookups', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    pending('young', 'conv-1', 10_000);
    const view = render(<ChatPage />);
    await act(async () => {});
    view.unmount();
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(state.taskForKey).not.toHaveBeenCalled();
    state.taskForKey.mockResolvedValue(undefined);
    render(<ChatPage />);
    await act(async () => {});
    await act(async () => vi.advanceTimersByTimeAsync(20_000));
    expect(state.taskForKey).toHaveBeenCalledTimes(3);
    expect(pendingSubmission('young')).not.toBeNull();
  });

  it('queues a never-accepted held draft behind newer input and restores when empty', async () => {
    const scope = openChatDraft('conv-1', auth.getAuthGeneration());
    setChatDraftInput(scope, 'old request');
    holdSubmission(scope, 'held-old', 'old request', []);
    setChatDraftInput(scope, 'newer draft');
    pending('held-old');
    render(<ChatPage />);
    await waitFor(() =>
      expect(screen.getByText(/will return to the composer/)).toBeTruthy(),
    );
    const composer = screen.getByLabelText('Composer') as HTMLTextAreaElement;
    expect(composer.value).toBe('newer draft');
    fireEvent.change(composer, { target: { value: '' } });
    await waitFor(() => expect(composer.value).toBe('old request'));
  });

  it('reconnects the failed turn key rather than a newer held submission', async () => {
    render(<ChatPage />);
    fireEvent.change(await screen.findByLabelText('Composer'), {
      target: { value: 'failed turn' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(state.send).toHaveBeenCalledTimes(1));
    const key = state.send.mock.calls[0][1].body.idempotency_key;
    await waitFor(() => expect(getChatDraft('conv-1').input).toBe(''));
    const scope = openChatDraft('conv-1', auth.getAuthGeneration());
    act(() =>
      holdSubmission(scope, 'newer-key', 'unrelated newer request', []),
    );
    fireEvent.click(screen.getAllByRole('button', { name: 'Reconnect' })[0]);
    expect(state.regenerate.mock.calls[0][0].body.idempotency_key).toBe(key);
  });

  it('removes only the rejected exchange before restoring and resending it', async () => {
    render(<ChatPage />);
    fireEvent.change(await screen.findByLabelText('Composer'), {
      target: { value: 'refused prompt' },
    });
    state.send.mockImplementation(async () => {
      state.setMessages?.([
        {
          id: 'independent',
          role: 'assistant',
          parts: [{ type: 'text', text: 'older answer' }],
        },
        {
          id: 'refused-user',
          role: 'user',
          parts: [{ type: 'text', text: 'refused prompt' }],
        },
        {
          id: 'r',
          role: 'assistant',
          parts: [
            {
              type: 'data-event',
              data: {
                type: 'request_rejected',
                status: 429,
                code: 'rate_limited',
              },
            },
          ],
        },
      ]);
      await rejectedReply(429, 'rate_limited')();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() =>
      expect(
        (screen.getByLabelText('Composer') as HTMLTextAreaElement).value,
      ).toBe('refused prompt'),
    );
    expect(state.messages.map((m) => m.id)).toEqual(['independent']);
    state.send.mockResolvedValue(undefined);
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(state.send).toHaveBeenCalledTimes(2));
    expect(state.send.mock.calls[1][1].body.idempotency_key).not.toBe(
      state.send.mock.calls[0][1].body.idempotency_key,
    );
  });

  it('shows permanent conversation lookup failure with a Home action', async () => {
    state.conversation = null;
    state.conversationLoadFailure = 'permanent';
    render(<ChatPage />);
    expect(
      await screen.findByText(/This conversation is not available/),
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Go to Home' })).toBeTruthy();
  });
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

function rejectedReply(status: number, code: string) {
  // What the AI SDK does: onFinish runs inside sendMessage, before it resolves.
  return async () => {
    state.chatOptions?.onFinish?.({
      message: {
        id: 'r',
        role: 'assistant',
        parts: [
          {
            type: 'data-event',
            data: { type: 'request_rejected', status, code },
          },
        ],
      },
      isAbort: false,
      isDisconnect: false,
      isError: false,
    });
  };
}

describe('a submission whose task the server accepted (#479 review)', () => {
  it('leaves the composer before the stream ends, so a reload cannot resend it', async () => {
    state.conversation = runningConversation(false);
    state.refresh.mockResolvedValue(runningConversation(false));
    // The task is named, then the connection drops; sendMessage has not
    // resolved, so the composer was not cleared on its way out.
    state.send.mockImplementation(() => {
      state.chatOptions?.onFinish?.({
        message: {
          id: 'r',
          role: 'assistant',
          parts: [
            {
              type: 'data-event',
              data: { type: 'task', task_id: 'task-9', status: 'running' },
            },
          ],
        },
        isAbort: false,
        isDisconnect: true,
        isError: false,
      });
      return new Promise(() => {});
    });
    render(<ChatPage />);
    const composer = await screen.findByLabelText('Composer');
    fireEvent.change(composer, { target: { value: 'book the table' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(state.send).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(getChatDraft('conv-1').input).toBe(''));
  });
});

describe('identical text typed again (#479 review)', () => {
  it('is a new request, not a resend of a held submission', async () => {
    state.conversation = runningConversation(false);
    state.refresh.mockResolvedValue(runningConversation(false));
    state.send.mockResolvedValue(undefined);
    render(<ChatPage />);
    const composer = await screen.findByLabelText('Composer');
    fireEvent.change(composer, { target: { value: 'same words' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(state.send).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(
        (screen.getByLabelText('Composer') as HTMLTextAreaElement).value,
      ).toBe(''),
    );
    // The first is still unresolved (held), but this draft was typed anew.
    fireEvent.change(screen.getByLabelText('Composer'), {
      target: { value: 'same words' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(state.send).toHaveBeenCalledTimes(2));
    expect(state.send.mock.calls[1][1].body.idempotency_key).not.toBe(
      state.send.mock.calls[0][1].body.idempotency_key,
    );
  });
});

describe('a submission refused before acceptance (#476, #479 review)', () => {
  beforeEach(() => {
    state.conversation = runningConversation(false);
    state.refresh.mockResolvedValue(runningConversation(false));
  });

  const composer = () =>
    screen.getByLabelText('Composer') as HTMLTextAreaElement;

  it('returns to the composer when rate limited, with its key settled', async () => {
    const { pendingSubmission } = await import('../lib/pendingSubmission');
    state.send.mockImplementation(rejectedReply(429, 'rate_limited'));
    render(<ChatPage />);
    fireEvent.change(await screen.findByLabelText('Composer'), {
      target: { value: 'try this' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(composer().value).toBe('try this'));
    const key = state.send.mock.calls[0][1].body.idempotency_key;
    expect(pendingSubmission(key)).toBeNull();
  });

  it('is kept, not lost, while the composer holds a newer draft', async () => {
    state.send.mockResolvedValue(undefined);
    render(<ChatPage />);
    fireEvent.change(await screen.findByLabelText('Composer'), {
      target: { value: 'first message' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(composer().value).toBe(''));
    fireEvent.change(composer(), { target: { value: 'next draft' } });
    // The first send is refused only now (not during a composer send).
    await act(async () => rejectedReply(403, 'route_unavailable')());
    await waitFor(() =>
      expect(screen.getByText(/will return to the composer/)).toBeTruthy(),
    );
    expect(composer().value).toBe('next draft');
    fireEvent.change(composer(), { target: { value: '' } });
    await waitFor(() => expect(composer().value).toBe('first message'));
  });

  it('is resolved by key, not released, when it was a resend', async () => {
    const { heldSubmission, restoreHeldSubmission } =
      await import('../lib/chatDrafts');
    state.send.mockResolvedValueOnce(undefined);
    render(<ChatPage />);
    fireEvent.change(await screen.findByLabelText('Composer'), {
      target: { value: 'book it' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(state.send).toHaveBeenCalledTimes(1));
    const key = state.send.mock.calls[0][1].body.idempotency_key;
    await waitFor(() => expect(composer().value).toBe(''));
    act(() => {
      restoreHeldSubmission(key);
    });
    await waitFor(() => expect(composer().value).toBe('book it'));
    // The resend is refused by an outer limit before the replay check.
    state.send.mockImplementationOnce(rejectedReply(429, 'rate_limited'));
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(state.taskForKey).toHaveBeenCalledWith(key));
    // Its held draft is not released as if nothing existed for the key.
    expect(heldSubmission(key)).toBeDefined();
  });
});

class MemoryAttachmentStore implements AttachmentStore {
  records = new Map<string, File>();
  gate: Promise<void> | null = null;
  async put({ id, file }: PersistedAttachment) {
    this.records.set(id, file);
  }
  async getMany(ids: string[]) {
    if (this.gate) await this.gate;
    return ids.flatMap((id) => {
      const file = this.records.get(id);
      return file ? [{ id, file }] : [];
    });
  }
  async delete(ids: string[]) {
    for (const id of ids) this.records.delete(id);
  }
  async clear() {
    this.records.clear();
  }
  async deleteSavedBefore() {}
}

describe('a held draft whose files are missing (#479 source review)', () => {
  let files: MemoryAttachmentStore;
  const composerValue = () =>
    (screen.getByLabelText('Composer') as HTMLTextAreaElement).value;

  beforeEach(async () => {
    state.conversation = runningConversation(false);
    state.refresh.mockResolvedValue(runningConversation(false));
    state.send.mockResolvedValue(undefined);
    files = new MemoryAttachmentStore();
    setAttachmentStoreForTests(files);
    await act(() => reloadChatDraftsForTests());
    // A sent (or put back) draft with one file, held under its key.
    const scope = openChatDraft('conv-1', auth.getAuthGeneration());
    const attachments = [
      { id: 'att-1', file: new File(['bytes'], 'notes.txt') },
    ];
    setChatDraftInput(scope, 'Summarise the notes');
    setChatDraftAttachments(scope, attachments);
    holdSubmission(scope, 'key-1', 'Summarise the notes', attachments);
    await act(async () => new Promise((r) => setTimeout(r, 0)));
  });
  afterEach(() => setAttachmentStoreForTests(undefined));

  it('is not dispatched after its file expired; its key is resolved instead', async () => {
    files.records.clear(); // the stored file expired
    await act(() => reloadChatDraftsForTests());
    await act(() => heldSubmissionsReady());
    expect(getChatDraft('conv-1').restoredKey).toBe('key-1');
    state.taskForKey.mockImplementation(() => new Promise(() => {}));
    render(<ChatPage />);
    await waitFor(() => expect(composerValue()).toBe('Summarise the notes'));
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() =>
      expect(screen.getByText(/cannot be sent again as it was/)).toBeTruthy(),
    );
    expect(state.send).not.toHaveBeenCalled();
    expect(state.taskForKey).toHaveBeenCalledWith('key-1');
    // The held evidence is untouched: still one file under its key.
    expect(heldSubmission('key-1')?.attachmentCount).toBe(1);
    expect(composerValue()).toBe('Summarise the notes');
  });

  it('is not retried under its key without its files', async () => {
    // A fresh page (after a reload) no longer has the turn's files to resend.
    await act(() => reloadChatDraftsForTests());
    await act(() => heldSubmissionsReady());
    state.taskForKey.mockImplementation(() => new Promise(() => {}));
    render(<ChatPage />);
    await waitFor(() => expect(composerValue()).toBe('Summarise the notes'));
    fireEvent.click(screen.getAllByRole('button', { name: 'Reconnect' })[0]);
    await waitFor(() =>
      expect(screen.getByText(/cannot be retried with its files/)).toBeTruthy(),
    );
    expect(state.regenerate).not.toHaveBeenCalled();
    expect(state.taskForKey).toHaveBeenCalledWith('key-1');
    expect(heldSubmission('key-1')?.attachmentCount).toBe(1);
  });

  it('is not resent under a new key when its key is resolved while its files are read', async () => {
    // The held draft's file is still being read when reconciliation finds
    // that the server already accepted its key.
    let finishRead!: (text: string) => void;
    const file = new File(['bytes'], 'notes.txt', { type: 'text/plain' });
    Object.defineProperty(file, 'text', {
      value: () =>
        new Promise<string>((resolve) => {
          finishRead = resolve;
        }),
    });
    await act(() => reloadChatDraftsForTests());
    const scope = openChatDraft('conv-1', auth.getAuthGeneration());
    const attachments = [{ id: 'att-2', file }];
    setChatDraftInput(scope, 'Read this file');
    setChatDraftAttachments(scope, attachments);
    holdSubmission(scope, 'key-2', 'Read this file', attachments);
    render(<ChatPage />);
    await waitFor(() => expect(composerValue()).toBe('Read this file'));
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(finishRead).toBeTypeOf('function'));
    act(() => acceptSubmission('key-2'));
    await act(async () => finishRead('contents'));
    await act(async () => new Promise((r) => setTimeout(r, 0)));
    expect(state.send).not.toHaveBeenCalled();
    expect(getChatDraft('conv-1').input).toBe('');
  });

  it('is not dispatched before its files have loaded', async () => {
    let open!: () => void;
    files.gate = new Promise<void>((resolve) => {
      open = resolve;
    });
    await act(() => reloadChatDraftsForTests());
    render(<ChatPage />);
    await waitFor(() => expect(composerValue()).toBe('Summarise the notes'));
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByText(/still loading/)).toBeTruthy());
    expect(state.send).not.toHaveBeenCalled();
    open();
    await act(() => heldSubmissionsReady());
    // With its file back it resends exactly, under its own key.
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(state.send).toHaveBeenCalledTimes(1));
    expect(state.send.mock.calls[0][1].body.idempotency_key).toBe('key-1');
    expect(state.send.mock.calls[0][1].body.attachments).toHaveLength(1);
  });
});
