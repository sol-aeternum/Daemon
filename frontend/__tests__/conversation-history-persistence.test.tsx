import { act, cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { ConversationList } from '../components/ConversationList';
import { useConversationHistory } from '../hooks/useConversationHistory';
import type { Conversation } from '../hooks/useConversationHistory';
import ChatsPage from '../app/chats/page';

const state = vi.hoisted(() => ({
  search: '',
  push: vi.fn(),
  fetchConversationById: vi.fn(),
  conversations: [] as Conversation[],
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: state.push }),
  useSearchParams: () => new URLSearchParams(state.search),
}));
vi.mock('../lib/auth', () => ({
  getAuthHeader: () => 'Bearer test-history',
  refreshIfNeeded: vi.fn(),
}));
vi.mock('../components/AccountWidget', () => ({ AccountWidget: () => null }));
vi.mock('../components/SidebarShell', () => ({
  SidebarShell: ({ children }: { children: ReactNode }) => children,
}));
vi.mock('../components/ConversationHistoryProvider', () => ({
  ConversationHistoryProvider: ({ children }: { children: ReactNode }) =>
    children,
  useConversationHistoryContext: () => ({
    conversations: state.conversations,
    searchQuery: '',
    setSearchQuery: vi.fn(),
    deleteConversation: vi.fn(),
    createConversation: vi.fn(),
    fetchConversationById: state.fetchConversationById,
  }),
}));

function HistorySidebar() {
  const history = useConversationHistory();
  return (
    <ConversationList
      conversations={history.conversations}
      currentId={new URLSearchParams(state.search).get('id')}
      onSelect={vi.fn()}
      onDelete={vi.fn()}
      onUpdate={vi.fn()}
      onNewChat={vi.fn()}
      searchQuery=""
      setSearchQuery={vi.fn()}
    />
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  state.search = '';
  state.conversations = [];
});

describe('saved conversation visibility', () => {
  it('reloads an untitled saved chat after navigation and a fresh mount while hiding empty drafts', async () => {
    const activity = new Date().toISOString();
    const fetchMock = vi.fn().mockImplementation(
      async () =>
        new Response(
          JSON.stringify({
            conversations: [
              {
                id: 'saved-chat',
                title: 'New conversation',
                message_count: 2,
                created_at: '2000-01-01T00:00:00Z',
                updated_at: '2000-01-01T00:00:00Z',
                last_activity_at: activity,
                pinned: false,
                title_locked: false,
                status: 'active',
                metadata: {},
              },
              {
                id: 'empty-draft',
                title: 'New conversation',
                message_count: 0,
                created_at: activity,
                updated_at: activity,
                last_activity_at: activity,
                pinned: false,
                title_locked: false,
                status: 'active',
                metadata: {},
              },
            ],
          }),
          { headers: { 'Content-Type': 'application/json' } },
        ),
    );
    vi.stubGlobal('fetch', fetchMock);

    for (const search of ['id=saved-chat', '', '']) {
      state.search = search;
      const mounted = render(<HistorySidebar />);
      await screen.findByText('New conversation');
      expect(screen.getAllByText('New conversation')).toHaveLength(1);
      expect(screen.getByText('Today')).toBeTruthy();
      expect(screen.queryByText('Older')).toBeNull();
      mounted.unmount();
    }
    expect(
      fetchMock.mock.calls.filter(
        ([url]) => url === '/conversations?limit=100',
      ),
    ).toHaveLength(3);
    expect(fetchMock.mock.calls[0][0]).toBe('/conversations?limit=100');
  });

  it('orders the Chats page by returned activity instead of stale update timestamps', async () => {
    const base: Conversation = {
      id: 'old-active',
      title: 'Old chat with new messages',
      messages: [],
      createdAt: '2000-01-01T00:00:00Z',
      updatedAt: '2000-01-01T00:00:00Z',
      lastActivityAt: '2026-09-30T00:00:00Z',
      messageCount: 2,
      pinned: false,
      title_locked: false,
      status: 'active',
      metadata: {},
    };
    state.conversations = [
      {
        ...base,
        id: 'newer-inactive',
        title: 'Newer inactive chat',
        updatedAt: '2026-09-29T00:00:00Z',
        lastActivityAt: null,
      },
      base,
    ];
    await act(async () => {
      render(<ChatsPage />);
    });
    const chats = screen.getAllByRole('button', { name: /Last message/ });
    expect(within(chats[0]).getByText(base.title)).toBeTruthy();
    expect(chats[0].textContent).toContain(
      new Date(base.lastActivityAt!).toLocaleString(),
    );
  });
});
