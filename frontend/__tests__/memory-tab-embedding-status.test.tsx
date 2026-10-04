import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import MemoryTab from '../components/settings/MemoryTab';

const authState = vi.hoisted(() => ({
  generation: 1,
  listeners: new Set<() => void>(),
}));

vi.mock('../lib/auth', () => ({
  ensureAuthHeader: async () => 'Bearer test-browser',
  getAuthGeneration: () => authState.generation,
  subscribeAuthGeneration: (listener: () => void) => {
    authState.listeners.add(listener);
    return () => {
      authState.listeners.delete(listener);
    };
  },
}));

interface MemoryRow {
  id: string;
  content: string;
  category: string;
  status: string;
  source_type: string;
  conversation_id: null;
  created_at: string;
  updated_at: string;
  confirmed: boolean;
}

function row(index: number, overrides: Partial<MemoryRow> = {}): MemoryRow {
  return {
    id: `m-${String(index).padStart(3, '0')}`,
    content: `Memory number ${index}`,
    category: 'fact',
    status: 'active',
    source_type: 'extracted',
    conversation_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    confirmed: true,
    ...overrides,
  };
}

const rows: MemoryRow[] = Array.from({ length: 5 }, (_, i) => row(i + 1));

type StatusHandler = () => Response | Promise<Response>;

let statusHandler: StatusHandler | null = null;
let statusRequests = 0;

