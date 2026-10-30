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
import type { ChatTransport } from 'ai';
import * as auth from '../lib/auth';
import { ensureBrowserCacheCleared } from '../lib/browserCache';
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
  isCurrentConversation: () => true,
  isHistoryScopeCurrent: () => true,
  refreshHistory: vi.fn(),
  emptyConversation: {
    id: 'existing-empty-chat',
    title: 'New conversation',
    messages: [] as DaemonMessage[],
    createdAt: '',
    updatedAt: '',
    pinned: false,
    title_locked: false,
    status: 'active',
    metadata: {},
  },
  currentId: null as string | null,
  setMessages: null as null | ((messages: DaemonMessage[]) => void),
  messages: [] as DaemonMessage[],
  transport: null as ChatTransport<DaemonMessage> | null,
  stopView: null as {
    conversationId: string | null;
    stoppedMessageIds: Set<string>;
    stopGeneration: () => void;
  } | null,
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
    useChat: (options: { transport: ChatTransport<DaemonMessage> }) => {
      state.transport = options.transport;
      const [messages, setMessages] = useState<DaemonMessage[]>([]);
      state.setMessages = setMessages;
      state.messages = messages;
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
vi.mock('../hooks/useStopGeneration', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../hooks/useStopGeneration')>();
  return {
    ...actual,
    useStopGeneration: (
      options: Parameters<typeof actual.useStopGeneration>[0],
    ) => {
      const result = actual.useStopGeneration(options);
      // Observe the page's real Stop hook composition, not a replacement model.
      state.stopView = { ...result, conversationId: options.conversationId };
      return result;
    },
  };
});
vi.mock('../components/ConversationHistoryProvider', () => ({
  ConversationHistoryProvider: ({ children }: { children: ReactNode }) =>
    children,
  useConversationHistoryContext: () => ({
    currentId: state.currentId,
    conversations: [],
    getCurrentConversation: () =>
      state.currentId === 'existing-empty-chat'
        ? state.emptyConversation
        : undefined,
    messageHistory: { scope: 'test-scope' },
    isCurrentConversation: state.isCurrentConversation,
    isHistoryScopeCurrent: state.isHistoryScopeCurrent,
    refreshMessageHistory: state.refreshHistory,
    createConversation: vi.fn(),
    updateConversation: vi.fn(),
    setConversationModel: vi.fn(),
    resolveSelectedModel: () => 'auto',
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
    attachments,
  }: {
    input: string;
    onInputChange: (event: React.ChangeEvent<HTMLTextAreaElement>) => void;
    attachments: Array<{ name: string }>;
  }) => (
    <div>
      <textarea aria-label="Composer" value={input} onChange={onInputChange} />
      {attachments.map(({ name }) => (
        <span key={name}>{name}</span>
      ))}
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
import {
  FIXTURE_AUTH_EPOCH,
  createAuthTestStorage,
  installAuthCookieTestEnvironment,
} from './helpers/authCookieEnvironment';

beforeEach(() => {
  const storage = createAuthTestStorage();
  vi.stubGlobal('localStorage', storage);
  vi.clearAllMocks();
  vi.stubGlobal('matchMedia', () => ({
    matches: true,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
  state.currentId = null;
  state.transport = null;
  state.stopView = null;
  // Real auth/coordinator modules need usable nonsecret shared metadata and a
  // qualified navigator (FIFO Web Locks fixture) before manual token binding.
  installAuthCookieTestEnvironment(storage);
  auth.clearLocalAuthState();
  auth.setAccessToken('fixture', Date.now() + 120_000, FIXTURE_AUTH_EPOCH);
  state.send.mockReturnValue(new Promise<void>(() => {}));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('page suggestion activation and draft isolation', () => {
  it('does not cancel a live suggestion for a same-generation readiness notification', () => {
    const listeners = new Set<() => void>();
    const subscribe = vi
      .spyOn(auth, 'subscribeAuthGeneration')
      .mockImplementation((callback) => {
        listeners.add(callback);
        return () => {
          listeners.delete(callback);
        };
      });
    try {
      render(<ChatPage />);
      fireEvent.click(screen.getByRole('button', { name: 'Plan the release' }));
      act(() =>
        state.setMessages?.([
          {
            id: 'live',
            role: 'assistant',
            parts: [{ type: 'text', text: 'Synthetic live partial' }],
          },
        ]),
      );
      const generation = auth.getAuthGeneration();
      act(() => {
        for (const listener of listeners) listener();
      });
      expect(auth.getAuthGeneration()).toBe(generation);
      expect(state.stop).not.toHaveBeenCalled();
      expect(state.messages.map((message) => message.id)).toEqual(['live']);
    } finally {
      subscribe.mockRestore();
    }
  });
  it.each(
    [null, 'existing-empty-chat'].flatMap((origin) =>
      ['before-assignment', 'after-assignment-before-route', 'after-route'].map(
        (phase) => ({ origin, phase }),
      ),
    ),
  )(
    'keeps suggestion Stop ownership from $origin at $phase through route promotion and navigation',
    async ({ origin, phase }) => {
      state.currentId = origin;
      const generation = auth.getAuthGeneration();
      const draft = openChatDraft(origin, generation);
      setChatDraftInput(draft, 'Unrelated origin draft');
      const view = render(<ChatPage />);
      fireEvent.click(screen.getByRole('button', { name: 'Plan the release' }));
      expect(state.send).toHaveBeenCalledOnce();
      const partial: DaemonMessage = {
        id: 'suggestion-partial',
        role: 'assistant',
        parts: [{ type: 'text', text: 'Partial suggestion answer' }],
      };
      act(() => state.setMessages?.([partial]));
      expect(state.stopView?.conversationId).toBeNull();
      if (phase === 'before-assignment') {
        act(() => state.stopView?.stopGeneration());
        expect(state.stopView?.stoppedMessageIds.has(partial.id)).toBe(true);
      }
      act(() =>
        state.setMessages?.([
          {
            ...partial,
            parts: [
              ...partial.parts,
              {
                type: 'data-event',
                data: {
                  type: 'conversation',
                  conversation_id: 'suggestion-destination',
                },
              } as never,
            ],
          },
        ]),
      );
      await waitFor(() =>
        expect(state.replace).toHaveBeenCalledWith(
          '/?id=suggestion-destination',
        ),
      );
      // The router is deliberately held: assignment must already own Stop.
      expect(state.currentId).toBe(origin);
      expect(state.stopView?.conversationId).toBeNull();
      if (phase === 'after-assignment-before-route')
        act(() => state.stopView?.stopGeneration());
      if (phase !== 'after-route')
        expect(state.stopView?.stoppedMessageIds.has(partial.id)).toBe(true);
      state.currentId = 'suggestion-destination';
      view.rerender(<ChatPage />);
      expect(state.stopView?.conversationId).toBe('suggestion-destination');
      if (phase === 'after-route') act(() => state.stopView?.stopGeneration());
      expect(state.stopView?.stoppedMessageIds.has(partial.id)).toBe(true);
      expect(state.stop).toHaveBeenCalledOnce();
      state.currentId = 'unrelated-conversation';
      view.rerender(<ChatPage />);
      expect(state.stopView?.stoppedMessageIds.has(partial.id)).toBe(false);
      state.currentId = 'suggestion-destination';
      view.rerender(<ChatPage />);
      expect(state.stopView?.stoppedMessageIds.has(partial.id)).toBe(true);
      expect(getChatDraft(origin, generation).input).toBe(
        'Unrelated origin draft',
      );
      expect(getChatDraft('suggestion-destination', generation).input).toBe('');
    },
  );

  it('uses the real SDK transport request path without executing fetch during render', async () => {
    // Manual token binding invalidates readiness. Exercise the real barrier's
    // qualified acknowledgement before testing the separate chat request path.
    vi.stubGlobal('isSecureContext', true);
    vi.stubEnv('NEXT_PUBLIC_API_URL', '');
    const clearCache = vi.fn(
      async () =>
        new Response(null, {
          status: 204,
          headers: { 'Cache-Control': 'no-store' },
        }),
    );
    vi.stubGlobal('fetch', clearCache);
    expect(auth.getAuthHeader()).toBeNull();
    await ensureBrowserCacheCleared();
    expect(clearCache).toHaveBeenCalledExactlyOnceWith(
      '/api/v1/auth/cache/clear',
      expect.objectContaining({ method: 'POST', cache: 'no-store' }),
    );
    expect(auth.getAuthHeader()).toBe('Bearer fixture');
    const request = vi.fn(
      async () =>
        new Response(
          'data: {"type":"start","messageId":"response"}\n\ndata: [DONE]\n\n',
          { headers: { 'Content-Type': 'text/event-stream' } },
        ),
    );
    vi.stubGlobal('fetch', request);
    const view = render(<ChatPage />);
    const transport = state.transport!;
    expect(transport).toBeTruthy();
    expect(request).not.toHaveBeenCalled();
    view.rerender(<ChatPage />);
    expect(state.transport).toBe(transport);
    const controller = new AbortController();
    let stream!: ReadableStream;
    await act(async () => {
      stream = await transport.sendMessages({
        trigger: 'submit-message',
        chatId: 'sdk-chat',
        messageId: undefined,
        messages: [
          {
            id: 'user',
            role: 'user',
            parts: [{ type: 'text', text: 'synthetic request' }],
          },
        ],
        abortSignal: controller.signal,
      });
    });
    expect(request).toHaveBeenCalledOnce();
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toBe('/api/chat');
    expect(init?.method).toBe('POST');
    expect(new Headers(init?.headers).get('Authorization')).toBe(
      'Bearer fixture',
    );
    expect(init?.signal).toBe(controller.signal);
    const body = JSON.parse(String(init?.body));
    expect(body.messages[0].parts[0].text).toBe('synthetic request');
    const reader = stream.getReader();
    expect((await reader.read()).value).toMatchObject({
      type: 'start',
      messageId: 'response',
    });
    await reader.cancel();
  });
  it.each([null, 'existing-empty-chat'])(
    'does not submit or transfer unrelated text/files from %s',
    async (id) => {
      state.currentId = id;
      const generation = auth.getAuthGeneration();
      const scope = openChatDraft(id, generation);
      const file = new File(['unrelated draft content'], 'private-draft.txt');
      setChatDraftInput(scope, 'Unfinished unrelated question');
      setChatDraftAttachments(scope, [{ id: 'file', file }]);
      render(<ChatPage />);
      fireEvent.click(screen.getByRole('button', { name: 'Plan the release' }));
      fireEvent.click(screen.getByRole('button', { name: 'Plan the release' }));
      expect(state.send).toHaveBeenCalledTimes(1);
      expect(state.send.mock.calls[0][0].text).toBe(candidate.prompt);
      expect(state.send.mock.calls[0][1]).toEqual({
        body: {
          id: null,
          suggestion_id: candidate.id,
          __suggestionAuthGeneration: generation,
          answer_detail: 'balanced',
          research_effort: 'auto',
        },
      });
      act(() =>
        state.setMessages?.([
          {
            id: 'answer',
            role: 'assistant',
            parts: [
              {
                type: 'data-event',
                data: { type: 'conversation', conversation_id: 'new-chat' },
              } as never,
            ],
          },
        ]),
      );
      await waitFor(() =>
        expect(state.replace).toHaveBeenCalledWith('/?id=new-chat'),
      );
      expect(getChatDraft(id, generation).input).toBe(
        'Unfinished unrelated question',
      );
      expect(getChatDraft(id, generation).pendingAttachments[0].file).toBe(
        file,
      );
      expect(getChatDraft('new-chat', generation).input).toBe('');
    },
  );

  it('aborts a suggestion send on auth revocation and discards late conversation events', async () => {
    render(<ChatPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Plan the release' }));
    act(() => auth.clearLocalAuthState());
    expect(state.stop).toHaveBeenCalled();
    act(() =>
      state.setMessages?.([
        {
          id: 'late',
          role: 'assistant',
          parts: [
            {
              type: 'data-event',
              data: {
                type: 'conversation',
                conversation_id: 'private-old-account',
              },
            } as never,
          ],
        },
      ]),
    );
    expect(state.replace).not.toHaveBeenCalled();
  });
});
