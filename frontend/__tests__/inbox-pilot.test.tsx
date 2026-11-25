import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { InboxPilot } from '@/app/inbox/InboxPilot';

const auth = vi.hoisted(() => ({
  generation: 1,
  renderHeader: 'Bearer render-time' as string | null,
  ensureHeader: vi.fn<() => Promise<string | null>>(),
}));
vi.mock('@/hooks/useAuthGeneration', () => ({
  useAuthGeneration: () => auth.generation,
}));
vi.mock('@/lib/auth', () => ({
  getAuthGeneration: () => auth.generation,
  ensureAuthHeader: auth.ensureHeader,
}));
vi.mock('@/components/AuthProvider', () => ({
  useAuth: () => ({
    isAuthenticated: true,
    authHeader: auth.renderHeader,
  }),
}));

const endpoint = 'http://127.0.0.1:9999/tasks/inbox-pilot';
const reportId = 'b6a96dbf-f749-49ab-bb67-27991aab8688';

beforeEach(() => {
  auth.generation = 1;
  auth.renderHeader = 'Bearer render-time';
  auth.ensureHeader.mockReset();
  auth.ensureHeader.mockImplementation(
    async () => `Bearer synthetic-${auth.generation}`,
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function send(text: string) {
  fireEvent.change(screen.getByRole('textbox', { name: 'Message Daemon' }), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
}

it('keeps a lost start unacknowledged and follows the original with reads only', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ state: 'empty' }))
    .mockRejectedValueOnce(Error('Lost response'))
    .mockResolvedValueOnce(Response.json({ state: 'running' }))
    .mockResolvedValueOnce(
      Response.json({ state: 'completed', conversation_id: reportId }),
    )
    .mockResolvedValueOnce(new Response(null, { status: 503 }));
  vi.stubGlobal('fetch', fetcher);

  render(<InboxPilot endpoint={endpoint} />);
  await screen.findByText('No research request yet.');
  send('Do a deep dive on X.');
  await screen.findByText('Status unavailable');
  expect(screen.queryByText('Accepted.')).toBeNull();

  send('Do a deep dive on X.');
  expect(fetcher).toHaveBeenCalledTimes(2);
  send("How's that going?");
  await screen.findByText('Last saved as running.');
  expect(screen.queryByRole('link', { name: 'Open report' })).toBeNull();

  send('How’s that going?');
  const link = await screen.findByRole('link', { name: 'Open report' });
  expect(link.getAttribute('href')).toBe(`/?id=${reportId}`);
  send('How’s that going?');
  expect(screen.queryByRole('link', { name: 'Open report' })).toBeNull();
  await screen.findByText('Status unavailable');
  expect(fetcher.mock.calls.map(([, init]) => init.method)).toEqual([
    'GET',
    'POST',
    'GET',
    'GET',
    'GET',
  ]);
});

it('does not publish a late report under a different sign-in generation', async () => {
  let resolvePrior!: (value: Response) => void;
  const prior = new Promise<Response>((resolve) => {
    resolvePrior = resolve;
  });
  const fetcher = vi
    .fn()
    .mockReturnValueOnce(prior)
    .mockResolvedValueOnce(Response.json({ state: 'empty' }));
  vi.stubGlobal('fetch', fetcher);

  const { rerender } = render(<InboxPilot endpoint={endpoint} />);
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  auth.generation += 1;
  rerender(<InboxPilot endpoint={endpoint} />);
  await screen.findByText('No research request yet.');
  await act(async () => {
    resolvePrior(
      Response.json({ state: 'completed', conversation_id: reportId }),
    );
  });
  expect(screen.queryByRole('link', { name: 'Open report' })).toBeNull();
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
});

it('resolves a current bearer for every follow-up and never replays a refused start', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ state: 'empty' }))
    .mockResolvedValueOnce(new Response(null, { status: 401 }))
    .mockResolvedValueOnce(Response.json({ state: 'running' }))
    .mockResolvedValueOnce(
      Response.json({ state: 'completed', conversation_id: reportId }),
    );
  vi.stubGlobal('fetch', fetcher);
  const { rerender } = render(<InboxPilot endpoint={endpoint} />);
  await screen.findByText('No research request yet.');
  // An unrelated render can observe expired header state even though the
  // signed-in refresh session remains valid.
  auth.renderHeader = null;
  rerender(<InboxPilot endpoint={endpoint} />);
  auth.ensureHeader.mockResolvedValueOnce('Bearer rotated-start');
  send('Do a deep dive on X.');
  await screen.findByText('Status unavailable');
  expect(fetcher).toHaveBeenCalledTimes(2);
  auth.ensureHeader.mockResolvedValueOnce('Bearer rotated-followup');
  send('How’s that going?');
  await screen.findByText('Last saved as running.');
  auth.ensureHeader.mockResolvedValueOnce('Bearer rotated-report');
  send('How’s that going?');
  await screen.findByRole('link', { name: 'Open report' });
  expect(
    fetcher.mock.calls.map(([, init]) => [
      init.method,
      init.headers.Authorization,
    ]),
  ).toEqual([
    ['GET', 'Bearer synthetic-1'],
    ['POST', 'Bearer rotated-start'],
    ['GET', 'Bearer rotated-followup'],
    ['GET', 'Bearer rotated-report'],
  ]);
});

it('does not send an old start if sign-in changes during header resolution', async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockImplementation(async () => Response.json({ state: 'empty' }));
  vi.stubGlobal('fetch', fetcher);
  const { rerender } = render(<InboxPilot endpoint={endpoint} />);
  await screen.findByText('No research request yet.');
  let release!: (value: string) => void;
  auth.ensureHeader.mockReturnValueOnce(
    new Promise((resolve) => {
      release = resolve;
    }),
  );
  send('Do a deep dive on X.');
  auth.generation += 1;
  rerender(<InboxPilot endpoint={endpoint} />);
  await screen.findByText('No research request yet.');
  await act(async () => release('Bearer rotated-old-account'));
  expect(fetcher.mock.calls.map(([, init]) => init?.method)).toEqual([
    'GET',
    'GET',
  ]);
  expect(
    new Headers(fetcher.mock.calls[1][1]?.headers).get('Authorization'),
  ).toBe('Bearer synthetic-2');
});

it('fails closed without issuing a request when header resolution refuses', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(Response.json({ state: 'running' }));
  vi.stubGlobal('fetch', fetcher);
  render(<InboxPilot endpoint={endpoint} />);
  await screen.findByText('Last saved as running.');
  auth.ensureHeader.mockResolvedValueOnce(null);
  send('How’s that going?');
  await screen.findByText('Status unavailable');
  expect(fetcher).toHaveBeenCalledTimes(1);
});