function memoriesResponse(url: URL): Response {
  const limit = Number(url.searchParams.get('limit') ?? 20);
  const offset = Number(url.searchParams.get('offset') ?? 0);
  return new Response(
    JSON.stringify({
      memories: rows.slice(offset, offset + limit),
      total: rows.length,
      has_more: false,
      limit,
      offset,
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  );
}

function statusResponse(payload: unknown, ok = true): Response {
  return new Response(JSON.stringify(payload), {
    status: ok ? 200 : 503,
    headers: { 'Content-Type': 'application/json' },
  });
}

async function route(input: RequestInfo | URL): Promise<Response> {
  const url = new URL(String(input), 'http://localhost');
  if (url.pathname === '/memories') {
    return memoriesResponse(url);
  }
  if (url.pathname === '/status') {
    statusRequests += 1;
    return statusHandler ? statusHandler() : statusResponse({});
  }
  throw new Error(`unexpected fetch ${url.pathname}`);
}

beforeEach(() => {
  statusHandler = null;
  statusRequests = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => route(input)),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  authState.generation = 1;
});

const UNAVAILABLE_STATUS = {
  db_healthy: true,
  embeddings: {
    observation_scope: 'backend_process',
    configuration: 'unavailable',
    reason_codes: ['missing_credentials', 'budget_adapter_unavailable'],
    last_outcome: 'never_attempted',
    last_success_at: null,
    last_failure_at: null,
  },
};

const ELIGIBLE_STATUS = {
  db_healthy: true,
  embeddings: {
    observation_scope: 'backend_process',
    configuration: 'eligible',
    reason_codes: ['budget_adapter_unavailable'],
    last_outcome: 'never_attempted',
    last_success_at: null,
    last_failure_at: null,
  },
};

function changeSignIn() {
  authState.generation += 1;
  for (const listener of [...authState.listeners]) listener();
}

function badge(): HTMLElement {
  return screen.getByLabelText(/^Memory embeddings:/);
}

describe('MemoryTab embedding semantic badge', () => {
  it('shows unknown, never green Ready, while /status is in flight', async () => {
    statusHandler = () => new Promise<Response>(() => {});
    render(<MemoryTab />);
    // The browser's own loading machinery (skeletons) is untouched while the
    // semantic fetch is in flight.
    expect(await screen.findByText('Memory number 1')).toBeDefined();
    expect(badge().textContent).toContain('Embeddings unknown');
    expect(screen.queryByText('Ready')).toBeNull();
    await waitFor(() => expect(statusRequests).toBe(1));
  });

  it('renders an unavailable payload with blocker details', async () => {
    statusHandler = () => statusResponse(UNAVAILABLE_STATUS);
    render(<MemoryTab />);
    expect(await screen.findByText('Embeddings unavailable')).toBeDefined();
    expect(badge().title).toContain('No account-budgeted embedding adapter');
    expect(badge().title).toContain('Embedding credentials missing');
  });

  it('renders eligible configuration as unverified, never operational-ready', async () => {
    statusHandler = () => statusResponse(ELIGIBLE_STATUS);
    render(<MemoryTab />);
    expect(await screen.findByText('Embeddings unverified')).toBeDefined();
    expect(badge().title).toContain('eligible but unverified');
  });

  it('keeps the semantic badge unknown when the /status fetch fails', async () => {
    statusHandler = () => Promise.reject(new Error('gateway down'));
    render(<MemoryTab />);
    await waitFor(() => expect(statusRequests).toBe(1));
    expect(await screen.findByText('Embeddings unknown')).toBeDefined();
    expect(screen.queryByText(/unavailable|unverified/i)).toBeNull();
  });

  it('treats an ok response without an embeddings object as unknown', async () => {
    statusHandler = () => statusResponse({ db_healthy: true });
    render(<MemoryTab />);
    expect(await screen.findByText('Embeddings unknown')).toBeDefined();
  });

  it('survives a malformed ok payload as unknown', async () => {
    statusHandler = () => new Response('<<not json>>', { status: 200 });
    render(<MemoryTab />);
    expect(await screen.findByText('Embeddings unknown')).toBeDefined();
  });

  it('never applies a stale cross-account /status response', async () => {
    const pending: Array<(response: Response) => void> = [];
    statusHandler = () =>
      new Promise<Response>((resolve) => {
        pending.push(resolve);
      });
    render(<MemoryTab />);
    await waitFor(() => expect(statusRequests).toBe(1));

    // Signing out/in clears the previous account immediately and re-reads
    // semantics for the new one.
    act(() => {
      changeSignIn();
    });
    await waitFor(() => expect(statusRequests).toBe(2));

    // The new account's payload is shown; the stale old-account payload is
    // never applied.
    await act(async () => {
      pending[0](statusResponse(UNAVAILABLE_STATUS));
      pending[1](statusResponse(ELIGIBLE_STATUS));
    });
    expect(await screen.findByText('Embeddings unverified')).toBeDefined();
    expect(screen.queryByText('Embeddings unavailable')).toBeNull();
  });

  it('drops the stale response even when it resolves after the fresh one', async () => {
    const pending: Array<(response: Response) => void> = [];
    statusHandler = () =>
      new Promise<Response>((resolve) => {
        pending.push(resolve);
      });
    render(<MemoryTab />);
    await waitFor(() => expect(statusRequests).toBe(1));

    act(() => {
      changeSignIn();
    });
    await waitFor(() => expect(statusRequests).toBe(2));

    // Fresh response first, then the stale one — the fresh state must win.
    await act(async () => {
      pending[1](statusResponse(ELIGIBLE_STATUS));
    });
    expect(await screen.findByText('Embeddings unverified')).toBeDefined();

    await act(async () => {
      pending[0](statusResponse(UNAVAILABLE_STATUS));
    });
    expect(await screen.findByText('Embeddings unverified')).toBeDefined();
    expect(screen.queryByText('Embeddings unavailable')).toBeNull();
  });

  it('clears loaded status immediately on sign-in change', async () => {
    statusHandler = () => statusResponse(UNAVAILABLE_STATUS);
    render(<MemoryTab />);
    expect(await screen.findByText('Embeddings unavailable')).toBeDefined();
    statusHandler = () => new Promise<Response>(() => {});
    act(changeSignIn);
    expect(badge().textContent).toContain('Embeddings unknown');
    await waitFor(() => expect(statusRequests).toBe(2));
  });

  it('drops a stale JSON body that finishes after the new status', async () => {
    let finishJson!: (payload: unknown) => void;
    const oldResponse = statusResponse({});
    vi.spyOn(oldResponse, 'json').mockImplementation(
      () =>
        new Promise((resolve) => {
          finishJson = resolve;
        }),
    );
    statusHandler = () => oldResponse;
    render(<MemoryTab />);
    await waitFor(() => expect(finishJson).toBeDefined());
    statusHandler = () => statusResponse(ELIGIBLE_STATUS);
    act(changeSignIn);
    expect(await screen.findByText('Embeddings unverified')).toBeDefined();
    await act(async () => finishJson(UNAVAILABLE_STATUS));
    expect(badge().textContent).toContain('Embeddings unverified');
  });

  it('ignores an old-account failure after the fresh status succeeds', async () => {
    let failOld!: (error: Error) => void;
    statusHandler = () =>
      new Promise((_resolve, reject) => {
        failOld = reject;
      });
    render(<MemoryTab />);
    await waitFor(() => expect(statusRequests).toBe(1));
    statusHandler = () => statusResponse(ELIGIBLE_STATUS);
    act(changeSignIn);
    expect(await screen.findByText('Embeddings unverified')).toBeDefined();
    await act(async () => failOld(new Error('old request failed')));
    expect(badge().textContent).toContain('Embeddings unverified');
  });
});
