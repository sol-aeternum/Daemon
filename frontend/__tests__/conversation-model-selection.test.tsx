import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useLayoutEffect } from 'react';
import {
  useConversationHistory,
  type ModelSaveResult,
} from '../hooks/useConversationHistory';

// Conversation model selection (#231): the saved choice is read back from the
// typed `selected_model` contract, and only server-accepted saves stick.

const state = vi.hoisted(() => ({
  id: '00000000-0000-0000-0000-000000009999',
  generation: 1,
  listeners: new Set<() => void>(),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(state.id ? { id: state.id } : {}),
}));
vi.mock('../lib/auth', () => ({
  getAuthHeader: () => 'Bearer fictional-model-selection',
  refreshIfNeeded: vi.fn(),
  getAuthGeneration: () => state.generation,
  subscribeAuthGeneration: (callback: () => void) => {
    state.listeners.add(callback);
    return () => state.listeners.delete(callback);
  },
}));

let latest: ReturnType<typeof useConversationHistory>;
const id = (n: number) =>
  `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`;
const A = id(9999);
const B = id(9998);
const SONNET = 'openrouter/test/sonnet';
const OPUS = 'openrouter/test/opus';
const at = '2026-10-29T00:00:00.123456Z';
const row = (conversation: string, selected?: string) => ({
  id: conversation,
  user_id: id(9997),
  title: conversation === A ? 'Chat A' : 'Chat B',
  created_at: at,
  updated_at: at,
  last_activity_at: at,
  pinned: false,
  ...(selected === undefined ? {} : { selected_model: selected }),
});
const detail = (conversation: string, selected?: string) => ({
  ...row(conversation, selected),
  messages: [],
  message_page: {
    has_more: false,
    next_cursor: null,
    start_cursor: null,
    end_cursor: null,
  },
});
const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status });
const list = () =>
  json({
    conversations: [],
    total: 0,
    page: {
      has_more: false,
      next_cursor: null,
      start_cursor: null,
      end_cursor: null,
    },
  });
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

interface Patch {
  conversation: string;
  body: Record<string, unknown>;
  response: ReturnType<typeof deferred<Response>>;
}

/** Server with a saved model per conversation and hand-resolved PATCHes. */
function installServer(saved: Record<string, string | undefined>) {
  const patches: Patch[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input, init?: RequestInit) => {
      const url = new URL(String(input), 'http://test');
      if (url.pathname === '/conversations') return list();
      const conversation = url.pathname.split('/').at(-1)!;
      if (init?.method === 'PATCH') {
        const patch: Patch = {
          conversation,
          body: JSON.parse(String(init.body)),
          response: deferred<Response>(),
        };
        patches.push(patch);
        return patch.response.promise;
      }
      return json(detail(conversation, saved[conversation]));
    }),
  );
  return patches;
}

function Probe() {
  const history = useConversationHistory();
  useLayoutEffect(() => {
    latest = history;
  });
  const current = history.getCurrentConversation();
  return <p>selected:{current ? current.title : 'none'}</p>;
}

const flush = () => act(async () => {});
const select = (conversation: string, model: string, current = 'auto') => {
  let result!: Promise<ModelSaveResult>;
  act(() => {
    result = latest.setConversationModel(conversation, model, current);
  });
  return result;
};
const shown = (conversation: string, loaded: string) =>
  latest.resolveSelectedModel({ id: conversation, selectedModel: loaded });

beforeEach(() => {
  state.id = A;
  state.generation = 1;
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  expect(state.listeners.size).toBe(0);
});

