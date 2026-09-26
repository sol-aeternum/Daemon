import { describe, expect, it, vi, beforeEach } from 'vitest';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';

const mockPush = vi.fn();
const mockRouter = { push: mockPush };
let mockSearchParams = new URLSearchParams();

vi.mock('next/navigation', () => ({
  useRouter: vi.fn(() => mockRouter),
  useSearchParams: vi.fn(() => mockSearchParams),
}));

vi.mock('../lib/auth', () => ({
  refreshAccessToken: vi.fn(() => Promise.resolve({ success: false })),
  completeSetup: vi.fn(() => Promise.resolve({ success: true })),
  completeEnrollment: vi.fn(() => Promise.resolve({ success: true })),
  startEmailSignIn: vi.fn(() =>
    Promise.resolve({
      success: true,
      challengeId: 'ch-123',
      expiresAt: 1234567890,
    }),
  ),
  completeEmailSignIn: vi.fn(() => Promise.resolve({ success: true })),
  startGoogleSignIn: vi.fn(() =>
    Promise.resolve({
      success: true,
      challengeId: 'google-challenge',
      nonce: 'server-nonce',
      expiresAt: 1234567890,
    }),
  ),
  completeGoogleSignIn: vi.fn(() => Promise.resolve({ success: true })),
}));

import {
  completeEmailSignIn,
  completeEnrollment,
  completeGoogleSignIn,
  completeSetup,
  refreshAccessToken,
  startEmailSignIn,
  startGoogleSignIn,
} from '../lib/auth';
import AuthLanding from '../components/AuthLanding';

const mockedCompleteEmail = vi.mocked(completeEmailSignIn);
const mockedCompleteEnrollment = vi.mocked(completeEnrollment);
const mockedCompleteGoogle = vi.mocked(completeGoogleSignIn);
const mockedCompleteSetup = vi.mocked(completeSetup);
const mockedStartEmail = vi.mocked(startEmailSignIn);
const mockedStartGoogle = vi.mocked(startGoogleSignIn);
interface TestGoogleCredentialResponse {
  credential?: string;
}

interface TestGoogleInitializeConfig {
  client_id: string;
  nonce: string;
  callback: (response: TestGoogleCredentialResponse) => void;
  auto_select?: boolean;
  cancel_on_tap_outside?: boolean;
}

let capturedGoogleCallback:
  | ((response: TestGoogleCredentialResponse) => void)
  | null = null;
const mockGoogleInitialize = vi.fn((config: TestGoogleInitializeConfig) => {
  capturedGoogleCallback = config.callback;
});
const mockGoogleRenderButton = vi.fn((parent: HTMLElement) => {
  const frame = document.createElement('iframe');
  frame.dataset.testid = 'fake-google-iframe';
  parent.appendChild(frame);
});
const mockGoogleCancel = vi.fn();
const mockGooglePrompt = vi.fn();

function installGoogleMock(): void {
  capturedGoogleCallback = null;
  Object.defineProperty(window, 'google', {
    configurable: true,
    value: {
      accounts: {
        id: {
          initialize: mockGoogleInitialize,
          renderButton: mockGoogleRenderButton,
          cancel: mockGoogleCancel,
          // This deliberately exists only to prove the UI does not depend on
          // One Tap. The component's type intentionally has no prompt member.
          prompt: mockGooglePrompt,
        },
      },
    },
  });
}

function hostedConfig(
  overrides: Partial<{
    email: { enabled: boolean };
    google: { enabled: boolean; clientId: string };
  }> = {},
) {
  return {
    email: overrides.email ?? { enabled: false },
    google: overrides.google ?? {
      enabled: true,
      clientId: 'runtime-client-id',
    },
  };
}

beforeEach(() => {
  mockSearchParams = new URLSearchParams();
  delete process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;
  delete process.env.NEXT_PUBLIC_EMAIL_ENABLED;
  delete (window as Window & { google?: unknown }).google;
  capturedGoogleCallback = null;
  vi.clearAllMocks();
  vi.mocked(refreshAccessToken).mockResolvedValue({ success: false });
  vi.mocked(startGoogleSignIn).mockResolvedValue({
    success: true,
    challengeId: 'google-challenge',
    nonce: 'server-nonce',
    expiresAt: 1234567890,
  });
  vi.mocked(completeGoogleSignIn).mockResolvedValue({ success: true });
  vi.mocked(completeSetup).mockResolvedValue({ success: true });
  vi.mocked(completeEnrollment).mockResolvedValue({ success: true });
  vi.mocked(startEmailSignIn).mockResolvedValue({
    success: true,
    challengeId: 'ch-123',
    expiresAt: 1234567890,
  });
  vi.mocked(completeEmailSignIn).mockResolvedValue({ success: true });
  mockGoogleInitialize.mockClear();
  mockGoogleRenderButton.mockClear();
  mockGoogleCancel.mockClear();
  mockGooglePrompt.mockClear();
  mockPush.mockClear();
});

