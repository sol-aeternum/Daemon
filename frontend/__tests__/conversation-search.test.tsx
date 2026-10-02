import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useEffect } from 'react';
import { ConversationList } from '../components/ConversationList';
import {
  SEARCH_PAGE_SIZE,
  useConversationHistory,
} from '../hooks/useConversationHistory';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(''),
}));
vi.mock('../lib/auth', () => ({
  getAuthHeader: () => 'Bearer test-search',
  refreshIfNeeded: vi.fn(),
}));
vi.mock('../components/AccountWidget', () => ({ AccountWidget: () => null }));

const activity = new Date().toISOString();

function apiConversation(id: string, title: string) {
  return {
    id,
    title,
    created_at: activity,
    updated_at: activity,
    message_count: 2,
    last_activity_at: activity,
    pinned: false,
    title_locked: false,
    status: 'active',
    metadata: {},
  };
}

const loaded = [
  apiConversation('recent-trip', 'Trip packing list'),
  apiConversation('recent-code', 'Refactor parser'),
];

type SearchResponder = (
  params: URLSearchParams,
) => Promise<Response> | Response;

let searchResponder: SearchResponder;
let searchRequests: URLSearchParams[];
let searchHeaders: Headers[];
let latest: ReturnType<typeof useConversationHistory>;

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

beforeEach(() => {
  searchRequests = [];
  searchHeaders = [];
  searchResponder = () => json({ conversations: [] });
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), 'http://localhost');
      if (url.pathname.endsWith('/conversations')) {
        if (url.searchParams.has('search')) {
          searchRequests.push(url.searchParams);
          searchHeaders.push(new Headers(init?.headers));
          return searchResponder(url.searchParams);
        }
        return json({ conversations: loaded });
      }
      if (init?.method === 'DELETE') return new Response(null, { status: 204 });
      return json({});
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function Sidebar({
  expose,
}: {
  expose: (history: ReturnType<typeof useConversationHistory>) => void;
}) {
  const history = useConversationHistory();
  useEffect(() => expose(history));
  return (
    <ConversationList
      conversations={history.conversations}
      currentId={null}
      onSelect={vi.fn()}
      onDelete={history.deleteConversation}
      onUpdate={history.updateConversation}
      onNewChat={vi.fn()}
      searchQuery={history.searchQuery}
      setSearchQuery={history.setSearchQuery}
      search={history.conversationSearch}
    />
  );
}

async function renderSidebar() {
  render(
    <Sidebar
      expose={(history) => {
        latest = history;
      }}
    />,
  );
  await screen.findByText('Trip packing list');
  return screen.getByRole('searchbox', { name: 'Search conversation titles' });
}

function type(input: HTMLElement, value: string) {
  fireEvent.change(input, { target: { value } });
}

