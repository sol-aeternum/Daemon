import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StrictMode, type ReactNode } from 'react';
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
  setChatDraftControls,
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
  applyMessages: vi.fn(),
  replace: vi.fn(),
  isCurrentConversation: (_conversation: unknown): boolean => true,
  isHistoryScopeCurrent: (_scope: string): boolean => true,
  loadOlder: vi.fn(),
  refreshHistory: vi.fn(),
  preservePrepend: vi.fn(),
  history: {
    scope: 'test-scope',
    messages: [] as DaemonMessage[],
    hasOlder: false,
    loadingOlder: false,
    syncing: false,
    error: false,
    incomplete: false,
  },
  currentId: null as string | null,
  setMessages: null as null | ((messages: DaemonMessage[]) => void),
  conversation: null as unknown,
  refresh: vi.fn(),
  cancelTask: vi.fn(),
  taskForKey: vi.fn(),
  taskStatus: vi.fn(),
  switchConversation: vi.fn(),
  messages: [] as DaemonMessage[],
  sdkError: undefined as Error | undefined,
  sdkStatus: 'ready' as 'ready' | 'streaming' | 'submitted' | 'error',
  conversationLoadFailure: null as null | 'permanent' | 'exhausted',
  chatOptions: null as null | {
    onFinish?: (event: Record<string, unknown>) => void;
    onError?: (error: Error) => void;
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
  const { useState, useCallback } = await import('react');
  return {
    useChat: (options: typeof state.chatOptions) => {
      const [messages, setSDKMessages] = useState<DaemonMessage[]>([]);
      const setMessages = useCallback(
        (
          next:
            | DaemonMessage[]
            | ((previous: DaemonMessage[]) => DaemonMessage[]),
        ) => {
          state.applyMessages(next);
          setSDKMessages(next);
        },
        [setSDKMessages],
      );
      state.setMessages = setMessages;
      state.messages = messages;
      state.chatOptions = options;
      return {
        messages,
        setMessages,
        sendMessage: state.send,
        regenerate: state.regenerate,
        stop: state.stop,
        status: state.sdkStatus,
        error: state.sdkError,
      };
    },
  };
});
vi.mock('../components/ConversationHistoryProvider', () => ({
  ConversationHistoryProvider: ({ children }: { children: ReactNode }) =>
    children,
  useConversationHistoryContext: () => {
    // Faithfully retain the rendered snapshot: refresh can update the owner
    // before this getter is replaced by a committed provider render.
    const renderedConversation = state.conversation;
    return {
      currentId: state.currentId,
      conversations: [],
      getCurrentConversation: () => renderedConversation,
      refreshCurrentConversation: state.refresh,
      messageHistory: {
        ...state.history,
        messages: state.history.messages.length
          ? state.history.messages
          : ((state.conversation as { messages?: DaemonMessage[] } | null)
              ?.messages ?? []),
      },
      loadOlderMessages: state.loadOlder,
      refreshMessageHistory: state.refreshHistory,
      isCurrentConversation: state.isCurrentConversation,
      isHistoryScopeCurrent: state.isHistoryScopeCurrent,
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
    };
  },
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
  ConnectionStatus: ({
    onReconnect,
    status,
  }: {
    onReconnect: () => void;
    status: string;
  }) => (
    <button type="button" data-connection-status={status} onClick={onReconnect}>
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
    preservePrepend: state.preservePrepend,
  }),
}));
vi.mock('react-resizable-panels', () => ({
  Group: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Panel: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Separator: () => null,
}));