async function waitForLoadingToFinish(): Promise<void> {
  await waitFor(() => {
    expect(screen.queryByText(/Checking session/i)).toBeNull();
  });
}

async function waitForGoogleButton(): Promise<HTMLElement> {
  await waitFor(() => {
    expect(screen.getByTestId('google-signin-button')).toBeTruthy();
    expect(mockGoogleRenderButton).toHaveBeenCalled();
  });
  return screen.getByTestId('google-signin-button');
}

describe('AuthLanding — hosted Google-first login', () => {
  it('renders the official GIS button with a server nonce and defaults to a temporary session', async () => {
    installGoogleMock();
    mockSearchParams = new URLSearchParams('invite=invite-secret');

    render(<AuthLanding mode="hosted" runtimeConfig={hostedConfig()} />);
    await waitForLoadingToFinish();
    await waitForGoogleButton();

    expect(mockedStartGoogle).toHaveBeenCalledTimes(1);
    expect(mockGoogleInitialize).toHaveBeenCalledWith({
      client_id: 'runtime-client-id',
      nonce: 'server-nonce',
      callback: expect.any(Function),
      auto_select: false,
      cancel_on_tap_outside: false,
    });
    expect(mockGoogleRenderButton).toHaveBeenCalledWith(
      expect.any(HTMLElement),
      {
        type: 'standard',
        theme: 'outline',
        size: 'large',
        text: 'continue_with',
        shape: 'pill',
        width: 400,
        logo_alignment: 'left',
      },
    );
    expect(mockGooglePrompt).not.toHaveBeenCalled();
    expect(screen.queryByRole('radio')).toBeNull();
    expect(screen.queryByText(/continue enrollment/i)).toBeNull();

    await act(async () => {
      capturedGoogleCallback?.({ credential: 'google-id-token' });
    });
    expect(screen.queryByTestId('fake-google-iframe')).toBeNull();

    await waitFor(() => {
      expect(mockedCompleteGoogle).toHaveBeenCalledWith(
        'google-challenge',
        'server-nonce',
        'google-id-token',
        'temporary',
        'invite-secret',
        expect.any(AbortSignal),
      );
    });
    expect(mockPush).toHaveBeenCalledWith('/');
  });

  it('does not fall back to build-time Google configuration when runtime config is absent', async () => {
    process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID = 'build-time-client-id';
    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    expect(screen.getByRole('alert').textContent || '').toMatch(
      /sign-in is not available right now/i,
    );
    expect(screen.queryByText(/build-time-client-id/i)).toBeNull();
    expect(mockedStartGoogle).not.toHaveBeenCalled();
  });

  it('shows a neutral error when no sign-in provider is enabled', async () => {
    render(
      <AuthLanding
        mode="hosted"
        runtimeConfig={hostedConfig({
          google: { enabled: false, clientId: '' },
        })}
      />,
    );
    await waitForLoadingToFinish();

    expect(screen.getByRole('alert').textContent || '').toMatch(
      /sign-in is not available right now/i,
    );
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.queryByText(/client id|disabled|configured/i)).toBeNull();
    expect(
      screen.queryByRole('button', { name: /continue with google/i }),
    ).toBeNull();
    expect(mockedStartGoogle).not.toHaveBeenCalled();
  });

  it('shows a friendly error and starts a fresh challenge on retry', async () => {
    installGoogleMock();
    mockedStartGoogle
      .mockResolvedValueOnce({ success: false, error: 'google_unavailable' })
      .mockResolvedValueOnce({
        success: true,
        challengeId: 'second-challenge',
        nonce: 'second-nonce',
        expiresAt: 1234567890,
      });

    render(<AuthLanding mode="hosted" runtimeConfig={hostedConfig()} />);
    await waitForLoadingToFinish();

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent || '').toMatch(
        /temporarily unavailable/i,
      );
    });
    expect(screen.queryByText('google_unavailable')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /try again/i }));
    await waitFor(() => {
      expect(mockedStartGoogle).toHaveBeenCalledTimes(2);
      expect(mockGoogleInitialize).toHaveBeenCalledWith(
        expect.objectContaining({ nonce: 'second-nonce' }),
      );
      expect(mockGoogleRenderButton).toHaveBeenCalledTimes(1);
    });
  });

  it('recovers from an empty GIS callback without an infinite loading state', async () => {
    installGoogleMock();
    mockedStartGoogle
      .mockResolvedValueOnce({
        success: true,
        challengeId: 'first-challenge',
        nonce: 'first-nonce',
        expiresAt: 1234567890,
      })
      .mockResolvedValueOnce({
        success: true,
        challengeId: 'second-challenge',
        nonce: 'second-nonce',
        expiresAt: 1234567890,
      });

    render(<AuthLanding mode="hosted" runtimeConfig={hostedConfig()} />);
    await waitForLoadingToFinish();
    await waitForGoogleButton();

    await act(async () => {
      capturedGoogleCallback?.({});
    });
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent || '').toMatch(/cancelled/i);
    });
    expect(screen.queryByRole('status')).toBeNull();
    expect(mockedCompleteGoogle).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /try again/i }));
    await waitFor(() => {
      expect(mockedStartGoogle).toHaveBeenCalledTimes(2);
    });
    await waitForGoogleButton();
  });

  it('shows a friendly completion error and permits retry', async () => {
    installGoogleMock();
    mockedCompleteGoogle.mockResolvedValueOnce({ success: false });

    render(<AuthLanding mode="hosted" runtimeConfig={hostedConfig()} />);
    await waitForLoadingToFinish();
    await waitForGoogleButton();

    await act(async () => {
      capturedGoogleCallback?.({ credential: 'google-id-token' });
    });
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent || '').toMatch(
        /temporarily unavailable/i,
      );
    });
    expect(screen.queryByText(/failed: 500|invalid token|debug/i)).toBeNull();
    expect(screen.getByRole('button', { name: /try again/i })).toBeTruthy();
  });

  it('ignores duplicate GIS callbacks after completion starts', async () => {
    installGoogleMock();
    render(<AuthLanding mode="hosted" runtimeConfig={hostedConfig()} />);
    await waitForLoadingToFinish();
    await waitForGoogleButton();

    await act(async () => {
      capturedGoogleCallback?.({ credential: 'first-id-token' });
      capturedGoogleCallback?.({ credential: 'second-id-token' });
    });

    await waitFor(() => {
      expect(mockedCompleteGoogle).toHaveBeenCalledTimes(1);
    });
    expect(mockedCompleteGoogle).toHaveBeenCalledWith(
      'google-challenge',
      'server-nonce',
      'first-id-token',
      'temporary',
      undefined,
      expect.any(AbortSignal),
    );
  });

  it('ignores a late callback after the auth screen unmounts', async () => {
    installGoogleMock();
    const view = render(
      <AuthLanding mode="hosted" runtimeConfig={hostedConfig()} />,
    );
    await waitForLoadingToFinish();
    await waitForGoogleButton();
    const lateCallback = capturedGoogleCallback;
    expect(lateCallback).toBeTruthy();

    view.unmount();
    await act(async () => {
      lateCallback?.({ credential: 'late-google-id-token' });
    });

    expect(mockedCompleteGoogle).not.toHaveBeenCalled();
  });

  it('passes the document CSP nonce to GIS and recovers from a script failure', async () => {
    delete (window as Window & { google?: unknown }).google;
    const nonceMeta = document.createElement('meta');
    nonceMeta.name = 'csp-nonce';
    nonceMeta.content = 'document-style-nonce';
    document.head.appendChild(nonceMeta);
    mockedStartGoogle.mockResolvedValue({
      success: true,
      challengeId: 'retry-challenge',
      nonce: 'retry-nonce',
      expiresAt: 1234567890,
    });

    render(<AuthLanding mode="hosted" runtimeConfig={hostedConfig()} />);
    await waitForLoadingToFinish();

    let script: HTMLScriptElement | null;
    try {
      script = await waitFor(() => {
        const element = document.querySelector<HTMLScriptElement>(
          'script[src="https://accounts.google.com/gsi/client"]',
        );
        expect(element).toBeTruthy();
        return element;
      });
    } finally {
      nonceMeta.remove();
    }
    expect(script?.nonce).toBe('document-style-nonce');
    await act(async () => {
      script?.dispatchEvent(new Event('error'));
    });
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent || '').toMatch(
        /temporarily unavailable/i,
      );
    });
    expect(mockedStartGoogle).not.toHaveBeenCalled();
    expect(script?.isConnected).toBe(false);

    installGoogleMock();
    fireEvent.click(screen.getByRole('button', { name: /try again/i }));
    await waitFor(() => {
      expect(mockGoogleRenderButton).toHaveBeenCalled();
    });
  });

  it('keeps email hidden when the runtime provider is disabled', async () => {
    render(
      <AuthLanding
        mode="hosted"
        runtimeConfig={hostedConfig({
          email: { enabled: false },
          google: { enabled: true, clientId: 'runtime-client-id' },
        })}
      />,
    );
    await waitForLoadingToFinish();

    expect(screen.queryByLabelText(/email address/i)).toBeNull();
    expect(
      screen.queryByRole('button', { name: /send verification code/i }),
    ).toBeNull();
  });

  it('keeps provider controls hidden while runtime config is loading', async () => {
    render(<AuthLanding mode="hosted" runtimeConfigLoading />);
    await waitForLoadingToFinish();

    expect(screen.getByText(/loading sign-in providers/i)).toBeTruthy();
    expect(screen.queryByLabelText(/email address/i)).toBeNull();
    expect(screen.queryByTestId('google-signin-button')).toBeNull();
    expect(mockedStartGoogle).not.toHaveBeenCalled();
  });

  it('keeps hosted enrollment and device choices out of the normal login', async () => {
    render(
      <AuthLanding
        mode="hosted"
        runtimeConfig={hostedConfig({
          email: { enabled: true },
        })}
      />,
    );
    await waitForLoadingToFinish();

    expect(
      screen.queryByRole('heading', { name: /continue enrollment/i }),
    ).toBeNull();
    expect(screen.queryByPlaceholderText(/pending id/i)).toBeNull();
    expect(screen.queryByRole('radio')).toBeNull();
    expect(screen.queryByText(/this device is/i)).toBeNull();
    // The only persistence control is the single unchecked opt-in.
    expect(screen.getAllByRole('checkbox')).toHaveLength(1);
  });
});

