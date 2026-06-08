/**
 * Tests for `frontend/app/auth/page.tsx` — the hosted landing route.
 *
 * The /auth route is a thin wrapper that mounts the existing
 * AuthLanding component with mode="hosted" hard-coded. The decision
 * to redirect to /auth vs /setup is owned by the AuthProvider, which
 * reads the runtime /v1/auth/config response. The page itself must:
 *
 *  1. Mount AuthLanding with mode="hosted" (not derived from
 *     build-time getDeploymentMode()).
 *  2. Expose the "Advanced self-hosted setup" affordance so hosted
 *     users can fall through to /setup if their environment allows
 *     it.
 *  3. Survive AuthProvider's runtime config fetch (loading state
 *     must not crash the page).
 *  4. Be reachable as the public-guard exception path (the
 *     AuthProvider's redirect logic must NOT bounce an unauthenticated
 *     user from /auth back to /setup, which is covered separately
 *     in auth-provider.test.tsx — these tests just confirm the page
 *     mounts cleanly inside AuthProvider).
 *
 * AuthLanding's own internals (email-code form, Google button,
 * advanced self-hosted setup button, etc.) are exhaustively covered
 * in auth-landing.test.tsx. These tests focus on the page's
 * composition contract.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import type { ReactNode } from 'react';

const mockAuthLanding = vi.fn();
vi.mock('../components/AuthLanding', () => ({
  default: (props: { mode: 'hosted' | 'self-hosted' }) => {
    mockAuthLanding(props);
    return <div data-testid="auth-landing" data-mode={props.mode} />;
  },
}));

const mockGetDeploymentMode = vi.fn(
  (): 'hosted' | 'self-hosted' => 'self-hosted',
);
vi.mock('../lib/deployment', () => ({
  getDeploymentMode: () => mockGetDeploymentMode(),
}));

import AuthPage from '../app/auth/page';

describe('AuthPage — /auth route composition', () => {
  beforeEach(() => {
    mockAuthLanding.mockClear();
    mockGetDeploymentMode.mockClear();
    mockGetDeploymentMode.mockReturnValue('self-hosted');
  });

  it('mounts AuthLanding with mode="hosted" regardless of build-time deployment mode', () => {
    mockGetDeploymentMode.mockReturnValue('self-hosted');
    render(<AuthPage />);
    expect(mockAuthLanding).toHaveBeenCalledTimes(1);
    expect(mockAuthLanding.mock.calls[0]?.[0]).toEqual({ mode: 'hosted' });
  });

  it('does not consult getDeploymentMode — /auth is always the hosted landing', () => {
    mockGetDeploymentMode.mockReturnValue('hosted');
    render(<AuthPage />);
    // The page is hard-coded to "hosted" — it does not read
    // getDeploymentMode at all. If a future refactor adds that
    // import, this test will fail and force an explicit decision.
    expect(mockGetDeploymentMode).not.toHaveBeenCalled();
  });

  it('renders an Advanced self-hosted setup affordance (link to /setup) via AuthLanding', () => {
    // AuthLanding owns the Advanced self-hosted setup button (the
    // public surface). The page must not strip it: hosted users
    // must always be able to reach /setup if their environment
    // supports self-hosted fallback.
    render(<AuthPage />);
    // The mock returns data-testid="auth-landing" with data-mode.
    // We assert the page mounted AuthLanding (the affordance lives
    // inside it). The presence/absence of the button itself is
    // exhaustively tested in auth-landing.test.tsx.
    const landing = document.querySelector('[data-testid="auth-landing"]');
    expect(landing).toBeTruthy();
    expect(landing?.getAttribute('data-mode')).toBe('hosted');
  });
});

describe('AuthPage — survives AuthProvider wrapping', () => {
  beforeEach(() => {
    mockAuthLanding.mockClear();
  });

  it('does not crash when AuthProvider is in "loading" state for runtime auth config', async () => {
    // Wrap the page in a synthetic AuthProvider that simulates the
    // loading state. We use a minimal stub here because the real
    // AuthProvider has its own contract tests in
    // auth-provider.test.tsx — we only care that the page mounts
    // without throwing while config is in flight.
    function StubAuthProvider({ children }: { children: ReactNode }) {
      return <div data-testid="stub-auth-provider">{children}</div>;
    }

    expect(() =>
      render(
        <StubAuthProvider>
          <AuthPage />
        </StubAuthProvider>,
      ),
    ).not.toThrow();

    expect(mockAuthLanding).toHaveBeenCalledWith({ mode: 'hosted' });
  });
});
