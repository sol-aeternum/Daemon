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

it('keeps singleton inputs scoped to its original scripted topic', async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ state: 'empty' }));
  vi.stubGlobal('fetch', fetcher);
  render(<InboxPilot endpoint={endpoint} />);
  await screen.findByText('No research request yet.');
  send('Do a deep dive on Y.');
  send('Y');
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('rejects a singleton-shaped response after a verified multi-job response', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({
        state: 'ambiguous',
        choices: ['X', 'Y'],
        can_start: [],
      }),
    )
    .mockResolvedValueOnce(
      Response.json({ state: 'completed', conversation_id: reportId }),
    );
  vi.stubGlobal('fetch', fetcher);
  render(<InboxPilot endpoint={endpoint} />);
  await screen.findByText('Do you mean research X or Y?');
  send('How’s that going?');
  await screen.findByText('Status unavailable');
  expect(screen.queryByRole('link', { name: 'Open report' })).toBeNull();
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

it('clarifies ambiguous work and resolves explicit topics with reads only', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({
        state: 'ambiguous',
        choices: ['X', 'Y'],
        can_start: [],
      }),
    )
    .mockResolvedValueOnce(
      Response.json({
        state: 'completed',
        topic: 'X',
        conversation_id: reportId,
        can_start: ['Y'],
      }),
    )
    .mockResolvedValueOnce(
      Response.json({ state: 'running', topic: 'Y', can_start: [] }),
    );
  vi.stubGlobal('fetch', fetcher);

  render(<InboxPilot endpoint={endpoint} />);
  await screen.findByText('Do you mean research X or Y?');
  expect(screen.queryByRole('link', { name: 'Open report' })).toBeNull();

  // A scripted start is not verified while the server asks for clarification.
  send('Do a deep dive on X.');
  expect(fetcher).toHaveBeenCalledTimes(1);

  send('X');
  const link = await screen.findByRole('link', { name: 'Open report' });
  expect(link.getAttribute('href')).toBe(`/?id=${reportId}`);
  expect(link.textContent).toBe('Open report');
  await screen.findByText('X: Your report is ready.');
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[1][0]).toBe(`${endpoint}?topic=X`);

  send("How's Y going?");
  await screen.findByText('Y: Last saved as running.');
  expect(screen.queryByRole('link', { name: 'Open report' })).toBeNull();
  expect(fetcher.mock.calls[2][0]).toBe(`${endpoint}?topic=Y`);
  expect(fetcher.mock.calls.map(([, init]) => init.method)).toEqual([
    'GET',
    'GET',
    'GET',
  ]);
});

it('starts the second topic only after the first completes and never replays a lost start', async () => {
  const secondReport = 'c1f0f0e2-3b4a-4a9e-9f1d-2c7b8e6a5d40';
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({ state: 'empty', can_start: ['X', 'Y'] }),
    )
    .mockResolvedValueOnce(
      Response.json({ state: 'running', topic: 'X', can_start: [] }),
    )
    .mockResolvedValueOnce(
      Response.json({
        state: 'completed',
        topic: 'X',
        conversation_id: reportId,
        can_start: ['Y'],
      }),
    )
    .mockRejectedValueOnce(Error('Lost response'))
    .mockResolvedValueOnce(
      Response.json({ state: 'running', topic: 'Y', can_start: [] }),
    )
    .mockResolvedValueOnce(
      Response.json({
        state: 'completed',
        topic: 'Y',
        conversation_id: secondReport,
        can_start: [],
      }),
    );
  vi.stubGlobal('fetch', fetcher);

  render(<InboxPilot endpoint={endpoint} />);
  await screen.findByText('No research request yet.');
  send('Do a deep dive on X.');
  await screen.findByText('X: Accepted.');
  expect(fetcher.mock.calls[1][1].method).toBe('POST');
  expect(fetcher.mock.calls[1][1].body).toBe(
    JSON.stringify({ text: 'Do a deep dive on X.' }),
  );

  // The first job is still running and its verified can_start is empty.
  send('Do a deep dive on Y.');
  expect(fetcher).toHaveBeenCalledTimes(2);

  send('How’s that going?');
  await screen.findByRole('link', { name: 'Open report' });
  await screen.findByText('X: Your report is ready.');

  send('Do a deep dive on Y.');
  await screen.findByText('Status unavailable');
  expect(screen.queryByText('Y: Accepted.')).toBeNull();
  expect(screen.queryByRole('link', { name: 'Open report' })).toBeNull();
  expect(fetcher.mock.calls[3][1].method).toBe('POST');
  expect(fetcher.mock.calls[3][1].body).toBe(
    JSON.stringify({ text: 'Do a deep dive on Y.' }),
  );
  expect(fetcher.mock.calls[3][1].headers['Idempotency-Key']).not.toBe(
    fetcher.mock.calls[1][1].headers['Idempotency-Key'],
  );

  // The uncertain second acceptance retains its key and is never resent.
  send('Do a deep dive on Y.');
  expect(fetcher).toHaveBeenCalledTimes(4);

  send("How's Y going?");
  await screen.findByText('Y: Last saved as running.');
  expect(screen.queryByRole('link', { name: 'Open report' })).toBeNull();
  expect(fetcher.mock.calls[4][0]).toBe(`${endpoint}?topic=Y`);

  send('How’s Y going?');
  const report = await screen.findByRole('link', { name: 'Open report' });
  expect(report.getAttribute('href')).toBe(`/?id=${secondReport}`);
  expect(screen.getAllByRole('link', { name: 'Open report' })).toHaveLength(1);
  expect(fetcher.mock.calls.map(([, init]) => init.method)).toEqual([
    'GET',
    'POST',
    'GET',
    'POST',
    'GET',
    'GET',
  ]);
});