describe('AuthLanding — optional hosted email flow', () => {
  it('starts and completes email sign-in with temporary persistence by default', async () => {
    render(
      <AuthLanding
        mode="hosted"
        runtimeConfig={hostedConfig({
          email: { enabled: true },
          google: { enabled: false, clientId: '' },
        })}
      />,
    );
    await waitForLoadingToFinish();

    fireEvent.change(screen.getByLabelText(/email address/i), {
      target: { value: 'user@example.com' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: /send verification code/i }),
    );

    await waitFor(() => {
      expect(mockedStartEmail).toHaveBeenCalledWith('user@example.com');
      expect(screen.getByLabelText(/verification code/i)).toBeTruthy();
    });
    expect(screen.queryByRole('radio')).toBeNull();
    expect(screen.queryByText(/web sign-in device/i)).toBeNull();

    fireEvent.change(screen.getByLabelText(/verification code/i), {
      target: { value: '123456' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: /verify and sign in/i }),
    );

    await waitFor(() => {
      expect(mockedCompleteEmail).toHaveBeenCalledWith(
        'ch-123',
        '123456',
        'temporary',
        undefined,
      );
    });
    expect(mockPush).toHaveBeenCalledWith('/');
  });

  it('passes an invite token without storing it in the form state', async () => {
    mockSearchParams = new URLSearchParams('invite_token=invite-secret');
    render(
      <AuthLanding
        mode="hosted"
        runtimeConfig={hostedConfig({
          email: { enabled: true },
          google: { enabled: false, clientId: '' },
        })}
      />,
    );
    await waitForLoadingToFinish();

    fireEvent.change(screen.getByLabelText(/email address/i), {
      target: { value: 'user@example.com' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: /send verification code/i }),
    );
    await waitFor(() =>
      expect(screen.getByLabelText(/verification code/i)).toBeTruthy(),
    );
    fireEvent.change(screen.getByLabelText(/verification code/i), {
      target: { value: '123456' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: /verify and sign in/i }),
    );

    await waitFor(() => {
      expect(mockedCompleteEmail).toHaveBeenCalledWith(
        'ch-123',
        '123456',
        'temporary',
        'invite-secret',
      );
    });
  });

  it('shows email start and completion errors and allows switching email', async () => {
    mockedStartEmail.mockResolvedValueOnce({
      success: false,
      error: 'rate limited',
    });
    render(
      <AuthLanding
        mode="hosted"
        runtimeConfig={hostedConfig({
          email: { enabled: true },
          google: { enabled: false, clientId: '' },
        })}
      />,
    );
    await waitForLoadingToFinish();

    fireEvent.change(screen.getByLabelText(/email address/i), {
      target: { value: 'user@example.com' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: /send verification code/i }),
    );
    await waitFor(() => {
      expect(screen.getByText('rate limited')).toBeTruthy();
    });

    mockedStartEmail.mockResolvedValueOnce({
      success: true,
      challengeId: 'new-email-challenge',
      expiresAt: 1234567890,
    });
    fireEvent.change(screen.getByLabelText(/email address/i), {
      target: { value: 'new@example.com' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: /send verification code/i }),
    );
    await waitFor(() => {
      expect(mockedStartEmail).toHaveBeenLastCalledWith('new@example.com');
      expect(screen.getByLabelText(/verification code/i)).toBeTruthy();
    });

    mockedCompleteEmail.mockResolvedValueOnce({
      success: false,
      error: 'bad code',
    });
    fireEvent.change(screen.getByLabelText(/verification code/i), {
      target: { value: '000000' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: /verify and sign in/i }),
    );
    await waitFor(() => {
      expect(screen.getByText('bad code')).toBeTruthy();
    });

    fireEvent.click(
      screen.getByRole('button', { name: /use a different email/i }),
    );
    expect(screen.getByLabelText(/email address/i)).toBeTruthy();
  });
});

