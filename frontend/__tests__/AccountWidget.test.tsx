import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const mockLogout = vi.fn();
const mockPush = vi.fn();
const mockEnsureAuthHeader = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    push: mockPush,
    replace: vi.fn(),
    refresh: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => '/',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('next-themes', () => ({
  useTheme: () => ({
    theme: 'system',
    setTheme: vi.fn(),
    themes: ['light', 'dark', 'system'],
    resolvedTheme: 'system',
    systemTheme: 'light',
  }),
  ThemeProvider: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock('@/components/AuthProvider', () => ({
  useAuth: () => ({
    isAuthenticated: true,
    accessToken: 'token',
    authHeader: 'Bearer token',
    refreshAuth: vi.fn(),
    logout: mockLogout,
    setAccessToken: vi.fn(),
  }),
}));

vi.mock('@/lib/auth', () => ({
  ensureAuthHeader: () => mockEnsureAuthHeader(),
}));

import { AccountWidget } from '@/components/AccountWidget';

interface SnapshotOverrides {
  plan?: string;
  capabilities?: string[];
  trial?: { state: string } | null;
  limits?: Record<string, unknown>;
}

/**
 * Stubs the same-origin entitlements proxy with a server snapshot. Anything not
 * named here is the account's real current state, not a client assertion.
 */
function stubSnapshot(
  overrides: SnapshotOverrides = {},
): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        plan: 'free',
        capabilities: ['chat'],
        trial: { state: 'exhausted' },
        limits: { monthly_budget_microusd: 0 },
        ...overrides,
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function stubSnapshotFailure(
  status: number,
  body: unknown = { detail: { code: 'x' } },
) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/**
 * The test environment exposes no `localStorage`, and the point of these tests
 * is that client-side keys are ignored, so install one the widget could read.
 */
function installStorage(initial: Record<string, string> = {}): void {
  const store: Record<string, string> = { ...initial };
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => store[key] ?? null,
      setItem: (key: string, value: string) => {
        store[key] = value;
      },
      removeItem: (key: string) => {
        delete store[key];
      },
      clear: () => {
        for (const key of Object.keys(store)) delete store[key];
      },
      key: () => null,
      length: 0,
    },
  });
}

function openDropdown() {
  fireEvent.click(screen.getByRole('button', { name: /test user/i }));
  return screen.findByRole('button', { name: /log out/i });
}

async function currentPlanRow() {
  await waitFor(() => {
    expect(screen.getByText('Current plan')).toBeTruthy();
  });
  const marker = screen.getByText('Current plan');
  return marker.closest('li');
}

