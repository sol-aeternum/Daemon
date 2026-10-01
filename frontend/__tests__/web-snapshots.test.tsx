// Focused regressions for the retained Sources UI. fetch is mocked only;
// no real accounts, providers, requests, or network endpoints are used.
// The real URL builder (webSnapshotPaths) runs against the jsdom origin.
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import type { RenderResult } from '@testing-library/react';
import { StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RetainedSources } from '@/components/RetainedSources';

const auth = vi.hoisted(() => {
  const state = { generation: 0 };
  const listeners = new Set<() => void>();
  const fire = () => {
    for (const listener of Array.from(listeners)) listener();
  };
  return {
    ensureAuthHeader: vi.fn<() => Promise<string | null>>(),
    getAuthGeneration: () => state.generation,
    subscribeAuthGeneration: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    bump: () => {
      state.generation += 1;
      fire();
    },
  };
});

vi.mock('@/lib/auth', () => ({
  ensureAuthHeader: auth.ensureAuthHeader,
  getAuthGeneration: auth.getAuthGeneration,
  subscribeAuthGeneration: auth.subscribeAuthGeneration,
}));

const CONV = 'c1a7e2d0-1111-4222-8333-000000000001';
const CONV_B = 'c1a7e2d0-1111-4222-8333-000000000002';
const ID1 = 'b1a7e2d0-aaaa-4bbb-8ccc-000000000001';
const ID2 = 'b1a7e2d0-aaaa-4bbb-8ccc-000000000002';

const AUTH_TITLE = 'Example page';
const LIST_UNAVAILABLE =
  'Retained sources are unavailable for this conversation.';
const GENERIC_LIST_ERROR = 'Retained sources could not be loaded.';
const AUTH_STATE = 'Sign in to view this conversation’s retained sources.';
const EXPORT_UNAVAILABLE = 'Snapshot export is unavailable right now.';
const EXPORT_TOO_LARGE = 'Snapshot export is larger than the download limit.';
const DELETE_UNKNOWN =
  'The removal outcome is unknown. Refresh to check whether the snapshot was removed.';
const DELETE_GONE =
  'This snapshot is unavailable. Refresh to reconcile the list.';

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function snapshotMeta(
  id: string,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    id,
    conversation_id: CONV,
    source_url: 'https://example.org/source',
    final_url: 'https://example.org/final',
    title: AUTH_TITLE,
    extract_mode: 'readability',
    extraction_version: 'v1',
    content_chars: 120,
    content_bytes: 240,
    stored_bytes: 300,
    retrieved_at: '2026-09-30T10:00:00.000Z',
    expires_at: '2027-01-01T00:00:00.000Z',
    ...overrides,
  };
}

function pageOf(data: {
  snapshots: unknown[];
  total?: number;
  offset?: number;
}): Response {
  return new Response(
    JSON.stringify({
      snapshots: data.snapshots,
      total: data.total ?? data.snapshots.length,
      offset: data.offset ?? 0,
      limit: 20,
    }),
    { status: 200, headers: { 'content-type': 'application/json' } },
  );
}

const statusResponse = (status: number) =>
  new Response(JSON.stringify({ detail: 'SECRET-SERVER-DETAIL' }), {
    status,
    headers: { 'content-type': 'application/json' },
  });

const bodyResponse = (payload: Record<string, unknown>) =>
  new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });

let fetchFn: ReturnType<typeof vi.fn>;