describe('conversation model selection', () => {
  it('restores the saved model on reload and in a second client', async () => {
    installServer({ [A]: SONNET });
    for (const _client of ['first', 'second']) {
      const mounted = render(<Probe />);
      await screen.findByText('selected:Chat A');
      expect(latest.getCurrentConversation()?.selectedModel).toBe(SONNET);
      mounted.unmount();
    }
  });

  it('defaults to auto when nothing is saved', async () => {
    installServer({});
    render(<Probe />);
    await screen.findByText('selected:Chat A');
    expect(latest.getCurrentConversation()?.selectedModel).toBe('auto');
  });

  it('saves only the typed selected_model field', async () => {
    const patches = installServer({ [A]: 'auto' });
    render(<Probe />);
    await screen.findByText('selected:Chat A');
    const result = select(A, SONNET);
    await flush();
    expect(patches.map((patch) => patch.body)).toEqual([
      { selected_model: SONNET },
    ]);
    patches[0].response.resolve(json({ status: 'updated' }));
    await expect(result).resolves.toEqual({ status: 'saved' });
  });

  it.each([
    [
      'a rejected model (422)',
      () =>
        json(
          {
            detail: {
              code: 'model_not_selectable',
              message: 'That model is not available for selection',
            },
          },
          422,
        ),
      "That model isn't available",
    ],
    [
      'a server error (500)',
      () => json({ detail: 'boom' }, 500),
      "Couldn't save",
    ],
  ])(
    'reports %s as a failure and reverts, never as success',
    async (_name, response, message) => {
      const patches = installServer({ [A]: 'auto' });
      render(<Probe />);
      await screen.findByText('selected:Chat A');
      const result = select(A, SONNET, 'auto');
      await flush();
      expect(shown(A, 'auto')).toBe(SONNET);
      patches[0].response.resolve(response());
      const outcome = await result;
      expect(outcome).toMatchObject({ status: 'failed', revertTo: 'auto' });
      expect(outcome.status === 'failed' && outcome.message).toContain(message);
      expect(shown(A, 'auto')).toBe('auto');
    },
  );

  it('reports a network failure and reverts', async () => {
    const patches = installServer({ [A]: OPUS });
    render(<Probe />);
    await screen.findByText('selected:Chat A');
    const result = select(A, SONNET, OPUS);
    await flush();
    patches[0].response.reject(new TypeError('offline'));
    await expect(result).resolves.toMatchObject({
      status: 'failed',
      revertTo: OPUS,
    });
  });

  it('sends rapid choices in order and never sends a replaced queued choice', async () => {
    const patches = installServer({ [A]: 'auto' });
    render(<Probe />);
    await screen.findByText('selected:Chat A');
    const first = select(A, SONNET);
    await flush();
    const replaced = select(A, OPUS);
    const last = select(A, 'auto');
    await flush();
    // One save at a time: the server applies choices in the order made.
    expect(patches).toHaveLength(1);
    expect(shown(A, 'auto')).toBe('auto');
    patches[0].response.resolve(json({ status: 'updated' }));
    await expect(first).resolves.toEqual({ status: 'superseded' });
    await expect(replaced).resolves.toEqual({ status: 'superseded' });
    await flush();
    expect(patches.map((patch) => patch.body.selected_model)).toEqual([
      SONNET,
      'auto',
    ]);
    patches[1].response.resolve(json({ status: 'updated' }));
    await expect(last).resolves.toEqual({ status: 'saved' });
  });

  it('coalesces choices made before the first save starts', async () => {
    const patches = installServer({ [A]: 'auto' });
    render(<Probe />);
    await screen.findByText('selected:Chat A');
    const first = select(A, SONNET);
    const last = select(A, OPUS);
    await flush();
    expect(patches.map((patch) => patch.body)).toEqual([
      { selected_model: OPUS },
    ]);
    patches[0].response.resolve(json({ status: 'updated' }));
    await expect(first).resolves.toEqual({ status: 'superseded' });
    await expect(last).resolves.toEqual({ status: 'saved' });
  });

  it('ignores an older rejected save once a newer choice is queued', async () => {
    const patches = installServer({ [A]: 'auto' });
    render(<Probe />);
    await screen.findByText('selected:Chat A');
    const older = select(A, SONNET);
    await flush();
    const newer = select(A, OPUS);
    await flush();
    patches[0].response.resolve(json({ detail: 'boom' }, 500));
    await expect(older).resolves.toEqual({ status: 'superseded' });
    await flush();
    patches[1].response.resolve(json({ status: 'updated' }));
    await expect(newer).resolves.toEqual({ status: 'saved' });
    expect(shown(A, 'auto')).toBe(OPUS);
  });

  it('reverts a rejected newer save to the last accepted one', async () => {
    const patches = installServer({ [A]: 'auto' });
    render(<Probe />);
    await screen.findByText('selected:Chat A');
    const accepted = select(A, SONNET);
    await flush();
    patches[0].response.resolve(json({ status: 'updated' }));
    await expect(accepted).resolves.toEqual({ status: 'saved' });
    const rejected = select(A, OPUS);
    await flush();
    patches[1].response.resolve(json({ detail: 'boom' }, 500));
    await expect(rejected).resolves.toMatchObject({
      status: 'failed',
      revertTo: SONNET,
    });
  });

  it('keeps a stale server read from overwriting a pending or just-saved choice', async () => {
    const patches = installServer({ [A]: 'auto' });
    render(<Probe />);
    await screen.findByText('selected:Chat A');
    const result = select(A, SONNET);
    await flush();
    // A refresh that read the row before the save lands still says auto.
    expect(shown(A, 'auto')).toBe(SONNET);
    patches[0].response.resolve(json({ status: 'updated' }));
    await result;
    expect(shown(A, 'auto')).toBe(SONNET);
    // Once a read echoes the save, later server changes (another client) apply.
    expect(shown(A, SONNET)).toBe(SONNET);
    expect(shown(A, OPUS)).toBe(OPUS);
  });

  it('scopes a failure to its own conversation after switching', async () => {
    const patches = installServer({ [A]: 'auto', [B]: OPUS });
    render(<Probe />);
    await screen.findByText('selected:Chat A');
    const result = select(A, SONNET);
    await flush();
    expect(shown(B, OPUS)).toBe(OPUS);
    patches[0].response.resolve(json({ detail: 'boom' }, 500));
    await expect(result).resolves.toMatchObject({ status: 'failed' });
    expect(shown(B, OPUS)).toBe(OPUS);
    expect(shown(A, 'auto')).toBe('auto');
  });

  it('drops a save that completes after the account changed', async () => {
    const patches = installServer({ [A]: 'auto' });
    render(<Probe />);
    await screen.findByText('selected:Chat A');
    const result = select(A, SONNET);
    await flush();
    act(() => {
      state.generation += 1;
      for (const callback of state.listeners) callback();
    });
    patches[0].response.resolve(json({ detail: 'boom' }, 500));
    await expect(result).resolves.toEqual({ status: 'superseded' });
    // The new account's reads are never overridden by the old account's save.
    expect(shown(A, OPUS)).toBe(OPUS);
  });
});
