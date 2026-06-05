import { describe, expect, it, vi, beforeEach } from 'vitest';
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from '@testing-library/react';

const mockPush = vi.fn();
let mockSearchParams = new URLSearchParams();

vi.mock('next/navigation', () => ({
  useRouter: vi.fn(() => ({ push: mockPush })),
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
  refreshAccessToken,
  completeSetup,
  completeEnrollment,
  startEmailSignIn,
  completeEmailSignIn,
  startGoogleSignIn,
  completeGoogleSignIn,
} from '../lib/auth';
import AuthLanding from '../components/AuthLanding';

const mockedRefresh = vi.mocked(refreshAccessToken);
const mockedCompleteSetup = vi.mocked(completeSetup);
const mockedCompleteEnrollment = vi.mocked(completeEnrollment);
const mockedStartEmail = vi.mocked(startEmailSignIn);
const mockedCompleteEmail = vi.mocked(completeEmailSignIn);
const mockedStartGoogle = vi.mocked(startGoogleSignIn);
const mockedCompleteGoogle = vi.mocked(completeGoogleSignIn);

interface TestGoogleCredentialResponse {
  credential?: string;
}

interface TestGooglePromptNotification {
  getMomentType?: () => 'display' | 'skipped' | 'dismissed' | string;
  isNotDisplayed?: () => boolean;
  isSkippedMoment?: () => boolean;
  isDismissedMoment?: () => boolean;
  getDismissedReason?: () => string | undefined;
}

let capturedGoogleCallback:
  | ((response: TestGoogleCredentialResponse) => void)
  | null = null;
let capturedPromptCallback:
  | ((notification: TestGooglePromptNotification) => void)
  | null = null;
const mockGoogleInitialize = vi.fn(
  (config: {
    client_id: string;
    nonce: string;
    callback: (response: TestGoogleCredentialResponse) => void;
  }) => {
    capturedGoogleCallback = config.callback;
  },
);
const mockGooglePrompt = vi.fn(
  (cb?: (notification: TestGooglePromptNotification) => void) => {
    capturedPromptCallback = cb ?? null;
  },
);

function installGoogleMock(): void {
  capturedGoogleCallback = null;
  capturedPromptCallback = null;
  Object.defineProperty(window, 'google', {
    configurable: true,
    value: {
      accounts: {
        id: {
          initialize: mockGoogleInitialize,
          prompt: mockGooglePrompt,
        },
      },
    },
  });
}

beforeEach(() => {
  mockSearchParams = new URLSearchParams();
  delete process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;
  delete (window as Window & { google?: unknown }).google;
  capturedGoogleCallback = null;
  capturedPromptCallback = null;
  mockedRefresh.mockClear();
  mockedCompleteSetup.mockClear();
  mockedCompleteEnrollment.mockClear();
  mockedStartEmail.mockClear();
  mockedCompleteEmail.mockClear();
  mockedStartGoogle.mockClear();
  mockedCompleteGoogle.mockClear();
  mockGoogleInitialize.mockClear();
  mockGooglePrompt.mockClear();
  mockPush.mockClear();
});

async function waitForLoadingToFinish(): Promise<void> {
  await waitFor(() => {
    expect(screen.queryByText(/Checking session/i)).toBeNull();
  });
}

describe('AuthLanding — hosted mode', () => {
  it('renders identity entry points before enrollment', async () => {
    process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID = 'public-client-id';

    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    const googleButton = screen.getByRole('button', {
      name: /continue with google/i,
    });
    const emailInput = screen.getByLabelText(/email address/i);
    const enrollmentHeading = screen.getByRole('heading', {
      name: /continue enrollment/i,
    });

    expect(googleButton).toBeTruthy();
    expect(emailInput).toBeTruthy();
    expect(enrollmentHeading).toBeTruthy();

    const googlePosition =
      googleButton.compareDocumentPosition(enrollmentHeading);
    expect(googlePosition & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it('does not render an active Google button without a client ID', async () => {
    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    expect(
      screen.queryByRole('button', {
        name: /continue with google/i,
      }),
    ).toBeNull();

    const unavailableButton = screen.getByRole('button', {
      name: /google sign-in unavailable/i,
    });

    expect(unavailableButton.hasAttribute('disabled')).toBe(true);
    expect(screen.getByText('No Google client ID configured')).toBeTruthy();
  });

  it('renders an active Google button when a client ID is configured', async () => {
    process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID = 'public-client-id';

    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    const googleButton = screen.getByRole('button', {
      name: /continue with google/i,
    });

    expect(googleButton.hasAttribute('disabled')).toBe(false);
  });

  it('starts Google with a server nonce before completing with the GIS credential', async () => {
    process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID = 'public-client-id';
    installGoogleMock();
    mockedStartGoogle.mockResolvedValueOnce({
      success: true,
      challengeId: 'google-challenge',
      nonce: 'server-nonce',
      expiresAt: 1234567890,
    });
    mockedCompleteGoogle.mockResolvedValueOnce({ success: true });

    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    fireEvent.click(
      screen.getByRole('button', { name: /continue with google/i }),
    );

    await waitFor(() => {
      expect(mockedStartGoogle).toHaveBeenCalledTimes(1);
      expect(mockGoogleInitialize).toHaveBeenCalledWith({
        client_id: 'public-client-id',
        nonce: 'server-nonce',
        callback: expect.any(Function),
      });
      expect(mockedCompleteGoogle).not.toHaveBeenCalled();
    });

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
      );
    });
    expect(mockPush).toHaveBeenCalledWith('/');
  });

  it('maps public computer choice to temporary persistence for Google', async () => {
    process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID = 'public-client-id';
    installGoogleMock();
    mockedStartGoogle.mockResolvedValueOnce({
      success: true,
      challengeId: 'google-challenge',
      nonce: 'server-nonce',
      expiresAt: 1234567890,
    });
    mockedCompleteGoogle.mockResolvedValueOnce({ success: true });

    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    fireEvent.click(screen.getByRole('radio', { name: /public/i }));
    fireEvent.click(
      screen.getByRole('button', { name: /continue with google/i }),
    );

    await waitFor(() => {
      expect(mockGoogleInitialize).toHaveBeenCalled();
    });
    await act(async () => {
      capturedGoogleCallback?.({ credential: 'google-id-token' });
    });

    await waitFor(() => {
      expect(mockedCompleteGoogle).toHaveBeenCalledWith(
        'google-challenge',
        'server-nonce',
        'google-id-token',
        'temporary',
        undefined,
      );
    });
  });

  it('passes invite token from URL query to Google completion without storing it', async () => {
    mockSearchParams = new URLSearchParams('invite=invite-secret');
    process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID = 'public-client-id';
    installGoogleMock();
    mockedStartGoogle.mockResolvedValueOnce({
      success: true,
      challengeId: 'google-challenge',
      nonce: 'server-nonce',
      expiresAt: 1234567890,
    });
    mockedCompleteGoogle.mockResolvedValueOnce({ success: true });

    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    fireEvent.click(
      screen.getByRole('button', { name: /continue with google/i }),
    );

    await waitFor(() => {
      expect(mockGoogleInitialize).toHaveBeenCalled();
    });
    await act(async () => {
      capturedGoogleCallback?.({ credential: 'google-id-token' });
    });

    await waitFor(() => {
      expect(mockedCompleteGoogle).toHaveBeenCalledWith(
        'google-challenge',
        'server-nonce',
        'google-id-token',
        'private',
        'invite-secret',
      );
    });
  });

  it('shows a recoverable error when Google start fails', async () => {
    process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID = 'public-client-id';
    installGoogleMock();
    mockedStartGoogle.mockResolvedValueOnce({
      success: false,
      error: 'google_unavailable',
    });

    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    fireEvent.click(
      screen.getByRole('button', { name: /continue with google/i }),
    );

    await waitFor(() => {
      expect(screen.getByText('google_unavailable')).toBeTruthy();
    });
    expect(mockedCompleteGoogle).not.toHaveBeenCalled();
    expect(
      screen.getByRole('button', { name: /continue with google/i }),
    ).toBeTruthy();
  });

  it('shows a recoverable error when GIS returns no credential', async () => {
    process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID = 'public-client-id';
    installGoogleMock();
    mockedStartGoogle.mockResolvedValueOnce({
      success: true,
      challengeId: 'google-challenge',
      nonce: 'server-nonce',
      expiresAt: 1234567890,
    });

    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    fireEvent.click(
      screen.getByRole('button', { name: /continue with google/i }),
    );

    await waitFor(() => {
      expect(mockGoogleInitialize).toHaveBeenCalled();
    });
    await act(async () => {
      capturedGoogleCallback?.({});
    });

    await waitFor(() => {
      expect(
        screen.getByText(/google sign-in was cancelled or unavailable/i),
      ).toBeTruthy();
    });
    expect(mockedCompleteGoogle).not.toHaveBeenCalled();
  });

  it('shows a recoverable error when Google complete fails', async () => {
    process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID = 'public-client-id';
    installGoogleMock();
    mockedStartGoogle.mockResolvedValueOnce({
      success: true,
      challengeId: 'google-challenge',
      nonce: 'server-nonce',
      expiresAt: 1234567890,
    });
    mockedCompleteGoogle.mockResolvedValueOnce({
      success: false,
      error: 'Google sign-in failed. Please try again.',
    });

    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    fireEvent.click(
      screen.getByRole('button', { name: /continue with google/i }),
    );

    await waitFor(() => {
      expect(mockGoogleInitialize).toHaveBeenCalled();
    });
    await act(async () => {
      capturedGoogleCallback?.({ credential: 'google-id-token' });
    });

    await waitFor(() => {
      expect(
        screen.getByText('Google sign-in failed. Please try again.'),
      ).toBeTruthy();
    });
    expect(mockPush).not.toHaveBeenCalled();
  });

  it.each([
    [
      'not displayed',
      {
        getMomentType: () => 'display',
        isNotDisplayed: () => true,
      },
    ],
    [
      'skipped',
      {
        getMomentType: () => 'skipped',
        isSkippedMoment: () => true,
      },
    ],
    [
      'dismissed without credential',
      {
        getMomentType: () => 'dismissed',
        isDismissedMoment: () => true,
        getDismissedReason: () => 'cancel_called',
      },
    ],
  ] satisfies Array<[string, TestGooglePromptNotification]>)(
    'shows a recoverable error when the GIS prompt is %s',
    async (_label, notification) => {
      process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID = 'public-client-id';
      installGoogleMock();
      mockedStartGoogle.mockResolvedValueOnce({
        success: true,
        challengeId: 'google-challenge',
        nonce: 'server-nonce',
        expiresAt: 1234567890,
      });

      render(<AuthLanding mode="hosted" />);
      await waitForLoadingToFinish();

      fireEvent.click(
        screen.getByRole('button', { name: /continue with google/i }),
      );

      await waitFor(() => {
        expect(capturedPromptCallback).toBeTruthy();
      });

      await act(async () => {
        capturedPromptCallback?.(notification);
      });

      await waitFor(() => {
        expect(
          screen.getByText(/google sign-in was cancelled or unavailable/i),
        ).toBeTruthy();
      });
      expect(mockedCompleteGoogle).not.toHaveBeenCalled();
      const recoveredButton = screen.getByRole('button', {
        name: /continue with google/i,
      });
      expect(recoveredButton.hasAttribute('disabled')).toBe(false);
    },
  );

  it('does not reject when GIS reports a credential_returned dismissed moment', async () => {
    process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID = 'public-client-id';
    installGoogleMock();
    mockedStartGoogle.mockResolvedValueOnce({
      success: true,
      challengeId: 'google-challenge',
      nonce: 'server-nonce',
      expiresAt: 1234567890,
    });
    mockedCompleteGoogle.mockResolvedValueOnce({ success: true });

    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    fireEvent.click(
      screen.getByRole('button', { name: /continue with google/i }),
    );

    await waitFor(() => {
      expect(capturedPromptCallback).toBeTruthy();
    });

    await act(async () => {
      capturedPromptCallback?.({
        getMomentType: () => 'dismissed',
        isDismissedMoment: () => true,
        getDismissedReason: () => 'credential_returned',
      });
      capturedGoogleCallback?.({ credential: 'google-id-token' });
    });

    await waitFor(() => {
      expect(mockedCompleteGoogle).toHaveBeenCalledWith(
        'google-challenge',
        'server-nonce',
        'google-id-token',
        'private',
        undefined,
      );
    });
    expect(
      screen.queryByText(/google sign-in was cancelled or unavailable/i),
    ).toBeNull();
  });

  it('hides setup token form behind an advanced section', async () => {
    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    expect(screen.queryByLabelText(/setup token/i)).toBeNull();

    const advancedButton = screen.getByRole('button', {
      name: /advanced/i,
    });
    expect(advancedButton).toBeTruthy();
    expect(advancedButton.getAttribute('aria-expanded')).toBe('false');

    await act(async () => {
      fireEvent.click(advancedButton);
    });
    expect(advancedButton.getAttribute('aria-expanded')).toBe('true');

    expect(screen.getByLabelText(/setup token/i)).toBeTruthy();
  });

  it('shows enrollment form in hosted mode', async () => {
    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    expect(screen.getByPlaceholderText(/paste daemon-enroll/i)).toBeTruthy();
    expect(screen.getByPlaceholderText(/pending id/i)).toBeTruthy();
    expect(screen.getByPlaceholderText(/^code$/i)).toBeTruthy();
    expect(
      screen.getByRole('button', { name: /complete enrollment/i }),
    ).toBeTruthy();
  });
});

describe('AuthLanding — self-hosted mode', () => {
  it('renders setup token form before enrollment', async () => {
    render(<AuthLanding mode="self-hosted" />);
    await waitForLoadingToFinish();

    const setupLabel = screen.getByLabelText(/setup token/i);
    const enrollmentHeading = screen.getByRole('heading', {
      name: /continue enrollment/i,
    });

    expect(setupLabel).toBeTruthy();
    expect(enrollmentHeading).toBeTruthy();

    const setupPosition = setupLabel.compareDocumentPosition(enrollmentHeading);
    expect(setupPosition & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it('does not show identity cards in self-hosted mode', async () => {
    render(<AuthLanding mode="self-hosted" />);
    await waitForLoadingToFinish();

    expect(
      screen.queryByRole('button', { name: /continue with google/i }),
    ).toBeNull();
    expect(
      screen.queryByRole('button', { name: /continue with email/i }),
    ).toBeNull();
  });

  it('shows enrollment form in self-hosted mode', async () => {
    render(<AuthLanding mode="self-hosted" />);
    await waitForLoadingToFinish();

    expect(screen.getByPlaceholderText(/paste daemon-enroll/i)).toBeTruthy();
    expect(
      screen.getByRole('button', { name: /complete enrollment/i }),
    ).toBeTruthy();
  });

  it('shows the security notice about POST-only tokens', async () => {
    render(<AuthLanding mode="self-hosted" />);
    await waitForLoadingToFinish();

    expect(screen.getByText(/why a form, not a url/i)).toBeTruthy();
    expect(screen.getByText(/sent in a post body only/i)).toBeTruthy();
  });
});

describe('AuthLanding — setup token submission', () => {
  it('submits setup token in a POST body via completeSetup', async () => {
    mockedCompleteSetup.mockResolvedValueOnce({ success: true });
    mockPush.mockClear();

    render(<AuthLanding mode="self-hosted" />);
    await waitForLoadingToFinish();

    const tokenInput = screen.getByLabelText(/setup token/i);
    fireEvent.change(tokenInput, { target: { value: 'my-secret-token' } });

    const submitButton = screen.getByRole('button', {
      name: /complete setup/i,
    });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(mockedCompleteSetup).toHaveBeenCalledWith(
        'my-secret-token',
        undefined,
      );
    });

    expect(mockPush).toHaveBeenCalledWith('/');
  });

  it('shows setup error when completeSetup fails', async () => {
    mockedCompleteSetup.mockResolvedValueOnce({
      success: false,
      error: 'Invalid setup token',
    });
    mockPush.mockClear();

    render(<AuthLanding mode="self-hosted" />);
    await waitForLoadingToFinish();

    const tokenInput = screen.getByLabelText(/setup token/i);
    fireEvent.change(tokenInput, { target: { value: 'bad-token' } });

    const submitButton = screen.getByRole('button', {
      name: /complete setup/i,
    });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(screen.getByText('Invalid setup token')).toBeTruthy();
    });

    expect(mockPush).not.toHaveBeenCalled();
  });
});

describe('AuthLanding — enrollment submission', () => {
  it('submits enrollment via completeEnrollment with parsed payload', async () => {
    mockedCompleteEnrollment.mockResolvedValueOnce({ success: true });
    mockPush.mockClear();

    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    const payloadInput = screen.getByPlaceholderText(/paste daemon-enroll/i);
    fireEvent.change(payloadInput, {
      target: { value: 'daemon-enroll://abc123#xyz789' },
    });

    const submitButton = screen.getByRole('button', {
      name: /complete enrollment/i,
    });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(mockedCompleteEnrollment).toHaveBeenCalledWith('abc123', 'xyz789');
    });

    expect(mockPush).toHaveBeenCalledWith('/');
  });

  it('shows enrollment error when completeEnrollment fails', async () => {
    mockedCompleteEnrollment.mockResolvedValueOnce({
      success: false,
      error: 'Invalid enrollment code',
    });
    mockPush.mockClear();

    render(<AuthLanding mode="self-hosted" />);
    await waitForLoadingToFinish();

    const pendingInput = screen.getByPlaceholderText(/pending id/i);
    const codeInput = screen.getByPlaceholderText(/^code$/i);
    fireEvent.change(pendingInput, { target: { value: 'pid-1' } });
    fireEvent.change(codeInput, { target: { value: 'wrong' } });

    const submitButton = screen.getByRole('button', {
      name: /complete enrollment/i,
    });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(screen.getByText('Invalid enrollment code')).toBeTruthy();
    });

    expect(mockPush).not.toHaveBeenCalled();
  });
});

