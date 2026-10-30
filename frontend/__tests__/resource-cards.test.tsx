// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ResourceCard, TaskResources } from '@/components/ResourceCards';
import { ResourceGallery } from '@/components/ResourceGallery';
import type { ResourceItem } from '@/lib/resources';
const auth = vi.hoisted(() => ({
  generation: 1,
  listeners: new Set<() => void>(),
  header: vi.fn<() => Promise<string | null>>(),
}));
vi.mock('@/lib/auth', () => ({
  ensureAuthHeader: auth.header,
  getAuthGeneration: () => auth.generation,
  subscribeAuthGeneration: (fn: () => void) => {
    auth.listeners.add(fn);
    return () => auth.listeners.delete(fn);
  },
}));
const item: ResourceItem = {
  resource_id: '11111111-1111-4111-8111-111111111111',
  version_id: '22222222-2222-4222-8222-222222222222',
  representation: 'submitted_text',
  kind: 'text',
  mime: 'text/plain',
  measured_bytes: 12,
  created_at: '2026-01-01',
  availability: 'available',
  display: { name: 'received.txt', title: null },
  reference: null,
};
beforeEach(() => {
  auth.generation = 1;
  auth.header.mockReset().mockResolvedValue('Bearer fictional-A');
  vi.stubGlobal('fetch', vi.fn());
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  expect(auth.listeners.size).toBe(0);
});
it('renders exact text as escaped prose and local preview only performs GET', async () => {
  vi.mocked(fetch).mockResolvedValue(
    new Response('<script>not executable</script>'),
  );
  render(<ResourceCard item={item} />);
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await screen.findByText('<script>not executable</script>');
  expect(document.querySelector('script')).toBeNull();
  expect(fetch).toHaveBeenCalledOnce();
  expect(vi.mocked(fetch).mock.calls[0][0]).toContain('/preview');
});
it('rejects an A body arriving after account change', async () => {
  let finish!: (text: string) => void;
  const body = new Promise<string>((r) => {
    finish = r;
  });
  vi.mocked(fetch).mockResolvedValue({
    ok: true,
    text: () => body,
  } as Response);
  render(<ResourceCard item={item} />);
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  await act(async () => {
    auth.generation++;
    for (const fn of auth.listeners) fn();
    finish('private A body');
    await body;
  });
  expect(screen.queryByText('private A body')).toBeNull();
  expect(vi.mocked(fetch).mock.calls[0][1]?.signal?.aborted).toBe(true);
});
it('metadata-only does not offer a fake content download', () => {
  render(
    <ResourceCard
      item={{
        ...item,
        measured_bytes: null,
        representation: 'metadata_only',
        availability: 'original_not_received',
      }}
    />,
  );
  expect(screen.queryByRole('button', { name: 'Download' })).toBeNull();
  expect(
    screen.getByText('Only submitted metadata was received.'),
  ).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
});
it('loads pinned safe file fallback for unknown component versions without executing controls', async () => {
  vi.mocked(fetch).mockImplementation(
    async (path) =>
      new Response(
        JSON.stringify(
          String(path).endsWith('/inputs')
            ? { items: [], next_cursor: null }
            : String(path).endsWith('/presentations')
              ? {
                  items: [
                    {
                      schema_version: 99,
                      component_library_version: 99,
                      component: 'unknown',
                      items: [
                        {
                          resource_id: item.resource_id,
                          version_id: item.version_id,
                        },
                      ],
                    },
                  ],
                }
              : { item },
        ),
      ),
  );
  render(<TaskResources taskId={item.resource_id} />);
  await screen.findByText('received.txt');
  expect(fetch).toHaveBeenCalledTimes(3);
});
it('catalog load-more and restart never call generation', async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          schema_version: 1,
          items: [item],
          next_cursor: 'opaque',
        }),
      ),
    )
    .mockResolvedValueOnce(new Response('{}', { status: 409 }))
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({ schema_version: 1, items: [], next_cursor: null }),
      ),
    );
  render(<ResourceGallery />);
  await screen.findByText('received.txt');
  fireEvent.click(screen.getByRole('button', { name: 'Load more' }));
  await screen.findByRole('button', { name: 'Restart' });
  fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
  await screen.findByText('No retained versions match these filters.');
  expect(
    vi
      .mocked(fetch)
      .mock.calls.every(([path]) => String(path).startsWith('/api/resources?')),
  ).toBe(true);
});

it('appends task input pages without dropping earlier cards or duplicating pins', async () => {
  const second = {
    ...item,
    version_id: '33333333-3333-4333-8333-333333333333',
    display: { name: 'second.txt', title: null },
  };
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  vi.mocked(fetch).mockImplementation(async (path) => {
    if (String(path).includes('?cursor=')) return pending;
    return new Response(
      JSON.stringify(
        String(path).endsWith('/inputs')
          ? { items: [item], next_cursor: 'next' }
          : { items: [] },
      ),
    );
  });
  render(<TaskResources taskId={item.resource_id} />);
  await screen.findByText('received.txt');
  fireEvent.click(screen.getByRole('button', { name: 'Load more inputs' }));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
  expect(screen.getByText('received.txt')).toBeTruthy();
  await act(async () => {
    finish(
      new Response(
        JSON.stringify({ items: [item, second], next_cursor: null }),
      ),
    );
  });
  await screen.findByText('second.txt');
  expect(screen.getAllByText('received.txt')).toHaveLength(1);
  expect(screen.queryByRole('button', { name: 'Load more inputs' })).toBeNull();
});

it.each(['account', 'task'])(
  'discards an old pending input page on %s change',
  async (change) => {
    let finish!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    vi.mocked(fetch).mockImplementation(async (path) => {
      if (auth.generation !== 1) {
        if (String(path).includes('?cursor='))
          return new Response('{}', { status: 409 });
        return new Response(
          JSON.stringify({
            items: String(path).endsWith('/inputs')
              ? [{ ...item, display: { name: 'new-account.txt', title: null } }]
              : [],
            next_cursor: null,
          }),
        );
      }
      if (String(path).includes('?cursor=')) return pending;
      return new Response(
        JSON.stringify(
          String(path).includes(item.resource_id) &&
            String(path).endsWith('/inputs')
            ? { items: [item], next_cursor: 'next' }
            : { items: [], next_cursor: null },
        ),
      );
    });
    const view = render(<TaskResources taskId={item.resource_id} />);
    await screen.findByText('received.txt');
    fireEvent.click(screen.getByRole('button', { name: 'Load more inputs' }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
    const signal = vi.mocked(fetch).mock.calls[2][1]?.signal;
    await act(async () => {
      if (change === 'account') {
        auth.generation++;
        for (const fn of auth.listeners) fn();
      } else
        view.rerender(
          <TaskResources taskId="44444444-4444-4444-8444-444444444444" />,
        );
    });
    expect(signal?.aborted).toBe(true);
    expect(screen.queryByText('received.txt')).toBeNull();
    if (change === 'account') {
      await screen.findByText('new-account.txt');
      expect(
        vi
          .mocked(fetch)
          .mock.calls.slice(3)
          .every(([path]) => !String(path).includes('?cursor=')),
      ).toBe(true);
    }
    await act(async () => {
      finish(
        new Response(JSON.stringify({ items: [item], next_cursor: null })),
      );
    });
    expect(screen.queryByText('received.txt')).toBeNull();
  },
);
