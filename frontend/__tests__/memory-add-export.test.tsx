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
  MemoryExportFormatError,
  MemoryRequestSupersededError,
  toMemoryExport,
  useMemories,
  type MemoryExport,
} from '../hooks/useMemories';

const authState = vi.hoisted(() => ({
  generation: 1,
  listeners: new Set<() => void>(),
}));
vi.mock('../lib/auth', () => ({
  ensureAuthHeader: async () => 'Bearer test-memory',
  getAuthGeneration: () => authState.generation,
  subscribeAuthGeneration: (listener: () => void) => {
    authState.listeners.add(listener);
    return () => authState.listeners.delete(listener);
  },
}));

/** Simulates sign-out, cross-tab sign-out or a new sign-in. */
function changeSignIn() {
  authState.generation += 1;
  for (const listener of [...authState.listeners]) listener();
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

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
        { id: 'm3', content: 'No dates', category: 'fact' },
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
          content: 'No dates',
          category: 'fact',
          created_at: null,
          updated_at: null,
        },
      ],
    });
  });

  it('treats an empty array as a valid empty export', () => {
    expect(toMemoryExport([]).memories).toEqual([]);
  });

  it.each([
    ['missing list', undefined],
    ['null list', null],
    ['object instead of list', { memories: [] }],
    ['non-object row', ['text']],
    ['row without content', [{ category: 'fact' }]],
    ['row with empty content', [{ content: '', category: 'fact' }]],
    ['row without category', [{ content: 'x' }]],
    [
      'row with a non-string date',
      [{ content: 'x', category: 'fact', created_at: 7 }],
    ],
  ])('rejects a malformed payload: %s', (_name, rows) => {
    expect(() => toMemoryExport(rows)).toThrow(MemoryExportFormatError);
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
  it.each([
    ['missing memories', {}],
    ['null memories', { memories: null }],
    ['non-array memories', { memories: 'none' }],
  ])(
    'rejects a successful response with %s instead of exporting nothing',
    async (_name, body) => {
      respond = () => json(body);
      const { result } = renderHook(() => useMemories());
      await expect(result.current.exportMemories()).rejects.toBeInstanceOf(
        MemoryExportFormatError,
      );
    },
  );

  it('drops an export whose sign-in changed before the response arrived', async () => {
    const gate = deferred<Response>();
    respond = () => gate.promise as unknown as Response;
    const { result } = renderHook(() => useMemories());
    const pending = result.current.exportMemories();
    await waitFor(() => expect(requests).toHaveLength(1));
    act(() => changeSignIn());
    gate.resolve(json({ memories: [{ content: 'Old', category: 'fact' }] }));
    await expect(pending).rejects.toBeInstanceOf(MemoryRequestSupersededError);
  });

  it('drops a save whose caller was aborted', async () => {
    const gate = deferred<Response>();
    respond = () => gate.promise as unknown as Response;
    const { result } = renderHook(() => useMemories());
    const controller = new AbortController();
    const pending = result.current.createMemory('note', 'fact', {
      signal: controller.signal,
    });
    await waitFor(() => expect(requests).toHaveLength(1));
    controller.abort();
    gate.resolve(json({ id: 'x' }));
    await expect(pending).rejects.toBeInstanceOf(MemoryRequestSupersededError);
  });
});

describe('MemoryActions', () => {
  function setup(overrides: Partial<Parameters<typeof MemoryActions>[0]> = {}) {
    const props = {
      createMemory: vi.fn(async () => ({ ok: true as const, id: 'id' })),
      exportMemories: vi.fn(async () =>
        toMemoryExport(
          [
            { content: 'A', category: 'fact' },
            { content: 'B', category: 'fact' },
          ],
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
      expect.anything(),
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

describe('MemoryActions lifecycle', () => {
  function stubDownload() {
    const downloads: string[] = [];
    vi.spyOn(URL, 'createObjectURL').mockImplementation(() => 'blob:x');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      downloads.push(this.download);
    });
    return downloads;
  }

  function renderActions(overrides: Record<string, unknown> = {}) {
    const props = {
      createMemory: vi.fn(),
      exportMemories: vi.fn(),
      onSaved: vi.fn(),
      ...overrides,
    } as unknown as Parameters<typeof MemoryActions>[0];
    return { props, ...render(<MemoryActions {...props} />) };
  }

  it('locks the draft while saving and never erases text it did not send', async () => {
    const gate = deferred<{ ok: true; id: string }>();
    const createMemory = vi.fn(() => gate.promise);
    renderActions({ createMemory });
    const field = screen.getByLabelText('Add a memory') as HTMLTextAreaElement;
    const select = screen.getByLabelText('Category') as HTMLSelectElement;
    fireEvent.change(field, { target: { value: 'A' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save memory' }));

    expect(field.readOnly).toBe(true);
    expect(select.disabled).toBe(true);
    fireEvent.change(field, { target: { value: 'B typed during save' } });
    fireEvent.change(select, { target: { value: 'project' } });
    expect(field.value).toBe('A');
    expect(select.value).toBe('fact');

    await act(async () => gate.resolve({ ok: true, id: 'a' }));
    expect(createMemory).toHaveBeenCalledWith('A', 'fact', expect.anything());
    expect(field.value).toBe('');
    expect(field.readOnly).toBe(false);
  });

  it.each([
    ['the view unmounts', 'unmount'],
    ['the person signs out or another tab signs out', 'signout'],
    ['a different account signs in', 'signin'],
  ])('does not download or report an export after %s', async (_name, how) => {
    const downloads = stubDownload();
    const gate = deferred<MemoryExport>();
    let signal: AbortSignal | undefined;
    const exportMemories = vi.fn((options?: { signal?: AbortSignal }) => {
      signal = options?.signal;
      return gate.promise;
    });
    const { unmount } = renderActions({ exportMemories });
    fireEvent.click(screen.getByRole('button', { name: 'Export JSON' }));
    await waitFor(() => expect(exportMemories).toHaveBeenCalled());

    if (how === 'unmount') unmount();
    else act(() => changeSignIn());
    expect(signal?.aborted).toBe(true);
    await act(async () =>
      gate.resolve(
        toMemoryExport([{ content: 'Old account', category: 'fact' }]),
      ),
    );

    expect(downloads).toEqual([]);
    if (how !== 'unmount') {
      expect(screen.queryByTestId('memory-action-outcome')).toBeNull();
      expect(
        (
          screen.getByRole('button', {
            name: 'Export JSON',
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(false);
    }
  });

  it('clears an unsaved draft when the sign-in changes', () => {
    renderActions();
    const field = screen.getByLabelText('Add a memory') as HTMLTextAreaElement;
    fireEvent.change(field, { target: { value: 'previous account note' } });
    act(() => changeSignIn());
    expect(field.value).toBe('');
  });

  it('reports a malformed export without downloading anything', async () => {
    const downloads = stubDownload();
    renderActions({
      exportMemories: vi.fn(async () => {
        throw new MemoryExportFormatError();
      }),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Export JSON' }));
    expect((await screen.findByRole('alert')).textContent).toBe(
      'Daemon sent an unexpected export, so nothing was downloaded. Please try again.',
    );
    expect(downloads).toEqual([]);
  });
});