describe('sidebar title search across all conversations', () => {
  it('finds an older conversation that is not among the loaded ones', async () => {
    searchResponder = () =>
      json({ conversations: [apiConversation('old-1', 'Tax return 2019')] });
    const input = await renderSidebar();
    type(input, '  tax  ');

    expect(await screen.findByText('Tax return 2019')).toBeTruthy();
    expect(screen.queryByText('Trip packing list')).toBeNull();
    expect(screen.getByTestId('conversation-search-status').textContent).toBe(
      '1 title matches',
    );
    expect(searchRequests).toHaveLength(1);
    expect(searchRequests[0].get('search')).toBe('tax');
    expect(searchRequests[0].get('limit')).toBe(String(SEARCH_PAGE_SIZE));
    expect(searchRequests[0].get('offset')).toBe('0');
    expect(searchHeaders[0].get('Authorization')).toBe('Bearer test-search');
  });

  it('shows loaded title matches immediately and says it is still searching', async () => {
    let release!: () => void;
    searchResponder = () =>
      new Promise((resolve) => {
        release = () =>
          resolve(
            json({
              conversations: [
                apiConversation('recent-trip', 'Trip packing list'),
                apiConversation('old-trip', 'Trip to Kyoto 2021'),
              ],
            }),
          );
      });
    const input = await renderSidebar();
    type(input, 'trip');

    expect(screen.getByText('Trip packing list')).toBeTruthy();
    expect(screen.getByTestId('conversation-search-status').textContent).toBe(
      'Searching all conversation titles…',
    );
    await waitFor(() => expect(searchRequests).toHaveLength(1));
    await act(async () => release());
    expect(await screen.findByText('Trip to Kyoto 2021')).toBeTruthy();
    expect(screen.getAllByText('Trip packing list')).toHaveLength(1);
  });

  it('debounces typing into a single request for the final query', async () => {
    const input = await renderSidebar();
    for (const value of ['p', 'pa', 'par', 'pars']) type(input, value);
    await waitFor(() => expect(searchRequests).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(searchRequests.map((p) => p.get('search'))).toEqual(['pars']);
  });

  it('never shows results from a superseded query', async () => {
    const pending: Record<string, () => void> = {};
    searchResponder = (params) =>
      new Promise((resolve) => {
        const query = params.get('search')!;
        pending[query] = () =>
          resolve(
            json({
              conversations: [apiConversation(`id-${query}`, `About ${query}`)],
            }),
          );
      });
    const input = await renderSidebar();
    type(input, 'alpha');
    await waitFor(() => expect(pending.alpha).toBeDefined());
    type(input, 'beta');
    await waitFor(() => expect(pending.beta).toBeDefined());
    await act(async () => pending.beta());
    await act(async () => pending.alpha());

    expect(await screen.findByText('About beta')).toBeTruthy();
    expect(screen.queryByText('About alpha')).toBeNull();
  });

  it('loads further pages on request without duplicating rows', async () => {
    const first = Array.from({ length: SEARCH_PAGE_SIZE }, (_, index) =>
      apiConversation(`note-${index}`, `Note ${index}`),
    );
    searchResponder = (params) =>
      params.get('offset') === '0'
        ? json({ conversations: first })
        : json({
            conversations: [
              first[SEARCH_PAGE_SIZE - 1],
              apiConversation('note-extra', 'Note extra'),
            ],
          });
    const input = await renderSidebar();
    type(input, 'note');

    const more = await screen.findByRole('button', {
      name: 'Load more results',
    });
    expect(screen.getByTestId('conversation-search-status').textContent).toBe(
      `${SEARCH_PAGE_SIZE}+ titles match`,
    );
    fireEvent.click(more);

    expect(await screen.findByText('Note extra')).toBeTruthy();
    expect(searchRequests.at(-1)?.get('offset')).toBe(String(SEARCH_PAGE_SIZE));
    expect(screen.getAllByText(`Note ${SEARCH_PAGE_SIZE - 1}`)).toHaveLength(1);
    expect(
      screen.queryByRole('button', { name: 'Load more results' }),
    ).toBeNull();
  });

  it('reports a failed search truthfully and retries on request', async () => {
    let fail = true;
    searchResponder = () =>
      fail
        ? json({ detail: 'unavailable' }, 503)
        : json({ conversations: [apiConversation('old-2', 'Refinance plan')] });
    const input = await renderSidebar();
    type(input, 'ref');

    await waitFor(() =>
      expect(
        screen.getByTestId('conversation-search-status').textContent,
      ).toContain("Couldn't search older conversations"),
    );
    expect(screen.getByText('Refactor parser')).toBeTruthy();
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    expect(await screen.findByText('Refinance plan')).toBeTruthy();
  });

  it('says plainly when no title matches', async () => {
    const input = await renderSidebar();
    type(input, 'zebra');
    expect(
      await screen.findByText('No conversation titles match “zebra”'),
    ).toBeTruthy();
  });

  it('Escape clears the search and restores the full list', async () => {
    searchResponder = () =>
      json({ conversations: [apiConversation('old-1', 'Tax return 2019')] });
    const input = await renderSidebar();
    type(input, 'tax');
    await screen.findByText('Tax return 2019');
    fireEvent.keyDown(input, { key: 'Escape' });

    expect((input as HTMLInputElement).value).toBe('');
    expect(screen.getByText('Trip packing list')).toBeTruthy();
    expect(screen.queryByText('Tax return 2019')).toBeNull();
    expect(screen.queryByTestId('conversation-search-status')).toBeNull();
  });

  it('keeps search results in step with renames and deletions', async () => {
    searchResponder = () =>
      json({
        conversations: [
          apiConversation('old-1', 'Tax return 2019'),
          apiConversation('old-2', 'Tax return 2020'),
        ],
      });
    const input = await renderSidebar();
    type(input, 'tax');
    await screen.findByText('Tax return 2020');

    await act(async () => {
      await latest.updateConversation('old-1', {
        title: 'Tax return 2019 (filed)',
      });
    });
    expect(screen.getByText('Tax return 2019 (filed)')).toBeTruthy();

    await act(async () => {
      await latest.deleteConversation('old-2');
    });
    expect(screen.queryByText('Tax return 2020')).toBeNull();
  });

  it('without a server search, still filters loaded titles locally', async () => {
    render(
      <ConversationList
        conversations={[
          {
            id: 'a',
            title: 'Garden plan',
            messages: [],
            createdAt: activity,
            updatedAt: activity,
            messageCount: 1,
            pinned: false,
            title_locked: false,
            status: 'active',
            metadata: {},
          },
        ]}
        currentId={null}
        onSelect={vi.fn()}
        onDelete={vi.fn()}
        onUpdate={vi.fn()}
        onNewChat={vi.fn()}
        searchQuery="garden"
        setSearchQuery={vi.fn()}
      />,
    );
    expect(screen.getByText('Garden plan')).toBeTruthy();
    expect(screen.queryByTestId('conversation-search-status')).toBeNull();
  });
});
