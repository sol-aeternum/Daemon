import type { ReactNode } from 'react';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { deferred, json } from './main-c14-fixtures';

const boundary = vi.hoisted(() => ({
  generate: vi.fn(),
  search: new URLSearchParams(),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => '/studio',
  useSearchParams: () => boundary.search,
}));
vi.mock('../lib/auth', () => ({
  ensureAuthHeader: async () => 'Bearer synthetic-c14',
}));
vi.mock('../hooks/useEntitlements', () => ({
  useEntitlements: () => ({ plan: 'pro', can: () => true }),
}));
vi.mock('../components/ConversationHistoryProvider', () => ({
  ConversationHistoryProvider: ({ children }: { children: ReactNode }) =>
    children,
}));
vi.mock('../components/SidebarShell', () => ({
  SidebarShell: ({ children }: { children: ReactNode }) => children,
}));
vi.mock('../components/CreditBalance', () => ({ CreditBalance: () => null }));
vi.mock('../app/studio/StudioProvider', () => ({
  StudioProvider: ({ children }: { children: ReactNode }) => children,
  useStudio: () => ({
    referenceImage: null,
    setPrompt: vi.fn(),
    setReferenceImage: vi.fn(),
  }),
}));
vi.mock('../app/studio/components/ImageGallery', () => ({
  ImageGallery: () => null,
}));
vi.mock('../app/studio/components/ReferenceUpload', () => ({
  ReferenceUpload: () => null,
}));
vi.mock('../app/studio/hooks/useVideoGeneration', () => ({
  useVideoGeneration: () => ({ generateVideo: boundary.generate }),
}));
vi.mock('../app/studio/components/PromptInput', () => ({
  PromptInput: ({
    videoButtonLabel,
    videoGenerateDisabled,
  }: {
    videoButtonLabel: string;
    videoGenerateDisabled: boolean;
  }) => (
    <button data-testid="generate" disabled={videoGenerateDisabled}>
      {videoButtonLabel}
    </button>
  ),
}));
import StudioPage from '../app/studio/page';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it('keeps initial/reload estimation loading and server-derived query semantics', async () => {
  vi.stubEnv('NEXT_PUBLIC_API_URL', '');
  const first = deferred<unknown>();
  let initial = true;
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(async (input) => {
    const url = new URL(String(input), 'https://studio.c14.invalid');
    if (initial) return json(await first.promise);
    return json({
      credits_required: Number(url.searchParams.get('duration')),
      current_balance: 100,
      sufficient: true,
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<StudioPage />);
  expect(fetchMock).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: /^Video$/ }));
  // The existing page renders both mobile and desktop controls. Do not invent
  // a cross-instance deduplication contract in this lint repair.
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(screen.getAllByText('Fetching estimate...')).toHaveLength(2);
  expect(
    screen
      .getAllByTestId('generate')
      .every((button) => (button as HTMLButtonElement).disabled),
  ).toBe(true);
  initial = false;
  await act(async () =>
    first.resolve({
      credits_required: 5,
      current_balance: 100,
      sufficient: true,
    }),
  );
  expect(screen.getAllByText('Estimated cost: 5 credits')).toHaveLength(2);
  fireEvent.change(screen.getAllByRole('slider')[0], {
    target: { value: '10' },
  });
  await screen.findByText('Estimated cost: 10 credits');
  const next = deferred<unknown>();
  fetchMock.mockImplementationOnce(async () => json(await next.promise));
  fireEvent.click(screen.getAllByRole('button', { name: /^Refresh$/ })[0]);
  expect(screen.getAllByText('Fetching estimate...')).toHaveLength(1);
  await act(async () =>
    next.resolve({
      credits_required: 12,
      current_balance: 100,
      sufficient: true,
    }),
  );
  await screen.findByText('Estimated cost: 12 credits');
  const query = new URL(
    String(fetchMock.mock.calls[2][0]),
    'https://studio.c14.invalid',
  ).searchParams;
  expect(query.get('duration')).toBe('10');
  expect(query.get('provider')).toBe('xai');
  expect(query.has('plan')).toBe(false);
  expect(fetchMock.mock.calls[0][1]).toMatchObject({
    cache: 'no-store',
    headers: { Authorization: 'Bearer synthetic-c14' },
  });
  expect(boundary.generate).not.toHaveBeenCalled();
});

it('keeps a failed estimate visible and resets loading/error for an explicit retry', async () => {
  vi.stubEnv('NEXT_PUBLIC_API_URL', '');
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockImplementation(async () => json({}, 503));
  vi.stubGlobal('fetch', fetchMock);
  render(<StudioPage />);
  fireEvent.click(screen.getByRole('button', { name: /^Video$/ }));
  await waitFor(() =>
    expect(screen.getAllByText('Estimate failed (503)')).toHaveLength(2),
  );
  const retry = deferred<unknown>();
  fetchMock.mockImplementationOnce(async () => json(await retry.promise));
  fireEvent.click(screen.getAllByRole('button', { name: /^Refresh$/ })[0]);
  expect(screen.getAllByText('Estimate failed (503)')).toHaveLength(1);
  expect(screen.getAllByText('Fetching estimate...')).toHaveLength(1);
  await act(async () =>
    retry.resolve({
      credits_required: 6,
      current_balance: 20,
      sufficient: true,
    }),
  );
  await screen.findByText('Estimated cost: 6 credits');
});
