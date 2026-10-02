import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryActions } from '../components/settings/memory/MemoryActions';
import {
  MAX_USER_MEMORY_LENGTH,
  toMemoryExport,
  useMemories,
  type MemoryExport,
} from '../hooks/useMemories';

vi.mock('../lib/auth', () => ({
  ensureAuthHeader: async () => 'Bearer test-memory',
}));

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

let requests: Array<{ url: string; init?: RequestInit }>;
let respond: (url: string, init?: RequestInit) => Response;

beforeEach(() => {
  requests = [];
  respond = () => json({});
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (!init?.method) return json({ memories: [], total: 0 });
      requests.push({ url, init });
      return respond(url, init);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('memory export shape', () => {
  it('keeps only portable fields and drops IDs, embeddings and internals', () => {
    const exported = toMemoryExport(
      [
        {
          id: 'm1',
          user_id: 'u1',
          content: 'Prefers metric units',
          category: 'preference',
          embedding: [0.1, 0.2],
          content_hash: 'abc',
          local_only: false,
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-02T00:00:00Z',
        },
        { id: 'm2', content: '', category: 'fact' },
        { id: 'm3', content: 'No category' },
      ],
      new Date('2026-10-03T00:00:00Z'),
    );
    expect(exported).toEqual({
      format: 'daemon-memories',
      version: 1,
      exported_at: '2026-10-03T00:00:00.000Z',
      status: 'active',
      memories: [
        {
          content: 'Prefers metric units',
          category: 'preference',
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-02T00:00:00Z',
        },
        {
          content: 'No category',
          category: 'fact',
          created_at: null,
          updated_at: null,
        },
      ],
    });
  });
});

describe('useMemories create and export', () => {
  it('creates a trimmed memory with the chosen category and auth', async () => {
    respond = () => json({ id: 'new-id', status: 'created' });
    const { result } = renderHook(() => useMemories());
    let outcome: Awaited<ReturnType<typeof result.current.createMemory>>;
    await act(async () => {
      outcome = await result.current.createMemory(
        '  I live in Adelaide  ',
        'fact',
      );
    });
    expect(outcome!).toEqual({ ok: true, id: 'new-id' });
    const call = requests.find((r) => r.init?.method === 'POST')!;
    expect(call.url.endsWith('/memories')).toBe(true);
    expect(JSON.parse(String(call.init!.body))).toEqual({
      content: 'I live in Adelaide',
      category: 'fact',
    });
    expect(new Headers(call.init!.headers).get('Authorization')).toBe(
      'Bearer test-memory',
    );
  });

  it('rejects empty and over-long memories without a request', async () => {
    const { result } = renderHook(() => useMemories());
    await act(async () => {
      expect(await result.current.createMemory('   ', 'fact')).toMatchObject({
        ok: false,
      });
      expect(
        await result.current.createMemory(
          'x'.repeat(MAX_USER_MEMORY_LENGTH + 1),
          'fact',
        ),
      ).toMatchObject({ ok: false });
    });
    expect(requests).toHaveLength(0);
  });

  it('reports server failures without claiming success', async () => {
    respond = () => json({ detail: 'down' }, 503);
    const { result } = renderHook(() => useMemories());
    await act(async () => {
      expect(await result.current.createMemory('note', 'fact')).toEqual({
        ok: false,
        error: 'Memory is unavailable right now. Please try again later.',
      });
    });
  });

  it('exports active memories through the export route', async () => {
    respond = () =>
      json({
        memories: [{ id: 'm', content: 'Likes tea', category: 'preference' }],
      });
    const { result } = renderHook(() => useMemories());
    let exported: MemoryExport;
    await act(async () => {
      exported = await result.current.exportMemories();
    });
    const call = requests.at(-1)!;
    expect(call.url.endsWith('/memories/export')).toBe(true);
    expect(JSON.parse(String(call.init!.body))).toEqual({ status: 'active' });
    expect(exported!.memories).toEqual([
      {
        content: 'Likes tea',
        category: 'preference',
        created_at: null,
        updated_at: null,
      },
    ]);
  });
});

describe('MemoryActions', () => {
  function setup(overrides: Partial<Parameters<typeof MemoryActions>[0]> = {}) {
    const props = {
      createMemory: vi.fn(async () => ({ ok: true as const, id: 'id' })),
      exportMemories: vi.fn(async () =>
        toMemoryExport(
          [{ content: 'A' }, { content: 'B' }],
          new Date('2026-10-03T12:00:00Z'),
        ),
      ),
      onSaved: vi.fn(),
      ...overrides,
    };
    render(<MemoryActions {...props} />);
    return props;
  }

  it('saves, clears the field, refreshes and explains possible merging', async () => {
    const props = setup();
    const field = screen.getByLabelText('Add a memory');
    const save = screen.getByRole('button', { name: 'Save memory' });
    expect((save as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(field, { target: { value: 'Allergic to peanuts' } });
    fireEvent.change(screen.getByLabelText('Category'), {
      target: { value: 'preference' },
    });
    fireEvent.click(save);

    expect(await screen.findByRole('status')).toHaveProperty(
      'textContent',
      'Saved to memory. If it repeated something already remembered, the two were merged.',
    );
    expect(props.createMemory).toHaveBeenCalledWith(
      'Allergic to peanuts',
      'preference',
    );
    expect((field as HTMLTextAreaElement).value).toBe('');
    expect(props.onSaved).toHaveBeenCalledTimes(1);
  });

  it('keeps the text and shows the error when saving fails', async () => {
    const props = setup({
      createMemory: vi.fn(async () => ({
        ok: false as const,
        error: "Couldn't save the memory. Please try again.",
      })),
    });
    const field = screen.getByLabelText('Add a memory');
    fireEvent.change(field, { target: { value: 'Keep me' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save memory' }));

    expect((await screen.findByRole('alert')).textContent).toBe(
      "Couldn't save the memory. Please try again.",
    );
    expect((field as HTMLTextAreaElement).value).toBe('Keep me');
    expect(props.onSaved).not.toHaveBeenCalled();
  });

  it('blocks over-long input and marks it invalid', () => {
    setup();
    const field = screen.getByLabelText('Add a memory');
    fireEvent.change(field, {
      target: { value: 'x'.repeat(MAX_USER_MEMORY_LENGTH + 1) },
    });
    expect(field.getAttribute('aria-invalid')).toBe('true');
    expect(
      (screen.getByRole('button', { name: 'Save memory' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it('downloads a dated JSON file and reports the count', async () => {
    const created: Blob[] = [];
    vi.spyOn(URL, 'createObjectURL').mockImplementation((blob) => {
      created.push(blob as Blob);
      return 'blob:memories';
    });
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const clicks: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicks.push(this.download);
    });
    setup();
    fireEvent.click(screen.getByRole('button', { name: 'Export JSON' }));

    expect((await screen.findByRole('status')).textContent).toBe(
      'Exported 2 active memories.',
    );
    expect(clicks).toEqual(['daemon-memories-2026-10-03.json']);
    expect(created[0].type).toBe('application/json');
  });

  it('reports a failed export', async () => {
    setup({
      exportMemories: vi.fn(async () => Promise.reject(new Error('x'))),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Export JSON' }));
    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toBe(
        "Couldn't export memories. Please try again.",
      ),
    );
  });
});
