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
    getCurrentConversation: () => undefined,
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

beforeEach(() => {
  vi.stubGlobal('localStorage', window.localStorage);
  vi.clearAllMocks();
  state.currentId = null;
  auth.clearLocalAuthState();
  auth.setAccessToken('fixture', Date.now() + 120_000);
  state.send.mockReturnValue(new Promise<void>(() => {}));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('page suggestion activation and draft isolation', () => {
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
