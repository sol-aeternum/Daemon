/**
 * Tests for the hosted /auth route composition.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';

type TestAuthConfig = {
  mode: 'hosted' | 'self_hosted';
  email: { enabled: boolean };
  google: { enabled: boolean; clientId: string };
};

type TestAuthConfigResult =
  | { status: 'resolved'; config: TestAuthConfig }
  | { status: 'error' };

const runtimeConfig: TestAuthConfig = {
  mode: 'hosted',
  email: { enabled: false },
  google: { enabled: true, clientId: 'runtime-google-client' },
};

const selfHostedRuntimeConfig: TestAuthConfig = {
  mode: 'self_hosted',
  email: { enabled: false },
  google: { enabled: false, clientId: '' },
};

const mockRouterReplace = vi.hoisted(() => vi.fn());
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: mockRouterReplace }),
}));

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

const mockFetchAuthConfig = vi.fn<() => Promise<TestAuthConfigResult>>(() =>
  Promise.resolve({ status: 'resolved', config: runtimeConfig }),
);
const mockGetCachedAuthConfig = vi.fn<() => TestAuthConfig | undefined>(
  () => undefined,
);
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
import SetupPage from '../app/setup/page';

describe('AuthPage — /auth route composition', () => {
  beforeEach(() => {
    mockAuthLanding.mockClear();
    mockRouterReplace.mockClear();
    mockFetchAuthConfig.mockClear();
    mockGetCachedAuthConfig.mockClear();
    mockSubscribeAuthConfig.mockClear();
    mockGetDeploymentMode.mockClear();
    mockGetDeploymentMode.mockReturnValue('self-hosted');
    mockGetCachedAuthConfig.mockReturnValue(undefined);
    mockFetchAuthConfig.mockResolvedValue({
      status: 'resolved',
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

  it('fetches hosted runtime config and passes provider flags to AuthLanding', async () => {
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
    expect(mockRouterReplace).not.toHaveBeenCalled();
  });

  it('redirects self-hosted runtime mode from /auth to /setup', async () => {
    mockGetCachedAuthConfig.mockReturnValue(selfHostedRuntimeConfig);

    render(<AuthPage />);

    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith('/setup');
    });
    expect(mockAuthLanding).not.toHaveBeenCalled();
    expect(mockFetchAuthConfig).not.toHaveBeenCalled();
  });

  it('falls back to /setup when runtime mode cannot be resolved', async () => {
    mockFetchAuthConfig.mockResolvedValue({ status: 'error' });

    render(<AuthPage />);

    await waitFor(() => {
      expect(mockRouterReplace).toHaveBeenCalledWith('/setup');
    });
  });
});

describe('SetupPage — /setup route composition', () => {
  beforeEach(() => {
    mockAuthLanding.mockClear();
    mockGetDeploymentMode.mockClear();
  });

  it('always renders the self-hosted setup flow without build-time mode checks', () => {
    mockGetDeploymentMode.mockReturnValue('hosted');

    render(<SetupPage />);

    expect(mockAuthLanding).toHaveBeenCalledWith(
      expect.objectContaining({ mode: 'self-hosted' }),
    );
    expect(mockGetDeploymentMode).not.toHaveBeenCalled();
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