describe('AuthLanding — keep me signed in and provider availability', () => {
  it('uses private persistence for Google when keep me signed in is checked without re-rendering the button', async () => {
    installGoogleMock();
    render(<AuthLanding mode="hosted" runtimeConfig={hostedConfig()} />);
    await waitForLoadingToFinish();
    await waitForGoogleButton();

    const checkbox = screen.getByRole('checkbox', {
      name: /keep me signed in/i,
    }) as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
    expect(screen.getByText(/sign out of Google/i)).toBeTruthy();

    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(true);
    expect(mockedStartGoogle).toHaveBeenCalledTimes(1);
    expect(mockGoogleRenderButton).toHaveBeenCalledTimes(1);

    await act(async () => {
      capturedGoogleCallback?.({ credential: 'google-id-token' });
    });
    await waitFor(() => {
      expect(mockedCompleteGoogle).toHaveBeenCalledWith(
        'google-challenge',
        'server-nonce',
        'google-id-token',
        'private',
        undefined,
        expect.any(AbortSignal),
      );
    });
  });

  it('uses private persistence for email when keep me signed in is checked', async () => {
    render(
      <AuthLanding
        mode="hosted"
        runtimeConfig={hostedConfig({
          email: { enabled: true },
          google: { enabled: false, clientId: '' },
        })}
      />,
    );
    await waitForLoadingToFinish();

    fireEvent.click(
      screen.getByRole('checkbox', { name: /keep me signed in/i }),
    );
    fireEvent.change(screen.getByLabelText(/email address/i), {
      target: { value: 'user@example.com' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: /send verification code/i }),
    );
    await waitFor(() => {
      expect(screen.getByLabelText(/verification code/i)).toBeTruthy();
    });
    fireEvent.change(screen.getByLabelText(/verification code/i), {
      target: { value: '123456' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: /verify and sign in/i }),
    );

    await waitFor(() => {
      expect(mockedCompleteEmail).toHaveBeenCalledWith(
        'ch-123',
        '123456',
        'private',
        undefined,
      );
    });
  });

  it('shows email sign-in without a Google error when only email is enabled', async () => {
    render(
      <AuthLanding
        mode="hosted"
        runtimeConfig={hostedConfig({
          email: { enabled: true },
          google: { enabled: false, clientId: '' },
        })}
      />,
    );
    await waitForLoadingToFinish();

    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByText(/google/i)).toBeNull();
    expect(screen.getByLabelText(/email address/i)).toBeTruthy();
    expect(
      screen.getByRole('checkbox', { name: /keep me signed in/i }),
    ).toBeTruthy();
    expect(mockedStartGoogle).not.toHaveBeenCalled();
  });

  it('resumes an existing session on retry instead of starting a new challenge', async () => {
    installGoogleMock();
    mockedCompleteGoogle.mockResolvedValueOnce({ success: false });

    render(<AuthLanding mode="hosted" runtimeConfig={hostedConfig()} />);
    await waitForLoadingToFinish();
    await waitForGoogleButton();

    await act(async () => {
      capturedGoogleCallback?.({ credential: 'google-id-token' });
    });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /try again/i })).toBeTruthy();
    });

    vi.mocked(refreshAccessToken).mockResolvedValueOnce({ success: true });
    fireEvent.click(screen.getByRole('button', { name: /try again/i }));

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith('/');
    });
    expect(mockedStartGoogle).toHaveBeenCalledTimes(1);
  });

  it('renders a fresh challenge before the current one expires', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      installGoogleMock();
      const expiresAt = Math.floor(Date.now() / 1000) + 120;
      mockedStartGoogle
        .mockResolvedValueOnce({
          success: true,
          challengeId: 'first-challenge',
          nonce: 'first-nonce',
          expiresAt,
        })
        .mockResolvedValueOnce({
          success: true,
          challengeId: 'second-challenge',
          nonce: 'second-nonce',
          expiresAt: expiresAt + 600,
        });

      render(<AuthLanding mode="hosted" runtimeConfig={hostedConfig()} />);
      await waitForLoadingToFinish();
      await waitForGoogleButton();
      expect(mockedStartGoogle).toHaveBeenCalledTimes(1);

      await act(async () => {
        vi.advanceTimersByTime(95_000);
      });

      await waitFor(() => {
        expect(mockedStartGoogle).toHaveBeenCalledTimes(2);
        expect(mockGoogleInitialize).toHaveBeenLastCalledWith(
          expect.objectContaining({ nonce: 'second-nonce' }),
        );
      });
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('AuthLanding — self-hosted setup and pairing', () => {
  it('retains setup and enrollment forms in resolved self-hosted mode', async () => {
    render(<AuthLanding mode="self-hosted" />);
    await waitForLoadingToFinish();

    expect(screen.getByLabelText(/setup token/i)).toBeTruthy();
    expect(
      screen.getByRole('heading', { name: /continue enrollment/i }),
    ).toBeTruthy();
    expect(screen.getByText(/why a form, not a url/i)).toBeTruthy();
  });

  it('surfaces setup and enrollment failures without exposing a hosted path', async () => {
    mockedCompleteSetup.mockResolvedValueOnce({
      success: false,
      error: 'Invalid setup token',
    });
    mockedCompleteEnrollment.mockResolvedValueOnce({
      success: false,
      error: 'Invalid enrollment code',
    });
    render(<AuthLanding mode="self-hosted" />);
    await waitForLoadingToFinish();

    fireEvent.change(screen.getByLabelText(/setup token/i), {
      target: { value: 'bad-setup-token' },
    });
    fireEvent.click(screen.getByRole('button', { name: /complete setup/i }));
    await waitFor(() => {
      expect(screen.getByText('Invalid setup token')).toBeTruthy();
    });

    fireEvent.change(screen.getByPlaceholderText(/pending id/i), {
      target: { value: 'bad-pending-id' },
    });
    fireEvent.change(screen.getByLabelText(/code/i), {
      target: { value: 'bad-code' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: /complete enrollment/i }),
    );
    await waitFor(() => {
      expect(screen.getByText('Invalid enrollment code')).toBeTruthy();
    });
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('submits setup and enrollment through their existing secure helpers', async () => {
    render(<AuthLanding mode="self-hosted" />);
    await waitForLoadingToFinish();

    fireEvent.change(screen.getByLabelText(/setup token/i), {
      target: { value: 'setup-secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: /complete setup/i }));
    await waitFor(() => {
      expect(mockedCompleteSetup).toHaveBeenCalledWith(
        'setup-secret',
        undefined,
      );
    });

    fireEvent.change(screen.getByPlaceholderText(/paste daemon-enroll/i), {
      target: { value: 'daemon-enroll://pending#code' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: /complete enrollment/i }),
    );
    await waitFor(() => {
      expect(mockedCompleteEnrollment).toHaveBeenCalledWith('pending', 'code');
    });
  });
});