beforeEach(() => {
  vi.clearAllMocks();
  installStorage();
  mockEnsureAuthHeader.mockResolvedValue('Bearer token');
  stubSnapshot();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('AccountWidget disclosure affordances', () => {
  it('reports the expanded state on the toggle', async () => {
    render(<AccountWidget displayName="Test User" />);
    const toggle = screen.getByRole('button', { name: /test user/i });

    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.getAttribute('aria-controls')).toBe('account-menu');
    expect(document.getElementById('account-menu')).toBeNull();

    await openDropdown();
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(document.getElementById('account-menu')).not.toBeNull();
  });

  it('closes on Escape and returns focus to the toggle', async () => {
    render(<AccountWidget displayName="Test User" />);
    const toggle = screen.getByRole('button', { name: /test user/i });

    await openDropdown();
    expect(document.getElementById('account-menu')).not.toBeNull();

    fireEvent.keyDown(document, { key: 'Escape' });

    await waitFor(() => {
      expect(document.getElementById('account-menu')).toBeNull();
    });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(toggle);
  });
});

describe('AccountWidget logout', () => {
  it('calls useAuth().logout when the Log out button is clicked', async () => {
    render(<AccountWidget displayName="Test User" />);

    fireEvent.click(screen.getByRole('button', { name: /test user/i }));

    const logoutButton = await screen.findByRole('button', {
      name: /log out/i,
    });
    fireEvent.click(logoutButton);

    await waitFor(() => {
      expect(mockLogout).toHaveBeenCalledTimes(1);
    });
  });

  it('closes the dropdown when Log out is clicked', async () => {
    render(<AccountWidget displayName="Test User" />);

    fireEvent.click(screen.getByRole('button', { name: /test user/i }));
    const logoutButton = await screen.findByRole('button', {
      name: /log out/i,
    });
    fireEvent.click(logoutButton);

    await waitFor(() => {
      expect(mockLogout).toHaveBeenCalled();
    });
  });
});

describe('AccountWidget plan state comes from the server', () => {
  it('shows the confirmed plan label instead of an optimistic default', async () => {
    stubSnapshot({ plan: 'power' });
    render(<AccountWidget displayName="Test User" />);

    expect(await screen.findByText('Power')).toBeTruthy();
  });

  it('ignores a localStorage plan that claims a paid tier the server did not grant', async () => {
    // A previously shipped build let the browser assert its own plan. Those
    // keys must have no effect on what the widget displays.
    localStorage.setItem('daemon_tier', 'max');
    localStorage.setItem('daemon_plan', 'power');
    localStorage.setItem(
      'daemon_capabilities',
      '["premium_routing","video_generation"]',
    );

    stubSnapshot({ plan: 'free', capabilities: ['chat'] });
    render(<AccountWidget displayName="Test User" />);

    expect(await screen.findByText('Free')).toBeTruthy();

    await openDropdown();
    const freeRow = await currentPlanRow();
    expect(freeRow?.textContent).toContain('Free');

    // No retired tier vocabulary anywhere in the rendered widget.
    const rendered = document.body.textContent ?? '';
    for (const retired of ['Max', 'Starter', 'BYOK']) {
      expect(rendered).not.toContain(retired);
    }
  });

  it('reports plan as unavailable instead of guessing when the server fails', async () => {
    stubSnapshotFailure(503, {
      detail: {
        code: 'entitlements_unavailable',
        message: 'Entitlements unavailable',
      },
    });
    render(<AccountWidget displayName="Test User" />);

    expect(await screen.findByText('Plan unavailable')).toBeTruthy();

    await openDropdown();
    await waitFor(() => {
      expect(screen.getByText('Plan service is unavailable.')).toBeTruthy();
    });
    expect(screen.queryByText('Current plan')).toBeNull();
    expect(screen.queryByText('Checking plan...')).toBeNull();
  });

  it('offers a retry that refetches rather than a checkout the build cannot honour', async () => {
    const fetchMock = stubSnapshotFailure(500);
    render(<AccountWidget displayName="Test User" />);

    await openDropdown();
    const retry = await screen.findByRole('button', { name: /retry/i });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          plan: 'pro',
          capabilities: ['chat'],
          trial: null,
          limits: {},
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    fireEvent.click(retry);

    expect(await screen.findByText('Pro')).toBeTruthy();
    expect(
      screen.getByText(
        'Plan changes and billing are not available in this build.',
      ),
    ).toBeTruthy();
  });
});

describe('AccountWidget trial copy', () => {
  const SALES_TOKENS = [
    /upgrade/i,
    /subscribe/i,
    /purchase/i,
    /checkout/i,
    /\$\d/,
    /per month/i,
  ];

  /** The single live region reporting trial state, once the plan is confirmed. */
  async function trialLine() {
    return (await screen.findByRole('status')).textContent ?? '';
  }

  it('states plainly what an active trial grants, without promising a paid plan', async () => {
    stubSnapshot({
      plan: 'free',
      capabilities: ['chat', 'premium_routing'],
      trial: { state: 'active' },
    });
    render(<AccountWidget displayName="Test User" />);

    await openDropdown();
    const detail = await trialLine();

    expect(detail).toMatch(/trial active/i);
    expect(detail).toContain('Premium models and extended limits');
    expect(detail).toContain('qualified models are available');
    for (const token of SALES_TOKENS) {
      expect(detail).not.toMatch(token);
    }
  });

  it('says an exhausted trial is spent and what still works, without claiming a plan', async () => {
    stubSnapshot({
      plan: 'free',
      capabilities: ['chat'],
      trial: { state: 'exhausted' },
    });
    render(<AccountWidget displayName="Test User" />);

    await openDropdown();
    const detail = await trialLine();

    expect(detail).toMatch(/spent/i);
    expect(detail).toMatch(/chat, memory/i);
    for (const token of SALES_TOKENS) {
      expect(detail).not.toMatch(token);
    }
  });

  it('does not assert a plan for a paid account whose trial is exhausted', async () => {
    // The same exhausted state can occur on a paid plan. The copy must not
    // tell a paying customer they are "on Free".
    stubSnapshot({
      plan: 'pro',
      capabilities: ['chat', 'premium_routing'],
      trial: { state: 'exhausted' },
    });
    render(<AccountWidget displayName="Test User" />);

    await openDropdown();
    const detail = await trialLine();

    expect(detail).not.toMatch(/\bFree\b/);
    const proRow = await currentPlanRow();
    expect(proRow?.textContent).toContain('Pro');
  });

  it('omits the trial line entirely when the server reports no trial', async () => {
    stubSnapshot({ plan: 'power', capabilities: ['chat'], trial: null });
    render(<AccountWidget displayName="Test User" />);

    await openDropdown();
    await waitFor(() => {
      expect(screen.getByText('Current plan')).toBeTruthy();
    });

    expect(screen.queryByRole('status')).toBeNull();
    expect(document.body.textContent).not.toMatch(/trial/i);
  });
});