import ChatPage from '../app/page';
import {
  FIXTURE_AUTH_EPOCH,
  installAuthCookieTestEnvironment,
} from './helpers/authCookieEnvironment';

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
  vi.stubGlobal('matchMedia', () => ({
    matches: true,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
  // Real auth/coordinator modules need usable nonsecret shared metadata and a
  // qualified navigator (FIFO Web Locks fixture) before manual token binding;
  // the environment is installed after resetAllMocks so its FIFO locks fixture
  // keeps its implementation.
  installAuthCookieTestEnvironment(testStorage);
  auth.clearLocalAuthState();
  auth.setAccessToken('fixture', Date.now() + 120_000, FIXTURE_AUTH_EPOCH);
  state.currentId = 'conv-1';
  state.history = {
    scope: 'test-scope',
    messages: [],
    hasOlder: false,
    loadingOlder: false,
    syncing: false,
    error: false,
    incomplete: false,
  };
  state.conversationLoadFailure = null;
  state.sdkError = undefined;
  state.sdkStatus = 'ready';
  state.isCurrentConversation = () => true;
  state.isHistoryScopeCurrent = (scope) => scope === state.history.scope;
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

describe('#230 display-only history projection', () => {
  it('keeps an errored transport disconnected when the SDK invokes onFinish after onError', async () => {
    state.conversation = runningConversation(false);
    state.send.mockImplementation(async () => {
      state.chatOptions?.onError?.(new Error('Synthetic transport failure'));
      state.chatOptions?.onFinish?.({
        message: { id: 'failed-reply', role: 'assistant', parts: [] },
        isAbort: false,
        isDisconnect: false,
        isError: true,
      });
    });
    render(<ChatPage />);
    fireEvent.change(await screen.findByLabelText('Composer'), {
      target: { value: 'synthetic status check' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(state.send).toHaveBeenCalledOnce());
    for (const control of screen.getAllByRole('button', { name: 'Reconnect' }))
      expect(control.getAttribute('data-connection-status')).toBe(
        'disconnected',
      );
  });

  it('adopts an authoritative saved tail after an errored composer send finally settles', async () => {
    state.conversation = runningConversation(false);
    let finishSend!: () => void;
    state.send.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finishSend = resolve;
        }),
    );
    state.taskForKey.mockResolvedValue({
      id: 'task-recovered',
      conversationId: 'conv-1',
      status: 'completed',
    });
    const fresh = {
      ...runningConversation(false),
      messages: [
        {
          id: 'saved-recovery',
          role: 'assistant',
          parts: [{ type: 'text', text: 'Synthetic authoritative result' }],
        },
      ] as DaemonMessage[],
      latestTask: { id: 'task-recovered', status: 'completed' },
    };
    state.refresh.mockImplementation(async () => {
      state.conversation = fresh;
      return fresh;
    });
    const view = render(<ChatPage />);
    fireEvent.change(await screen.findByLabelText('Composer'), {
      target: { value: 'synthetic recovery request' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(state.send).toHaveBeenCalledOnce());
    await act(async () => {
      state.setMessages?.([
        {
          id: 'local-partial',
          role: 'assistant',
          parts: [{ type: 'text', text: 'Synthetic local partial' }],
        },
      ]);
      state.sdkError = new Error('Synthetic dropped stream');
      state.chatOptions?.onError?.(state.sdkError);
    });
    await waitFor(() => expect(state.refresh).toHaveBeenCalledOnce());
    view.rerender(<ChatPage />);
    // The saved result arrived before sendMessage's finally: do not overwrite
    // the local turn while its composer work is still in flight.
    expect(state.messages.map((message) => message.id)).toEqual([
      'local-partial',
    ]);
    await act(async () => {
      finishSend();
    });
    // A persistent SDK error intentionally blocks generic idle adoption. Task
    // authority must survive the earlier refusal and flush without another poll.
    expect(state.messages).toBe(fresh.messages);
  });

  it('does not render or regenerate another route’s SDK tail while selected detail is loading', async () => {
    state.conversation = runningConversation(false);
    const view = render(<ChatPage />);
    await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(2));
    const previous = state.messages;
    state.currentId = 'another-conversation';
    state.conversation = null;
    view.rerender(<ChatPage />);
    expect(screen.queryAllByRole('article')).toHaveLength(0);
    expect(state.messages).toBe(previous);
    fireEvent.click(screen.getAllByRole('button', { name: 'Reconnect' })[0]);
    expect(state.regenerate).not.toHaveBeenCalled();
  });

  it('prepends older persisted messages without changing SDK send/regenerate state or losing focus', async () => {
    const saved = (n: number): DaemonMessage => ({
      id: `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`,
      role: n % 2 ? 'user' : 'assistant',
      created_at: '2026-10-28T00:00:00.123456Z',
      parts: [{ type: 'text', text: `Synthetic saved message ${n}` }],
    });
    const tail = [saved(3), saved(4)];
    state.conversation = { ...runningConversation(false), messages: tail };
    state.history = { ...state.history, messages: tail, hasOlder: true };
    state.loadOlder.mockImplementation(async () => {
      state.history = {
        ...state.history,
        messages: [saved(1), saved(2), ...tail],
        hasOlder: false,
      };
    });
    const view = render(<ChatPage />);
    const older = await screen.findByRole('button', {
      name: 'Load older messages',
    });
    await waitFor(() => expect(state.messages).toHaveLength(2));
    const sdk = state.messages;
    const payload = JSON.stringify(sdk);
    older.focus();
    await act(async () => fireEvent.click(older));
    view.rerender(<ChatPage />);
    expect(screen.getAllByRole('article')).toHaveLength(4);
    expect(state.preservePrepend).toHaveBeenCalledOnce();
    expect(state.messages).toBe(sdk);
    expect(JSON.stringify(state.messages)).toBe(payload);
    expect(screen.getByRole('button', { name: 'No earlier messages' })).toBe(
      older,
    );
    expect(document.activeElement).toBe(older);
    expect(older.getAttribute('aria-disabled')).toBe('true');
    fireEvent.click(older);
    expect(state.loadOlder).toHaveBeenCalledOnce();
    fireEvent.click(screen.getAllByRole('button', { name: 'Reconnect' })[0]);
    expect(state.regenerate).toHaveBeenCalledOnce();
    expect(state.messages).toBe(sdk);
  });
});

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function pendingSavedRecovery(strict = false) {
  const initial = runningConversation(false);
  const fresh = {
    ...initial,
    messages: [
      {
        id: 'saved-recovery',
        role: 'assistant',
        parts: [{ type: 'text', text: 'Synthetic authoritative result' }],
      },
    ] as DaemonMessage[],
    latestTask: { id: 'task-recovered', status: 'completed' },
  };
  let ownerTail: unknown = initial;
  state.conversation = initial;
  state.isCurrentConversation = (conversation) => conversation === ownerTail;
  const send = deferred<void>();
  const response = deferred<typeof fresh>();
  state.send.mockReturnValue(send.promise);
  state.taskForKey.mockResolvedValue({
    id: 'task-recovered',
    conversationId: 'conv-1',
    status: 'completed',
  });
  state.refresh.mockImplementation(async () => {
    const value = await response.promise;
    ownerTail = value;
    return value;
  });
  const wrap = (child: ReactNode) =>
    strict ? <StrictMode>{child}</StrictMode> : child;
  const view = render(wrap(<ChatPage />));
  fireEvent.change(await screen.findByLabelText('Composer'), {
    target: { value: 'synthetic recovery request' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  await waitFor(() => expect(state.send).toHaveBeenCalledOnce());
  await act(async () => {
    state.setMessages?.([
      {
        id: 'local-partial',
        role: 'assistant',
        parts: [{ type: 'text', text: 'Synthetic local partial' }],
      },
    ]);
    state.sdkError = new Error('Synthetic dropped stream');
    state.chatOptions?.onError?.(state.sdkError);
  });
  await waitFor(() => expect(state.refresh).toHaveBeenCalledOnce());
  return {
    ...view,
    rerender: (child: ReactNode) => view.rerender(wrap(child)),
    fresh,
    send,
    response,
    publish: (value: unknown = fresh) => {
      ownerTail = value;
      state.conversation = value;
      view.rerender(wrap(<ChatPage />));
    },
  };
}

describe('#230 deferred saved-tail authority', () => {
  const expectLocal = () =>
    expect(state.messages.map((message) => message.id)).toEqual([
      'local-partial',
    ]);

  it.each([false, true])(
    'retains authority across stale/null getters until commit without refetching (StrictMode=%s)',
    async (strict) => {
      const recovery = await pendingSavedRecovery(strict);
      await act(async () => {
        recovery.response.resolve(recovery.fresh);
      });
      await act(async () => {
        recovery.send.resolve();
      });
      expectLocal(); // owner has fresh, committed getter still has initial
      recovery.publish(null);
      expectLocal();
      recovery.publish();
      expect(state.messages).toBe(recovery.fresh.messages);
      expect(state.refresh).toHaveBeenCalledOnce();
    },
  );

  it('also flushes when the provider commits before the authoritative read returns', async () => {
    const recovery = await pendingSavedRecovery();
    recovery.publish();
    await act(async () => {
      recovery.send.resolve();
    });
    expectLocal(); // not yet qualified by the returned task-bound read
    await act(async () => {
      recovery.response.resolve(recovery.fresh);
    });
    expect(state.messages).toBe(recovery.fresh.messages);
    expect(state.refresh).toHaveBeenCalledOnce();
  });

  it('waits for committed SDK completion after composer completion even while error remains', async () => {
    const recovery = await pendingSavedRecovery();
    await act(async () => {
      recovery.response.resolve(recovery.fresh);
    });
    state.sdkStatus = 'streaming';
    recovery.publish();
    await act(async () => {
      recovery.send.resolve();
    });
    expectLocal();
    state.sdkStatus = 'error';
    recovery.rerender(<ChatPage />);
    expect(state.sdkError).toBeDefined();
    expect(state.messages).toBe(recovery.fresh.messages);
  });

  it('waits for Stop confirmation after composer completion', async () => {
    const recovery = await pendingSavedRecovery();
    const stop = deferred<'cancelled'>();
    state.cancelTask.mockReturnValue(stop.promise);
    state.sdkStatus = 'streaming';
    // DurableStop identifies the running task from the SDK tail, not history.
    act(() =>
      state.setMessages?.([
        {
          id: 'local-partial',
          role: 'assistant',
          parts: [
            { type: 'text', text: 'Synthetic local partial' },
            {
              type: 'data-event',
              data: {
                type: 'task',
                task_id: 'task-recovered',
                status: 'running',
              },
            },
          ],
        },
      ]),
    );
    recovery.rerender(<ChatPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    await waitFor(() => expect(state.cancelTask).toHaveBeenCalledOnce());
    await act(async () => {
      recovery.response.resolve(recovery.fresh);
    });
    state.sdkStatus = 'error';
    recovery.publish();
    await act(async () => {
      recovery.send.resolve();
    });
    expectLocal();
    await act(async () => {
      stop.resolve('cancelled');
    });
    expect(state.messages).toBe(recovery.fresh.messages);
  });

  it('rejects a deferred old response after a newer same-conversation send starts', async () => {
    const recovery = await pendingSavedRecovery();
    await act(async () => {
      recovery.send.resolve();
    });
    fireEvent.click(screen.getAllByRole('button', { name: 'Reconnect' })[0]);
    expect(state.regenerate).toHaveBeenCalledOnce();
    await act(async () => {
      recovery.response.resolve(recovery.fresh);
    });
    recovery.publish();
    expectLocal();
  });

  it('invalidates queued authority on a newer send before the provider commits', async () => {
    const recovery = await pendingSavedRecovery();
    await act(async () => {
      recovery.response.resolve(recovery.fresh);
      recovery.send.resolve();
    });
    fireEvent.click(screen.getAllByRole('button', { name: 'Reconnect' })[0]);
    expect(state.regenerate).toHaveBeenCalledOnce();
    recovery.publish();
    expectLocal();
  });

  it('invalidates pending authority across A-B-A even when the URL returns to the same ID', async () => {
    const recovery = await pendingSavedRecovery();
    await act(async () => {
      recovery.response.resolve(recovery.fresh);
    });
    state.currentId = 'conv-2';
    state.history = { ...state.history, scope: 'selection-B' };
    recovery.publish(null);
    state.currentId = 'conv-1';
    state.history = { ...state.history, scope: 'selection-A2' };
    recovery.publish();
    await act(async () => {
      recovery.send.resolve();
    });
    expectLocal();
  });

  it('discards old-task authority rather than broadening it to a newer task', async () => {
    const recovery = await pendingSavedRecovery();
    await act(async () => {
      recovery.response.resolve(recovery.fresh);
    });
    const other = {
      ...recovery.fresh,
      latestTask: { id: 'different-task', status: 'completed' },
    };
    recovery.publish(other);
    await act(async () => {
      recovery.send.resolve();
    });
    expectLocal();
    recovery.publish(); // old matching task reappearing cannot revive consumed authority
    expectLocal();
    expect(state.refresh).toHaveBeenCalledOnce();
  });

  it('retains authority when tail ownership changes between the getter and adoption guard', async () => {
    const recovery = await pendingSavedRecovery();
    await act(async () => {
      recovery.response.resolve(recovery.fresh);
    });
    recovery.publish();
    const current = state.isCurrentConversation;
    let first = true;
    state.isCurrentConversation = (conversation) => {
      if (!first) return false;
      first = false;
      return current(conversation);
    };
    recovery.rerender(<ChatPage />);
    first = true; // next flush sees a valid getter, then loses ownership at the setter
    await act(async () => {
      recovery.send.resolve();
    });
    expectLocal();
    state.isCurrentConversation = current;
    recovery.rerender(<ChatPage />);
    expect(state.messages).toBe(recovery.fresh.messages);
  });

  it.each(['before-response', 'after-response'] as const)(
    'cannot install or flush authority after unmount at %s',
    async (when) => {
      const recovery = await pendingSavedRecovery();
      if (when === 'after-response')
        await act(async () => {
          recovery.response.resolve(recovery.fresh);
        });
      recovery.unmount();
      const prior = state.messages;
      const applies = state.applyMessages.mock.calls.length;
      await act(async () => {
        recovery.response.resolve(recovery.fresh);
        recovery.send.resolve();
      });
      expect(state.messages).toBe(prior);
      expect(state.applyMessages).toHaveBeenCalledTimes(applies);
      expect(state.refresh).toHaveBeenCalledOnce();
    },
  );
});

describe('#477 submission reliability closeout', () => {
  beforeEach(() => {
    state.conversation = runningConversation(false);
    state.refresh.mockResolvedValue(runningConversation(false));
    state.send.mockResolvedValue(undefined);
  });
  it('warns before a deliberate new request after quarantine and never resumes the old task', async () => {
    const conversation = runningConversation(false);
    state.conversation = {
      ...conversation,
      messages: [
        conversation.messages[0],
        {
          ...conversation.messages[1],
          metadata: { terminal_reason: 'inference_dispatch_unresolved' },
        },
      ],
    };
    render(<ChatPage />);
    expect(
      await screen.findByText(
        /Starting a new request may overlap with provider work or charges/,
      ),
    ).toBeTruthy();
    expect(screen.queryByRole('button', { name: /resume/i })).toBeNull();
    expect(state.send).not.toHaveBeenCalled();
    expect(state.regenerate).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole('textbox', { name: 'Composer' }), {
      target: { value: 'A deliberately new request' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(state.send).toHaveBeenCalledTimes(1));
    expect(state.send.mock.calls[0][1].body.idempotency_key).toBeTypeOf(
      'string',
    );
    expect(state.regenerate).not.toHaveBeenCalled();
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

  it('explicitly sends Balanced + Auto for a new turn', async () => {
    render(<ChatPage />);
    fireEvent.change(screen.getByLabelText('Composer'), {
      target: { value: 'A new question' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(state.send).toHaveBeenCalledTimes(1));
    expect(state.send.mock.calls[0][1].body).toMatchObject({
      answer_detail: 'balanced',
      research_effort: 'auto',
    });
  });

  it('snapshots controls before file serialization and preserves changed-back draft on late hold/ack', async () => {
    const scope = openChatDraft('conv-1', auth.getAuthGeneration());
    let release!: (text: string) => void;
    const file = new File(['evidence'], 'notes.txt', { type: 'text/plain' });
    Object.defineProperty(file, 'text', {
      value: vi.fn(
        () =>
          new Promise<string>((resolve) => {
            release = resolve;
          }),
      ),
    });
    const controls = {
      answer_detail: 'brief',
      research_effort: 'research',
    } as const;
    act(() => {
      setChatDraftInput(scope, 'Check the notes');
      setChatDraftAttachments(scope, [{ id: 'notes', file }]);
      setChatDraftControls(scope, controls);
    });
    render(<ChatPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(release).toBeTypeOf('function'));
    act(() =>
      setChatDraftControls(scope, { ...controls, answer_detail: 'detailed' }),
    );
    act(() => setChatDraftControls(scope, controls));
    await act(async () => release('evidence'));
    await waitFor(() => expect(state.send).toHaveBeenCalledTimes(1));
    const first = state.send.mock.calls[0][1].body;
    expect(first).toMatchObject(controls);
    expect(getChatDraft('conv-1').restoredKey).toBeUndefined();
    expect(getChatDraft('conv-1').input).toBe('Check the notes');
    expect(heldSubmission(first.idempotency_key)?.controls).toEqual(controls);
    act(() => acceptSubmission(first.idempotency_key));
    expect(getChatDraft('conv-1').input).toBe('Check the notes');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await act(async () => release('evidence'));
    await waitFor(() => expect(state.send).toHaveBeenCalledTimes(2));
    expect(state.send.mock.calls[1][1].body.idempotency_key).not.toBe(
      first.idempotency_key,
    );
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
    expect(heldSubmission('held-old')).toBeDefined();
    expect(pendingSubmission('held-old')).not.toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(state.send).toHaveBeenCalledTimes(1));
    expect(state.send.mock.calls[0][1].body.idempotency_key).toBe('held-old');
  });

  it.each(['conv-1', null])(
    'keeps a queued uncertain %s draft and its files under the same key after reload and late acceptance',
    async (conversationId) => {
      const files = new MemoryAttachmentStore();
      setAttachmentStoreForTests(files);
      try {
        state.currentId = conversationId;
        if (conversationId === null) state.conversation = null;
        const scope = openChatDraft(conversationId, auth.getAuthGeneration());
        const file = new File(['exact bytes'], 'notes.txt', {
          type: 'text/plain',
        });
        Object.defineProperty(file, 'text', {
          value: async () => 'exact bytes',
        });
        const attachments = [{ id: 'queued-file', file }];
        setChatDraftInput(scope, 'read my notes');
        setChatDraftAttachments(scope, attachments);
        holdSubmission(scope, 'queued-files-key', 'read my notes', attachments);
        setChatDraftInput(scope, 'newer draft');
        setChatDraftAttachments(scope, []);
        pending('queued-files-key', conversationId);
        await act(() => reloadChatDraftsForTests());
        await act(() => heldSubmissionsReady());
        render(<ChatPage />);
        await waitFor(() =>
          expect(screen.getByText(/will return to the composer/)).toBeTruthy(),
        );
        const composer = screen.getByLabelText(
          'Composer',
        ) as HTMLTextAreaElement;
        expect(composer.value).toBe('newer draft');
        expect(state.send).not.toHaveBeenCalled();
        // Acceptance commits after the null lookup. A resend must replay this
        // identity, not allocate a second new-chat task under another key.
        state.send.mockImplementation(async () => {
          state.chatOptions?.onFinish?.({
            message: {
              id: 'late-accepted-reply',
              role: 'assistant',
              parts: [
                {
                  type: 'data-event',
                  data: {
                    type: 'task',
                    task_id: 'late-task',
                    status: 'running',
                  },
                },
              ],
            },
            isDisconnect: true,
            isError: false,
            isAbort: false,
          });
        });
        fireEvent.change(composer, { target: { value: '' } });
        await waitFor(() => expect(composer.value).toBe('read my notes'));
        fireEvent.click(screen.getByRole('button', { name: 'Send' }));
        await waitFor(() => expect(state.send).toHaveBeenCalledTimes(1));
        const body = state.send.mock.calls[0][1].body;
        expect(body.idempotency_key).toBe('queued-files-key');
        expect(body.id).toBe(conversationId);
        expect(body.attachments).toEqual([
          expect.objectContaining({
            name: 'notes.txt',
            text_content: 'exact bytes',
          }),
        ]);
        expect(pendingSubmission('queued-files-key')?.taskId).toBe('late-task');
      } finally {
        setAttachmentStoreForTests(undefined);
      }
    },
  );

  it.each([
    [
      undefined,
      { success: false, error: 'notification timed out' },
      'not recorded',
    ],
    ['invalid', { success: true }, 'not recorded'],
    [undefined, { performed: false }, 'not recorded'],
    [
      'unknown',
      { success: false, error: 'notification timed out' },
      'not recorded',
    ],
    ['failed', { performed: false }, '1 issue'],
    ['succeeded', { success: true }, '+1 other tool'],
  ])(
    'hydrates historical outcome %s without inferring effects from raw payloads',
    async (outcome, result, label) => {
      const conversation = runningConversation(false);
      state.conversation = {
        ...conversation,
        messages: conversation.messages.map((message) =>
          message.role === 'assistant'
            ? {
                ...message,
                tool_calls: [{ name: 'notification_send', arguments: {} }],
                tool_results: [{ name: 'notification_send', result, outcome }],
              }
            : message,
        ),
      };
      render(<ChatPage />);
      const activity = await screen.findByRole('button', {
        name: new RegExp(
          `Tool activity:.*${String(label).replace('+', '\\+')}`,
        ),
      });
      expect(activity).toBeTruthy();
      if (label === 'not recorded')
        expect(activity.getAttribute('aria-label')).not.toContain('issue');
    },
  );

  it.each([
    [
      'web_search',
      { query: 'fixture', results: [], total_found: 0 },
      'succeeded',
      'Searched 1 time',
    ],
    [
      'web_fetch',
      {
        snapshot_id: '0f9a1b2c-1111-4111-8111-111111111111',
        url: 'https://example.test/page',
        content: 'retained page',
        content_length: 13,
        total_chars: 13,
        start_char: 0,
        end_char: 13,
        next_start_char: null,
        complete: true,
        has_more: false,
      },
      'succeeded',
      'Read 1 page',
    ],
    ['web_search', { error: 'search refused' }, 'failed', '1 issue'],
    [
      'web_search',
      { query: 'fixture', results: [], total_found: 0 },
      undefined,
      'not recorded',
    ],
  ])(
    'hydrates recorded read-tool outcome for %s without reclassifying legacy history',
    async (name, result, outcome, label) => {
      const conversation = runningConversation(false);
      state.conversation = {
        ...conversation,
        messages: conversation.messages.map((message) =>
          message.role === 'assistant'
            ? {
                ...message,
                tool_calls: [{ name, arguments: {} }],
                tool_results: [{ name, result, outcome }],
              }
            : message,
        ),
      };
      render(<ChatPage />);
      const activity = await screen.findByRole('button', {
        name: new RegExp(`Tool activity:.*${String(label)}`),
      });
      expect(activity).toBeTruthy();
      if (outcome === 'succeeded')
        expect(activity.getAttribute('aria-label')).not.toContain(
          'not recorded',
        );
    },
  );

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
    const refusedKey = state.send.mock.calls[0][1].body.idempotency_key;
    expect(pendingSubmission(refusedKey)).toBeNull();
    expect(heldSubmission(refusedKey)).toBeUndefined();
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(state.send).toHaveBeenCalledTimes(2));
    expect(state.send.mock.calls[1][1].body.idempotency_key).not.toBe(
      refusedKey,
    );
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