describe('AuthLanding — email sign-in flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('starts email sign-in with generic user-facing behavior', async () => {
    mockedStartEmail.mockResolvedValueOnce({
      success: true,
      challengeId: 'ch-abc',
      expiresAt: 1234567890,
    });

    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    const emailInput = screen.getByLabelText(/email address/i);
    fireEvent.change(emailInput, { target: { value: 'user@example.com' } });

    const sendButton = screen.getByRole('button', {
      name: /send verification code/i,
    });
    fireEvent.click(sendButton);

    await waitFor(() => {
      expect(mockedStartEmail).toHaveBeenCalledWith('user@example.com');
    });

    expect(screen.getByLabelText(/verification code/i)).toBeTruthy();
    expect(screen.getByText(/web sign-in device/i)).toBeTruthy();
  });

  it('completes email sign-in and redirects on success', async () => {
    mockedStartEmail.mockResolvedValueOnce({
      success: true,
      challengeId: 'ch-abc',
      expiresAt: 1234567890,
    });
    mockedCompleteEmail.mockResolvedValueOnce({ success: true });
    mockPush.mockClear();

    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    const emailInput = screen.getByLabelText(/email address/i);
    fireEvent.change(emailInput, { target: { value: 'user@example.com' } });

    const sendButton = screen.getByRole('button', {
      name: /send verification code/i,
    });
    fireEvent.click(sendButton);

    await waitFor(() => {
      expect(screen.getByLabelText(/verification code/i)).toBeTruthy();
    });
    expect(screen.getByText(/web sign-in device/i)).toBeTruthy();

    const codeInput = screen.getByLabelText(/verification code/i);
    fireEvent.change(codeInput, { target: { value: '123456' } });

    const verifyButton = screen.getByRole('button', {
      name: /verify and sign in/i,
    });
    fireEvent.click(verifyButton);

    await waitFor(() => {
      expect(mockedCompleteEmail).toHaveBeenCalledWith(
        'ch-abc',
        '123456',
        'private',
        undefined,
      );
    });

    expect(mockPush).toHaveBeenCalledWith('/');
  });

  it('passes invite token from URL query to email completion without storing it', async () => {
    mockSearchParams = new URLSearchParams('invite_token=invite-secret');
    mockedStartEmail.mockResolvedValueOnce({
      success: true,
      challengeId: 'ch-abc',
      expiresAt: 1234567890,
    });
    mockedCompleteEmail.mockResolvedValueOnce({ success: true });

    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

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
        'ch-abc',
        '123456',
        'private',
        'invite-secret',
      );
    });
  });

  it('maps public computer choice to temporary persistence', async () => {
    mockedStartEmail.mockResolvedValueOnce({
      success: true,
      challengeId: 'ch-abc',
      expiresAt: 1234567890,
    });
    mockedCompleteEmail.mockResolvedValueOnce({ success: true });

    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    const emailInput = screen.getByLabelText(/email address/i);
    fireEvent.change(emailInput, { target: { value: 'user@example.com' } });

    const sendButton = screen.getByRole('button', {
      name: /send verification code/i,
    });
    fireEvent.click(sendButton);

    await waitFor(() => {
      expect(screen.getByLabelText(/verification code/i)).toBeTruthy();
    });
    expect(screen.getByText(/web sign-in device/i)).toBeTruthy();

    const publicRadio = screen.getByRole('radio', { name: /public/i });
    fireEvent.click(publicRadio);

    const codeInput = screen.getByLabelText(/verification code/i);
    fireEvent.change(codeInput, { target: { value: '654321' } });

    const verifyButton = screen.getByRole('button', {
      name: /verify and sign in/i,
    });
    fireEvent.click(verifyButton);

    await waitFor(() => {
      expect(mockedCompleteEmail).toHaveBeenCalledWith(
        'ch-abc',
        '654321',
        'temporary',
        undefined,
      );
    });
  });

  it('shows generic error when email start fails', async () => {
    mockedStartEmail.mockResolvedValueOnce({
      success: false,
      error: 'Rate limit exceeded',
    });

    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    const emailInput = screen.getByLabelText(/email address/i);
    fireEvent.change(emailInput, { target: { value: 'user@example.com' } });

    const sendButton = screen.getByRole('button', {
      name: /send verification code/i,
    });
    fireEvent.click(sendButton);

    await waitFor(() => {
      expect(screen.getByText(/rate limit exceeded/i)).toBeTruthy();
    });
  });

  it('shows generic error when email complete fails', async () => {
    mockedStartEmail.mockResolvedValueOnce({
      success: true,
      challengeId: 'ch-abc',
      expiresAt: 1234567890,
    });
    mockedCompleteEmail.mockResolvedValueOnce({
      success: false,
      error: 'Invalid or expired code. Please try again.',
    });

    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    const emailInput = screen.getByLabelText(/email address/i);
    fireEvent.change(emailInput, { target: { value: 'user@example.com' } });

    const sendButton = screen.getByRole('button', {
      name: /send verification code/i,
    });
    fireEvent.click(sendButton);

    await waitFor(() => {
      expect(screen.getByLabelText(/verification code/i)).toBeTruthy();
    });
    expect(screen.getByText(/web sign-in device/i)).toBeTruthy();

    const codeInput = screen.getByLabelText(/verification code/i);
    fireEvent.change(codeInput, { target: { value: '000000' } });

    const verifyButton = screen.getByRole('button', {
      name: /verify and sign in/i,
    });
    fireEvent.click(verifyButton);

    await waitFor(() => {
      expect(screen.getByText(/invalid or expired code/i)).toBeTruthy();
    });
  });

  it('allows switching to a different email from code step', async () => {
    mockedStartEmail.mockResolvedValueOnce({
      success: true,
      challengeId: 'ch-abc',
      expiresAt: 1234567890,
    });

    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    const emailInput = screen.getByLabelText(/email address/i);
    fireEvent.change(emailInput, { target: { value: 'old@example.com' } });

    const sendButton = screen.getByRole('button', {
      name: /send verification code/i,
    });
    fireEvent.click(sendButton);

    await waitFor(() => {
      expect(screen.getByLabelText(/verification code/i)).toBeTruthy();
    });

    const switchButton = screen.getByRole('button', {
      name: /use a different email/i,
    });
    await act(async () => {
      fireEvent.click(switchButton);
    });

    expect(screen.getByLabelText(/email address/i)).toBeTruthy();
  });
});
