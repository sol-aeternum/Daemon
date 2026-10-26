import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deferred, json } from './main-c14-fixtures';

vi.mock('../lib/auth', () => ({ ensureAuthHeader: vi.fn() }));
import { ensureAuthHeader } from '../lib/auth';
import { CreditBalance } from '../components/CreditBalance';
import SkillsTab from '../components/settings/SkillsTab';

const auth = vi.mocked(ensureAuthHeader);
const fetchMock = vi.fn<typeof fetch>();
const skill = (id: string) => ({
  id,
  name: `Skill ${id}`,
  description: `Description ${id}`,
  content: `Content ${id}`,
  enabled: true,
  updated_at: '2026-01-01',
});

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubEnv('NEXT_PUBLIC_API_URL', '');
  auth.mockResolvedValue('Bearer synthetic-c14');
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

// Selected unchanged C14 regression bodies; no old memory/auth contracts imported.
describe('main integration: credit and skill request completion', () => {
  it('preserves credit fallback order, explicit refresh and error/loading semantics', async () => {
    const first = deferred<Response>();
    fetchMock
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce(json({ balance: 7 }));
    render(
      <CreditBalance
        mode="expanded"
        userId="synthetic-user"
        refreshInterval={0}
      />,
    );
    expect(screen.getByText('--')).toBeTruthy();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await act(async () => first.resolve(json({}, 404)));
    await screen.findByText('7');
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/video-credits/balance?user_id=synthetic-user',
      '/video-credits/balance?user_id=synthetic-user',
    ]);
    expect(auth).toHaveBeenCalledTimes(2);
    fetchMock
      .mockResolvedValueOnce(json({}, 503))
      .mockResolvedValueOnce(json({}, 503));
    fireEvent.click(screen.getByRole('button', { name: /^Refresh$/ }));
    await screen.findByText('Balance request failed (503)');
    const refresh = deferred<Response>();
    fetchMock.mockReturnValueOnce(refresh.promise);
    fireEvent.click(screen.getByRole('button', { name: /^Refresh$/ }));
    expect(screen.queryByText('Balance request failed (503)')).toBeNull();
    expect(
      (screen.getByRole('button', { name: /^Refresh$/ }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    await act(async () => refresh.resolve(json({ balance: 9 })));
    expect(screen.getByText('9')).toBeTruthy();
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      cache: 'no-store',
      headers: { Authorization: 'Bearer synthetic-c14' },
    });
  });

  it('preserves credit polling/events and removes subscriptions on unmount', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetchMock.mockImplementation(async () => json({ balance: 5 }));
    const view = render(
      <CreditBalance
        mode="compact"
        userId="synthetic-user"
        refreshInterval={1000}
      />,
    );
    await screen.findByText('5 credits');
    const before = fetchMock.mock.calls.length;
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    await act(async () => {
      window.dispatchEvent(new Event('video-credits:refresh'));
    });
    expect(fetchMock).toHaveBeenCalledTimes(before + 2);
    view.unmount();
    await act(async () => {
      vi.advanceTimersByTime(3000);
      window.dispatchEvent(new Event('video-credits:refresh'));
    });
    expect(fetchMock).toHaveBeenCalledTimes(before + 2);
  });

  it('keeps missing-user credit state local and does not perform an anonymous request', async () => {
    const view = render(
      <CreditBalance mode="expanded" userId="" refreshInterval={0} />,
    );
    expect(screen.getByText('Missing user ID')).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock.mockResolvedValueOnce(json({ balance: 3 }));
    view.rerender(
      <CreditBalance
        mode="expanded"
        userId="synthetic-B"
        refreshInterval={0}
      />,
    );
    await screen.findByText('3');
    expect(screen.queryByText('Missing user ID')).toBeNull();
  });

  it('loads skills, preserves drafts across ordinary rerenders and loads the selected detail', async () => {
    const first = deferred<Response>();
    const detailB = deferred<Response>();
    let lists = 0;
    fetchMock.mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/skills') {
        if (++lists === 1) return first.promise;
        return json({ skills: [skill('A'), skill('B')] });
      }
      return url.endsWith('/B') ? detailB.promise : json(skill('A'));
    });
    const view = render(<SkillsTab />);
    expect(screen.getByText('Loading skills...')).toBeTruthy();
    await act(async () =>
      first.resolve(json({ skills: [skill('A'), skill('B')] })),
    );
    await screen.findByDisplayValue('Content A');
    fireEvent.change(screen.getByDisplayValue('Content A'), {
      target: { value: 'Local draft' },
    });
    view.rerender(<SkillsTab />);
    expect(screen.getByDisplayValue('Local draft')).toBeTruthy();
    fireEvent.click(
      screen.getByRole('button', { name: /Skill B Description B/ }),
    );
    expect(screen.getByText('Loading skill details...')).toBeTruthy();
    await act(async () => detailB.resolve(json(skill('B'))));
    expect(screen.getByDisplayValue('Content B')).toBeTruthy();
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/B')),
    ).toHaveLength(1);
  });

  it('clears the no-selection draft after deleting the only skill and can select a newly created one', async () => {
    let items = [skill('A')];
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (init?.method === 'DELETE') {
        items = [];
        return json({});
      }
      if (init?.method === 'POST') {
        items = [skill('C')];
        return json(skill('C'));
      }
      return url === '/api/skills' ? json({ skills: items }) : json(items[0]);
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<SkillsTab />);
    await screen.findByDisplayValue('Content A');
    fireEvent.click(screen.getByRole('button', { name: /Delete/ }));
    await screen.findByText('Select a skill to edit.');
    expect(screen.queryByDisplayValue('Content A')).toBeNull();
    fireEvent.click(screen.getByTitle('Create skill'));
    await screen.findByDisplayValue('Content C');
  });
});