it('reads an explicit missing topic and starts it only from verified can_start', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({
        state: 'ambiguous',
        choices: ['X', 'Y'],
        can_start: [],
      }),
    )
    .mockResolvedValueOnce(
      Response.json({ state: 'missing', can_start: ['Y'] }),
    )
    .mockResolvedValueOnce(
      Response.json({ state: 'running', topic: 'Y', can_start: [] }),
    );
  vi.stubGlobal('fetch', fetcher);

  render(<InboxPilot endpoint={endpoint} />);
  await screen.findByText('Do you mean research X or Y?');
  send('Y');
  await screen.findByText('No Y research request yet.');
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[1][1].method).toBe('GET');
  expect(fetcher.mock.calls[1][0]).toBe(`${endpoint}?topic=Y`);
  expect(screen.queryByRole('link', { name: 'Open report' })).toBeNull();

  send('Do a deep dive on Y.');
  await screen.findByText('Y: Accepted.');
  expect(fetcher).toHaveBeenCalledTimes(3);
  expect(fetcher.mock.calls[2][1].method).toBe('POST');
  expect(fetcher.mock.calls[2][1].body).toBe(
    JSON.stringify({ text: 'Do a deep dive on Y.' }),
  );
});

it('fails closed on a malformed multi response', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(Response.json({ state: 'queued', can_start: [] }));
  vi.stubGlobal('fetch', fetcher);

  render(<InboxPilot endpoint={endpoint} />);
  await screen.findByText('Status unavailable');
  expect(screen.queryByRole('link', { name: 'Open report' })).toBeNull();
  expect(screen.queryByRole('textbox', { name: 'Message Daemon' })).toBeNull();
});

it('refuses a selection response for a different topic', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({
        state: 'ambiguous',
        choices: ['X', 'Y'],
        can_start: [],
      }),
    )
    .mockResolvedValueOnce(
      Response.json({
        state: 'completed',
        topic: 'Y',
        conversation_id: reportId,
        can_start: [],
      }),
    );
  vi.stubGlobal('fetch', fetcher);

  render(<InboxPilot endpoint={endpoint} />);
  await screen.findByText('Do you mean research X or Y?');
  send('X');
  await screen.findByText('Status unavailable');
  expect(screen.queryByRole('link', { name: 'Open report' })).toBeNull();
});

it('does not publish a late multi report under a different sign-in generation', async () => {
  let resolvePrior!: (value: Response) => void;
  const prior = new Promise<Response>((resolve) => {
    resolvePrior = resolve;
  });
  const ambiguous = Response.json({
    state: 'ambiguous',
    choices: ['X', 'Y'],
    can_start: [],
  });
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(ambiguous)
    .mockReturnValueOnce(prior)
    .mockResolvedValueOnce(
      Response.json({
        state: 'ambiguous',
        choices: ['X', 'Y'],
        can_start: [],
      }),
    );
  vi.stubGlobal('fetch', fetcher);

  const { rerender } = render(<InboxPilot endpoint={endpoint} />);
  await screen.findByText('Do you mean research X or Y?');
  send('X');
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  auth.generation += 1;
  rerender(<InboxPilot endpoint={endpoint} />);
  await screen.findByText('Do you mean research X or Y?');
  await act(async () => {
    resolvePrior(
      Response.json({
        state: 'completed',
        topic: 'X',
        conversation_id: reportId,
        can_start: [],
      }),
    );
  });
  expect(screen.queryByRole('link', { name: 'Open report' })).toBeNull();
  expect(screen.queryByText('X: Your report is ready.')).toBeNull();
  expect(fetcher.mock.calls[1][1].signal.aborted).toBe(true);
});

it('resolves a fresh bearer for each multi read and start and never replays a refused start', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({
        state: 'ambiguous',
        choices: ['X', 'Y'],
        can_start: [],
      }),
    )
    .mockResolvedValueOnce(
      Response.json({
        state: 'completed',
        topic: 'X',
        conversation_id: reportId,
        can_start: ['Y'],
      }),
    )
    .mockResolvedValueOnce(new Response(null, { status: 401 }))
    .mockResolvedValueOnce(
      Response.json({ state: 'running', topic: 'Y', can_start: [] }),
    );
  vi.stubGlobal('fetch', fetcher);

  render(<InboxPilot endpoint={endpoint} />);
  await screen.findByText('Do you mean research X or Y?');
  auth.renderHeader = null;

  auth.ensureHeader.mockResolvedValueOnce('Bearer rotated-select');
  send('X');
  await screen.findByText('X: Your report is ready.');

  auth.ensureHeader.mockResolvedValueOnce('Bearer rotated-start');
  send('Do a deep dive on Y.');
  await screen.findByText('Status unavailable');
  expect(screen.queryByText('Y: Accepted.')).toBeNull();

  // A refused start is not replayed and its retained key is not renewed.
  send('Do a deep dive on Y.');
  expect(fetcher).toHaveBeenCalledTimes(3);

  auth.ensureHeader.mockResolvedValueOnce('Bearer rotated-after-refusal');
  send("How's Y going?");
  await screen.findByText('Y: Last saved as running.');
  expect(
    fetcher.mock.calls.map(([, init]) => [
      init.method,
      init.headers.Authorization,
    ]),
  ).toEqual([
    ['GET', 'Bearer synthetic-1'],
    ['GET', 'Bearer rotated-select'],
    ['POST', 'Bearer rotated-start'],
    ['GET', 'Bearer rotated-after-refusal'],
  ]);
});
