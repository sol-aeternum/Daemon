/**
 * Tests for the hosted /auth route composition.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';

const runtimeConfig = {
  mode: 'hosted' as const,
  email: { enabled: false },
  google: { enabled: true, clientId: 'runtime-google-client' },
};

const mockAuthLanding = vi.fn();
vi.mock('../components/AuthLanding', () => ({
  default: (props: {
    mode: 'hosted' | 'self-hosted';
    runtimeConfig?: unknown;
    runtimeConfigLoading?: boolean;
  }) => {
    mockAuthLanding(props);
    return <div data-testid="auth-landing" data-mode={props.mode} />;
  },
}));

const mockFetchAuthConfig = vi.fn(() =>
  Promise.resolve({ status: 'resolved' as const, config: runtimeConfig }),
);
const mockGetCachedAuthConfig = vi.fn(() => undefined);
const mockSubscribeAuthConfig = vi.fn((_cb: unknown) => () => {});
vi.mock('../lib/auth-config', () => ({
  fetchAuthConfig: () => mockFetchAuthConfig(),
  getCachedAuthConfig: () => mockGetCachedAuthConfig(),
  subscribeAuthConfig: (cb: unknown) => mockSubscribeAuthConfig(cb),
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
    mockFetchAuthConfig.mockClear();
    mockGetCachedAuthConfig.mockClear();
    mockSubscribeAuthConfig.mockClear();
    mockGetDeploymentMode.mockClear();
    mockGetDeploymentMode.mockReturnValue('self-hosted');
    mockGetCachedAuthConfig.mockReturnValue(undefined);
    mockFetchAuthConfig.mockResolvedValue({
      status: 'resolved' as const,
      config: runtimeConfig,
    });
  });

  it('mounts AuthLanding with mode="hosted" regardless of build-time deployment mode', async () => {
    mockGetDeploymentMode.mockReturnValue('self-hosted');
    render(<AuthPage />);

    await waitFor(() => {
      expect(mockAuthLanding).toHaveBeenCalledWith(
        expect.objectContaining({ mode: 'hosted' }),
      );
    });
  });

  it('does not consult getDeploymentMode — /auth is always the hosted landing', async () => {
    mockGetDeploymentMode.mockReturnValue('hosted');
    render(<AuthPage />);

    await waitFor(() => {
      expect(mockAuthLanding).toHaveBeenCalled();
    });
    expect(mockGetDeploymentMode).not.toHaveBeenCalled();
  });

  it('fetches runtime auth config and passes provider flags to AuthLanding', async () => {
    render(<AuthPage />);

    await waitFor(() => {
      expect(mockAuthLanding).toHaveBeenCalledWith(
        expect.objectContaining({
          mode: 'hosted',
          runtimeConfig,
          runtimeConfigLoading: false,
        }),
      );
    });
    expect(mockFetchAuthConfig).toHaveBeenCalledTimes(1);
  });
});

describe('AuthPage — survives AuthProvider wrapping', () => {
  beforeEach(() => {
    mockAuthLanding.mockClear();
  });

  it('does not crash when AuthProvider is in "loading" state for runtime auth config', async () => {
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

    await waitFor(() => {
      expect(mockAuthLanding).toHaveBeenCalledWith(
        expect.objectContaining({ mode: 'hosted' }),
      );
    });
  });
});
