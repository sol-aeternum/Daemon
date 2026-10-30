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
