import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { InboxPilot } from '@/app/inbox/InboxPilot';

const auth = vi.hoisted(() => ({ generation: 1 }));
vi.mock('@/hooks/useAuthGeneration', () => ({
  useAuthGeneration: () => auth.generation,
}));
vi.mock('@/lib/auth', () => ({
  getAuthGeneration: () => auth.generation,
}));
vi.mock('@/components/AuthProvider', () => ({
  useAuth: () => ({
    isAuthenticated: true,
    authHeader: `Bearer synthetic-${auth.generation}`,
  }),
}));

const endpoint = 'http://127.0.0.1:9999/tasks/inbox-pilot';
const reportId = 'b6a96dbf-f749-49ab-bb67-27991aab8688';

beforeEach(() => {
  auth.generation = 1;
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
