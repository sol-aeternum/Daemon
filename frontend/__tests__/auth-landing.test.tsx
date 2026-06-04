import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const mockPush = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: vi.fn(() => ({ push: mockPush })),
}));

vi.mock('../lib/auth', () => ({
  refreshAccessToken: vi.fn(() => Promise.resolve({ success: false })),
  completeSetup: vi.fn(() => Promise.resolve({ success: true })),
  completeEnrollment: vi.fn(() => Promise.resolve({ success: true })),
}));

import {
  refreshAccessToken,
  completeSetup,
  completeEnrollment,
} from '../lib/auth';
import AuthLanding from '../components/AuthLanding';

const mockedRefresh = vi.mocked(refreshAccessToken);
const mockedCompleteSetup = vi.mocked(completeSetup);
const mockedCompleteEnrollment = vi.mocked(completeEnrollment);

async function waitForLoadingToFinish(): Promise<void> {
  await waitFor(() => {
    expect(screen.queryByText(/Checking session/i)).toBeNull();
  });
}

describe('AuthLanding — hosted mode', () => {
  it('renders identity entry points before enrollment', async () => {
    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    const googleButton = screen.getByRole('button', {
      name: /continue with google/i,
    });
    const emailButton = screen.getByRole('button', {
      name: /continue with email/i,
    });
    const enrollmentHeading = screen.getByRole('heading', {
      name: /continue enrollment/i,
    });

    expect(googleButton).toBeTruthy();
    expect(emailButton).toBeTruthy();
    expect(enrollmentHeading).toBeTruthy();

    const googlePosition =
      googleButton.compareDocumentPosition(enrollmentHeading);
    expect(googlePosition & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it('shows identity cards as disabled with coming-soon reason', async () => {
    render(<AuthLanding mode="hosted" />);
    await waitForLoadingToFinish();

    const googleButton = screen.getByRole('button', {
      name: /continue with google/i,
    });
    const emailButton = screen.getByRole('button', {
      name: /continue with email/i,
    });

    expect(googleButton.hasAttribute('disabled')).toBe(true);
    expect(emailButton.hasAttribute('disabled')).toBe(true);
    const comingSoonElements = screen.getAllByText('Coming soon');
    expect(comingSoonElements.length).toBe(2);
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

    fireEvent.click(advancedButton);
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