beforeEach(() => {
  auth.ensureAuthHeader
    .mockReset()
    .mockImplementation(async () => 'Bearer token');
  fetchFn = vi.fn(() => Promise.resolve(new Response()));
  vi.stubGlobal('fetch', fetchFn);
  // The real URL constructor stays (the URL builder trusts it); only the
  // object-URL helpers are test doubles.
  Object.assign(URL, {
    createObjectURL: vi.fn(() => 'blob:fixture'),
    revokeObjectURL: vi.fn(),
  });
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const listUrl = (offset: number, conversationId = CONV) =>
  `http://localhost:3000/conversations/${conversationId}/web-snapshots?limit=20&offset=${offset}`;
const deleteUrl = (id = ID1) =>
  `http://localhost:3000/conversations/${CONV}/web-snapshots/${id}`;
const exportUrl = (id = ID1) =>
  `http://localhost:3000/conversations/${CONV}/web-snapshots/${id}/export`;

type CapturedInit = {
  signal?: AbortSignal;
  cache?: string;
  credentials?: string;
  redirect?: string;
  headers?: Record<string, string>;
  method?: string;
};

const initOf = (index: number): CapturedInit =>
  fetchFn.mock.calls[index][1] as CapturedInit;

const removalTrigger = (id = ID1): HTMLButtonElement => {
  const button = document.querySelector<HTMLButtonElement>(
    `[data-snapshot-id="${id}"] [data-removal-trigger="true"]`,
  );
  if (!button) throw new Error('Remove button not found');
  return button;
};

const rows = (): number =>
  document.querySelectorAll('[data-snapshot-id]').length;

/** Mock the initial list load, render, and wait for that row count. */
async function loadedList(
  snapshots: unknown[] = [snapshotMeta(ID1)],
  options: { total?: number; offset?: number } = {},
): Promise<RenderResult> {
  fetchFn.mockResolvedValueOnce(pageOf({ snapshots, ...options }));
  const view = render(<RetainedSources conversationId={CONV} />);
  await waitFor(() => expect(rows()).toBe(snapshots.length));
  return view;
}

describe('RetainedSources loading', () => {
  it('keeps Previous available when expiry leaves a later page empty', async () => {
    fetchFn
      .mockResolvedValueOnce(
        pageOf({ snapshots: [snapshotMeta(ID1)], total: 21 }),
      )
      .mockResolvedValueOnce(
        pageOf({ snapshots: [snapshotMeta(ID2)], total: 21, offset: 20 }),
      )
      .mockResolvedValueOnce(pageOf({ snapshots: [], total: 20, offset: 20 }))
      .mockResolvedValueOnce(
        pageOf({ snapshots: [snapshotMeta(ID1)], total: 20 }),
      );
    render(<RetainedSources conversationId={CONV} />);
    await waitFor(() => expect(rows()).toBe(1));
    fireEvent.click(screen.getByText('Next'));
    await waitFor(() =>
      expect(
        document.querySelector(`[data-snapshot-id="${ID2}"]`),
      ).toBeTruthy(),
    );
    fireEvent.click(screen.getByText('Refresh'));
    await screen.findByText('No retained sources on this page.');
    expect(screen.queryByText('No retained sources yet.')).toBeNull();
    fireEvent.click(screen.getByText('Previous'));
    await waitFor(() =>
      expect(
        document.querySelector(`[data-snapshot-id="${ID1}"]`),
      ).toBeTruthy(),
    );
    expect(fetchFn.mock.calls[3][0]).toBe(listUrl(0));
  });

  it('makes no API call for a missing or invalid conversation id', async () => {
    render(<RetainedSources conversationId={null} />);
    const first = await screen.findByText(LIST_UNAVAILABLE);
    expect(first).toBeTruthy();
    render(<RetainedSources conversationId="not-a-uuid-1234" />);
    const both = screen.getAllByText(LIST_UNAVAILABLE);
    expect(both).toHaveLength(2);
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it('loads metadata through the configured authenticated URL with safe request options', async () => {
    await loadedList();
    expect(fetchFn).toHaveBeenCalledTimes(1);
    const [url, requestInit] = fetchFn.mock.calls[0];
    expect(url).toBe(listUrl(0));
    expect((requestInit as CapturedInit).method).toBe('GET');
    expect((requestInit as CapturedInit).cache).toBe('no-store');
    expect((requestInit as CapturedInit).credentials).toBe('omit');
    expect((requestInit as CapturedInit).redirect).toBe('error');
    expect((requestInit as CapturedInit).headers).toEqual({
      Authorization: 'Bearer token',
    });
    const row = document.querySelector(`[data-snapshot-id="${ID1}"]`);
    expect(row?.textContent).toContain('Retrieved 2026-09-30 10:00 UTC');
    expect(row?.textContent).toContain('Expires 2027-01-01 00:00 UTC');
  });

  it('renders safe HTTP(S) links only, with noopener/noreferrer', async () => {
    await loadedList([
      snapshotMeta(ID1, { final_url: 'javascript:alert(1)' }),
      snapshotMeta(ID2, {
        final_url: 'javascript:alert(1)',
        source_url: 'data:text/html,x',
      }),
    ]);
    const links = await screen.findAllByText('Open original');
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute('href')).toBe('https://example.org/source');
    expect(links[0].getAttribute('rel')).toBe('noopener noreferrer');
    expect(document.querySelector(`[data-snapshot-id="${ID2}"] a`)).toBeNull();
  });

  it('fails generic on wrong-conversation metadata and never renders server text', async () => {
    await loadedList();
    fetchFn.mockReset();
    fetchFn.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          snapshots: [
            snapshotMeta(ID2, {
              title: 'SECRET-PAGE-TITLE',
              conversation_id: 'd1a7e2d0-aaaa-4bbb-8ccc-000000000009',
            }),
          ],
          total: 1,
          offset: 0,
          limit: 20,
          store: 'export schema daemon',
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    );
    fireEvent.click(screen.getByText('Refresh'));
    expect(await screen.findByText(GENERIC_LIST_ERROR)).toBeTruthy();
    const bodyText: string = document.body.textContent ?? '';
    expect(bodyText).not.toContain('SECRET-SERVER-DETAIL');
    expect(bodyText).not.toContain('SECRET-PAGE-TITLE');
    expect(bodyText).not.toContain('export schema daemon');
  });

  it('shows an auth-unavailable state without any request when unauthenticated', async () => {
    auth.ensureAuthHeader.mockResolvedValue(null);
    render(<RetainedSources conversationId={CONV} />);
    await screen.findByText(AUTH_STATE);
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it.each([404, 500])(
    'shows a generic list failure and Refresh reconcile for HTTP %i',
    async (status) => {
      fetchFn
        .mockResolvedValueOnce(statusResponse(status))
        .mockResolvedValueOnce(pageOf({ snapshots: [snapshotMeta(ID1)] }));
      render(<RetainedSources conversationId={CONV} />);
      await screen.findByText(GENERIC_LIST_ERROR);
      expect(document.body.textContent).not.toContain('SECRET-SERVER-DETAIL');
      fireEvent.click(screen.getByText('Refresh'));
      await waitFor(() => expect(rows()).toBe(1));
      expect(fetchFn).toHaveBeenCalledTimes(2);
      expect(fetchFn.mock.calls[1][0]).toBe(listUrl(0));
    },
  );

  it('shows a generic list failure on network rejection', async () => {
    fetchFn.mockRejectedValueOnce(new TypeError('network down'));
    render(<RetainedSources conversationId={CONV} />);
    await screen.findByText(GENERIC_LIST_ERROR);
  });

  it('paginates full pages with Previous/Next and replaces rather than appends', async () => {
    const pageIds = (offset: number, count: number) =>
      Array.from({ length: count }, (_, i) =>
        snapshotMeta(
          `b1a7e2d0-aaaa-4bbb-8ccc-${String(offset + i + 10).padStart(12, '0')}`,
        ),
      );
    fetchFn
      .mockResolvedValueOnce(pageOf({ snapshots: pageIds(0, 20), total: 45 }))
      .mockResolvedValueOnce(
        pageOf({ snapshots: pageIds(20, 20), total: 45, offset: 20 }),
      )
      .mockResolvedValueOnce(
        pageOf({ snapshots: pageIds(40, 5), total: 45, offset: 40 }),
      )
      .mockResolvedValueOnce(
        pageOf({ snapshots: pageIds(20, 20), total: 45, offset: 20 }),
      );
    render(<RetainedSources conversationId={CONV} />);
    await waitFor(() => expect(rows()).toBe(20));
    fireEvent.click(screen.getByText('Next'));
    await waitFor(() => expect(fetchFn.mock.calls[1][0]).toBe(listUrl(20)));
    await waitFor(() => expect(rows()).toBe(20));
    expect(document.querySelector(`[data-snapshot-id="${ID1}"]`)).toBeNull();
    fireEvent.click(screen.getByText('Next'));
    await waitFor(() => expect(fetchFn.mock.calls[2][0]).toBe(listUrl(40)));
    await waitFor(() => expect(rows()).toBe(5));
    expect((screen.getByText('Next') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByText('Previous'));
    await waitFor(() => expect(fetchFn.mock.calls[3][0]).toBe(listUrl(20)));
  });

  it('shows a generic timeout state when the list never responds', async () => {
    vi.useFakeTimers();
    fetchFn.mockImplementation(() => new Promise<Response>(() => {}));
    render(<RetainedSources conversationId={CONV} />);
    await act(async () => {
      vi.advanceTimersByTime(12_500);
      await Promise.resolve();
    });
    expect(screen.getByText(GENERIC_LIST_ERROR)).toBeTruthy();
  });
});

describe('export', () => {
  it('downloads a bounded opaque JSON file via the configured URL and revokes it', async () => {
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {});
    await loadedList();
    fetchFn.mockResolvedValueOnce(
      bodyResponse({
        schema: 'daemon.web_snapshot_export',
        version: 1,
        snapshot_id: ID1,
        content: 'retained page text',
      }),
    );
    fireEvent.click(screen.getByText('Export'));
    await waitFor(() =>
      expect(URL.createObjectURL).toHaveBeenCalledWith(
        expect.objectContaining({ type: 'application/json' }),
      ),
    );
    expect(fetchFn.mock.calls[1][0]).toBe(exportUrl());
    expect(initOf(1).method).toBe('GET');
    expect(initOf(1).cache).toBe('no-store');
    expect(initOf(1).credentials).toBe('omit');
    expect(initOf(1).redirect).toBe('error');
    expect(initOf(1).headers).toEqual({ Authorization: 'Bearer token' });
    expect(clickSpy.mock.instances[0]).toHaveProperty(
      'download',
      `web-snapshot-${ID1}.json`,
    );
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:fixture');
    expect(document.body.textContent).not.toContain('retained page text');
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });

  it.each([401, 403, 500, 413])(
    'shows a generic export error for HTTP %i without creating a download',
    async (status) => {
      await loadedList();
      fetchFn.mockResolvedValueOnce(statusResponse(status));
      fireEvent.click(screen.getByText('Export'));
      if (status === 413) {
        await screen.findByText(EXPORT_TOO_LARGE);
      } else {
        await screen.findByText(EXPORT_UNAVAILABLE);
      }
      expect(URL.createObjectURL).not.toHaveBeenCalled();
      expect(fetchFn).toHaveBeenCalledTimes(2);
    },
  );

  it('shows a generic export error on network failure', async () => {
    await loadedList();
    fetchFn.mockRejectedValueOnce(new TypeError('network down'));
    fireEvent.click(screen.getByText('Export'));
    await screen.findByText(EXPORT_UNAVAILABLE);
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it('refuses non-JSON export bodies without displaying retained text', async () => {
    await loadedList();
    fetchFn.mockResolvedValueOnce(
      new Response('{"content":"private retained text"}', {
        status: 200,
        headers: { 'content-type': 'text/html' },
      }),
    );
    fireEvent.click(screen.getByText('Export'));
    await screen.findByText(EXPORT_UNAVAILABLE);
    expect(document.body.textContent).not.toContain('private retained text');
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it('declines exports streaming past the 8 MiB bound', async () => {
    await loadedList();
    fetchFn.mockResolvedValueOnce(
      bodyResponse({
        schema: 'daemon.web_snapshot_export',
        snapshot_id: ID1,
        content: 'x'.repeat(8 * 1024 * 1024 + 1),
      }),
    );
    fireEvent.click(screen.getByText('Export'));
    await screen.findByText(EXPORT_TOO_LARGE);
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it('aborts an export awaiting authentication when the generation changes', async () => {
    const gate = deferred<string | null>();
    auth.ensureAuthHeader
      .mockReset()
      .mockImplementationOnce(async () => 'Bearer token')
      .mockImplementationOnce(() => gate.promise);
    fetchFn
      .mockResolvedValueOnce(pageOf({ snapshots: [snapshotMeta(ID1)] }))
      .mockResolvedValueOnce(pageOf({ snapshots: [], total: 0 }));
    const view = render(<RetainedSources conversationId={CONV} />);
    await screen.findByText('Export');
    expect(fetchFn).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByText('Export'));
    await waitFor(() => expect(auth.ensureAuthHeader).toHaveBeenCalledTimes(2));
    act(() => {
      auth.bump();
    });
    await act(async () => {
      gate.resolve('Bearer abandoned');
      await Promise.resolve();
    });
    // No export request ever left and no download object was created; the
    // remount is fed a valid empty page for the new lifetime.
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(
      fetchFn.mock.calls.filter((call) => String(call[0]) === exportUrl()),
    ).toHaveLength(0);
    view.unmount();
  });

  it('completes a typical export and cleans up the blob and link', async () => {
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {});
    const view = await loadedList();
    fetchFn.mockResolvedValueOnce(
      bodyResponse({ snapshot_id: ID1, content: 'kept' }),
    );
    const trigger = document.querySelector<HTMLButtonElement>(
      `[data-snapshot-export="${ID1}"]`,
    );
    if (!trigger) throw new Error('Export button not found');
    expect(trigger.textContent).toBe('Export snapshot JSON');
    fireEvent.click(trigger);
    await waitFor(() => expect(clickSpy).toHaveBeenCalledTimes(1));
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect((initOf(1).signal as AbortSignal).aborted).toBe(false);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:fixture');
    expect(document.querySelector('a[download]')).toBeNull();
    view.unmount();
    await act(async () => {
      await Promise.resolve();
    });
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:fixture');
  });
});

describe('removal confirmation', () => {
  it('cancels via Cancel and Escape, returning focus to the invoking button', async () => {
    await loadedList();
    fireEvent.click(removalTrigger());
    expect(screen.getByText('Remove snapshot')).toBeTruthy();
    expect(
      document.body.textContent?.includes(
        'original website and this conversation transcript are unchanged',
      ),
    ).toBe(true);

    fireEvent.click(screen.getByText('Cancel'));
    await waitFor(() =>
      expect(document.querySelector('[data-removal-trigger]')).toBeTruthy(),
    );
    expect(screen.queryByText('Remove snapshot')).toBeNull();

    // Escape from the inline confirmation cancels and returns focus.
    fireEvent.click(removalTrigger());
    const cancel = await screen.findByText('Cancel');
    // A details-close keydown listener would close these details instead;
    // the inline confirmation must stop the event before that.
    const bubblingListener = vi.fn();
    document.addEventListener('keydown', bubblingListener);
    fireEvent.keyDown(cancel, { key: 'Escape' });
    document.removeEventListener('keydown', bubblingListener);
    await waitFor(() =>
      expect(
        (document.activeElement as HTMLElement).dataset.removalTrigger,
      ).toBe('true'),
    );
    expect(bubblingListener).not.toHaveBeenCalled();
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it('issues exactly one DELETE on the explicit confirm and reconciles by refetch', async () => {
    await loadedList();
    fetchFn
      .mockResolvedValueOnce(bodyResponse({ status: 'deleted' }))
      .mockResolvedValueOnce(pageOf({ snapshots: [], total: 0 }));
    fireEvent.click(removalTrigger());
    fireEvent.click(screen.getByText('Remove snapshot'));
    const empty = await screen.findByText('No retained sources yet.');
    expect(empty).toBeTruthy();
    const deleteIndex = fetchFn.mock.calls.findIndex(
      ([url]) => url === deleteUrl(),
    );
    expect(deleteIndex).toBeGreaterThanOrEqual(0);
    expect(fetchFn.mock.calls[deleteIndex][0]).toBe(deleteUrl());
    expect(initOf(deleteIndex).method).toBe('DELETE');
    expect(initOf(deleteIndex).cache).toBe('no-store');
    expect(initOf(deleteIndex).credentials).toBe('omit');
    expect(initOf(deleteIndex).redirect).toBe('error');
    expect(initOf(deleteIndex).headers).toEqual({
      Authorization: 'Bearer token',
    });
    expect(
      fetchFn.mock.calls.filter(([url]) => url === deleteUrl()),
    ).toHaveLength(1);
    expect(fetchFn.mock.calls[deleteIndex + 1][0]).toBe(listUrl(0));
  });

  it('moves back one full page when the only row of a later page deletes', async () => {
    await loadedList([snapshotMeta(ID1)], { total: 21 });
    const LAST_ROW_ID = 'b1a7e2d0-aaaa-4bbb-8ccc-000000000015';
    fetchFn
      .mockResolvedValueOnce(
        pageOf({
          snapshots: [snapshotMeta(LAST_ROW_ID)],
          total: 21,
          offset: 20,
        }),
      )
      .mockResolvedValueOnce(bodyResponse({ status: 'deleted' }))
      .mockResolvedValueOnce(
        pageOf({ snapshots: [snapshotMeta(ID1)], total: 20, offset: 0 }),
      );
    fireEvent.click(screen.getByText('Next'));
    await waitFor(() =>
      expect(
        document.querySelector(`[data-snapshot-id="${LAST_ROW_ID}"]`),
      ).toBeTruthy(),
    );
    const lastRemove = document.querySelector<HTMLButtonElement>(
      `[data-snapshot-id="${LAST_ROW_ID}"] ~ * [data-removal-trigger="true"],
       [data-removal-trigger="true"]`,
    );
    if (!lastRemove) throw new Error('Remove button not found');
    expect(fetchFn).toHaveBeenCalledTimes(2);
    fireEvent.click(lastRemove);
    fireEvent.click(screen.getByText('Remove snapshot'));
    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(4));
    expect(fetchFn.mock.calls[2][0]).toBe(deleteUrl(LAST_ROW_ID));
    expect(fetchFn.mock.calls[3][0]).toBe(listUrl(0));
    expect(screen.queryByText(GENERIC_LIST_ERROR)).toBeNull();
  });

  it('never replays a DELETE after an unknown outcome; retry stays manual', async () => {
    await loadedList();
    fetchFn.mockRejectedValueOnce(new TypeError('network down'));
    fireEvent.click(removalTrigger());
    fireEvent.click(screen.getByText('Remove snapshot'));
    await screen.findByText(DELETE_UNKNOWN);
    expect(fetchFn).toHaveBeenCalledTimes(2);
    expect(screen.queryByText('Remove snapshot')).toBeNull();
    expect(screen.getByText('Refresh')).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });

  it('does not claim ownership or expiry reasons when a DELETE 404s', async () => {
    await loadedList();
    fetchFn.mockResolvedValueOnce(statusResponse(404));
    fireEvent.click(removalTrigger());
    fireEvent.click(screen.getByText('Remove snapshot'));
    const notice = await screen.findByText(DELETE_GONE);
    const text = notice.textContent?.toLowerCase() ?? '';
    expect(text).not.toContain('owner');
    expect(text).not.toContain('expire');
    expect(text).not.toContain('authoris');
    expect(text).not.toContain('wrong');
  });

  it('invalidates the inline confirmation on page change and refresh', async () => {
    fetchFn
      .mockResolvedValueOnce(
        pageOf({
          snapshots: [
            snapshotMeta(ID1, { title: 'FirstRow' }),
            snapshotMeta(ID2, { title: 'SecondRow' }),
          ],
          total: 21,
        }),
      )
      .mockResolvedValueOnce(
        pageOf({
          snapshots: [snapshotMeta(ID2, { title: 'SecondRow' })],
          total: 21,
          offset: 20,
        }),
      )
      .mockResolvedValueOnce(
        pageOf({
          snapshots: [
            snapshotMeta(ID1, { title: 'FirstRow' }),
            snapshotMeta(ID2, { title: 'SecondRow' }),
          ],
        }),
      );
    render(<RetainedSources conversationId={CONV} />);
    await screen.findByText('FirstRow');
    expect(fetchFn).toHaveBeenCalledTimes(1);
    fireEvent.click(removalTrigger(ID1));
    expect(screen.getByText('Remove snapshot')).toBeTruthy();

    fireEvent.click(screen.getByText('Next'));
    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(2));
    expect(screen.queryByText('Remove snapshot')).toBeNull();

    const secondRemove = document.querySelector<HTMLButtonElement>(
      `[data-removal-trigger="true"]`,
    );
    if (!secondRemove) throw new Error('Remove button not found');
    fireEvent.click(secondRemove);
    expect(screen.getByText('Remove snapshot')).toBeTruthy();

    fireEvent.click(screen.getByText('Refresh'));
    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(3));
    expect(screen.queryByText('Remove snapshot')).toBeNull();

    expect(
      fetchFn.mock.calls.filter(
        ([, requestInit]) =>
          (requestInit as CapturedInit | undefined)?.method === 'DELETE',
      ),
    ).toHaveLength(0);
  });

  it('disables removal and export while a delete request runs', async () => {
    const gate = deferred<string | null>();
    auth.ensureAuthHeader
      .mockReset()
      .mockImplementationOnce(async () => 'Bearer token')
      .mockImplementationOnce(() => gate.promise);
    await loadedList();
    expect(fetchFn).toHaveBeenCalledTimes(1);
    fireEvent.click(removalTrigger());
    fireEvent.click(screen.getByText('Remove snapshot'));
    await waitFor(() =>
      expect((screen.getByText('Export') as HTMLButtonElement).disabled).toBe(
        true,
      ),
    );
    expect((screen.getByText('Refresh') as HTMLButtonElement).disabled).toBe(
      true,
    );
    gate.resolve('Bearer token');
  });
});

describe('lifetime invalidation', () => {
  it('loads under StrictMode effect replay without reviving abandoned auth', async () => {
    const oldAuth = deferred<string | null>();
    auth.ensureAuthHeader.mockImplementationOnce(() => oldAuth.promise);
    fetchFn.mockResolvedValueOnce(pageOf({ snapshots: [snapshotMeta(ID1)] }));
    render(
      <StrictMode>
        <RetainedSources conversationId={CONV} />
      </StrictMode>,
    );
    await screen.findByText(AUTH_TITLE);
    await act(async () => oldAuth.resolve('Bearer old'));
    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(initOf(0).headers).toEqual({ Authorization: 'Bearer token' });
  });

  it.each([401, 403])(
    'clears old metadata when refresh returns %i',
    async (status) => {
      await loadedList();
      fetchFn.mockResolvedValueOnce(statusResponse(status));
      fireEvent.click(screen.getByText('Refresh'));
      await screen.findByText(AUTH_STATE);
      expect(rows()).toBe(0);
      expect(screen.queryByText('Export')).toBeNull();
    },
  );

  it('ignores an old response body across A to B to A', async () => {
    const oldBody = deferred<unknown>();
    fetchFn.mockResolvedValueOnce({ ok: true, json: () => oldBody.promise });
    const view = render(<RetainedSources conversationId={CONV} />);
    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(1));
    fetchFn.mockResolvedValueOnce(
      pageOf({
        snapshots: [
          snapshotMeta(ID2, { conversation_id: CONV_B, title: 'B source' }),
        ],
      }),
    );
    view.rerender(<RetainedSources conversationId={CONV_B} />);
    await screen.findByText('B source');
    fetchFn.mockResolvedValueOnce(
      pageOf({ snapshots: [snapshotMeta(ID1, { title: 'Current A' })] }),
    );
    view.rerender(<RetainedSources conversationId={CONV} />);
    await screen.findByText('Current A');
    await act(async () =>
      oldBody.resolve({
        snapshots: [snapshotMeta(ID2, { title: 'Abandoned A' })],
        offset: 0,
        limit: 20,
        total: 1,
      }),
    );
    expect(screen.queryByText('Abandoned A')).toBeNull();
    expect(screen.getByText('Current A')).toBeTruthy();
  });

  it('cancels a held export body on unmount without creating a private blob', async () => {
    const view = await loadedList();
    const cancel = vi.fn();
    fetchFn.mockResolvedValueOnce(
      new Response(new ReadableStream({ cancel }), {
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    fireEvent.click(screen.getByText('Export'));
    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(2));
    view.unmount();
    await waitFor(() => expect(cancel).toHaveBeenCalled());
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it('never sends a confirmed delete after its pending auth scope changes', async () => {
    await loadedList();
    const gate = deferred<string | null>();
    auth.ensureAuthHeader.mockImplementationOnce(() => gate.promise);
    fireEvent.click(removalTrigger());
    fireEvent.click(screen.getByText('Remove snapshot'));
    fetchFn.mockResolvedValueOnce(pageOf({ snapshots: [] }));
    act(() => auth.bump());
    await act(async () => gate.resolve('Bearer old'));
    await screen.findByText('No retained sources yet.');
    expect(
      fetchFn.mock.calls.filter(([, init]) => init.method === 'DELETE'),
    ).toHaveLength(0);
  });

  it('times out a hung export body and releases its stream', async () => {
    await loadedList();
    vi.useFakeTimers();
    const cancel = vi.fn();
    fetchFn.mockResolvedValueOnce(
      new Response(new ReadableStream({ cancel }), {
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    await act(async () => {
      fireEvent.click(screen.getByText('Export'));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_001);
    });
    expect(screen.getByText(EXPORT_UNAVAILABLE)).toBeTruthy();
    expect(cancel).toHaveBeenCalled();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it('aborts an in-flight list request on unmount', async () => {
    fetchFn.mockImplementation(() => new Promise<Response>(() => {}));
    const view = render(<RetainedSources conversationId={CONV} />);
    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(1));
    expect(initOf(0).signal?.aborted).toBe(false);
    view.unmount();
    await act(async () => {
      await Promise.resolve();
    });
    expect(initOf(0).signal?.aborted).toBe(true);
  });

  it('renders no old metadata and aborts requests on generation change', async () => {
    await loadedList();
    act(() => {
      auth.bump();
    });
    expect(screen.queryByText('Example page')).toBeNull();
  });

  it('re-keyed mounts refetch the new conversation', async () => {
    fetchFn
      .mockResolvedValueOnce(pageOf({ snapshots: [snapshotMeta(ID1)] }))
      .mockResolvedValueOnce(
        pageOf({
          snapshots: [
            snapshotMeta(ID2, { title: 'Second', conversation_id: CONV_B }),
          ],
        }),
      );
    const view = render(<RetainedSources conversationId={CONV} />);
    await screen.findByText('Export'); // wait? actually row title needed
    await screen.findByText('Remove');
    view.rerender(<RetainedSources conversationId={CONV_B} />);
    expect(await screen.findByText('Second')).toBeTruthy();
    expect(screen.queryByText('Example page')).toBeNull();
    expect(fetchFn.mock.calls[1][0]).toBe(listUrl(0, CONV_B));
  });
});
