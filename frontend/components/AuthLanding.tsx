'use client';

import { useState, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  refreshAccessToken,
  completeSetup,
  completeEnrollment,
  startEmailSignIn,
  completeEmailSignIn,
  startGoogleSignIn,
  completeGoogleSignIn,
} from '../lib/auth';
import { getEmailEnabled, getGoogleClientId } from '../lib/deployment';
import type { AuthConfig } from '../lib/auth-config';
import { Sparkles, Shield, AlertCircle, Chrome, Monitor } from 'lucide-react';

export type DeploymentMode = 'hosted' | 'self-hosted';

interface GoogleCredentialResponse {
  credential?: string;
}

interface GooglePromptNotification {
  getMomentType?: () => 'display' | 'skipped' | 'dismissed' | string;
  isNotDisplayed?: () => boolean;
  isSkippedMoment?: () => boolean;
  isDismissedMoment?: () => boolean;
  getNotDisplayedReason?: () => unknown;
  getSkippedReason?: () => unknown;
  getDismissedReason?: () => string | undefined;
}

interface GoogleIdentityServices {
  accounts: {
    id: {
      initialize: (config: {
        client_id: string;
        nonce: string;
        callback: (response: GoogleCredentialResponse) => void;
      }) => void;
      prompt?: (
        callback?: (notification: GooglePromptNotification) => void,
      ) => void;
    };
  };
}

declare global {
  interface Window {
    google?: GoogleIdentityServices;
  }
}

const GOOGLE_GIS_SCRIPT_SRC = 'https://accounts.google.com/gsi/client';

function loadGoogleIdentityServices(): Promise<GoogleIdentityServices> {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return Promise.reject(new Error('Google sign-in is unavailable here.'));
  }
  if (window.google?.accounts?.id) {
    return Promise.resolve(window.google);
  }

  return new Promise((resolve, reject) => {
    const existingScript = document.querySelector<HTMLScriptElement>(
      `script[src="${GOOGLE_GIS_SCRIPT_SRC}"]`,
    );

    const finish = () => {
      if (window.google?.accounts?.id) {
        resolve(window.google);
      } else {
        reject(new Error('Google sign-in did not finish loading.'));
      }
    };

    if (existingScript) {
      existingScript.addEventListener('load', finish, { once: true });
      existingScript.addEventListener(
        'error',
        () => reject(new Error('Google sign-in failed to load.')),
        { once: true },
      );
      return;
    }

    const script = document.createElement('script');
    script.src = GOOGLE_GIS_SCRIPT_SRC;
    script.async = true;
    script.defer = true;
    script.onload = finish;
    script.onerror = () => reject(new Error('Google sign-in failed to load.'));
    document.head.appendChild(script);
  });
}

interface AuthLandingProps {
  mode: DeploymentMode;
  runtimeConfig?: Pick<AuthConfig, 'email' | 'google'>;
  runtimeConfigLoading?: boolean;
}

export default function AuthLanding({
  mode,
  runtimeConfig,
  runtimeConfigLoading = false,
}: AuthLandingProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [isChecking, setIsChecking] = useState(true);

  const [setupToken, setSetupToken] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [setupError, setSetupError] = useState<string | null>(null);

  const [enrollmentPayload, setEnrollmentPayload] = useState('');
  const [enrollmentPendingId, setEnrollmentPendingId] = useState('');
  const [enrollmentCode, setEnrollmentCode] = useState('');
  const [isEnrolling, setIsEnrolling] = useState(false);
  const [enrollmentError, setEnrollmentError] = useState<string | null>(null);

  const [email, setEmail] = useState('');
  const [emailChallengeId, setEmailChallengeId] = useState('');
  const [emailCode, setEmailCode] = useState('');
  const [emailStep, setEmailStep] = useState<'idle' | 'code'>('idle');
  const [emailError, setEmailError] = useState<string | null>(null);
  const [isEmailStarting, setIsEmailStarting] = useState(false);
  const [isEmailCompleting, setIsEmailCompleting] = useState(false);
  const [googleError, setGoogleError] = useState<string | null>(null);
  const [isGoogleStarting, setIsGoogleStarting] = useState(false);
  const [devicePersistence, setDevicePersistence] = useState<
    'private' | 'temporary'
  >('private');
  const inviteToken =
    searchParams.get('invite_token')?.trim() ||
    searchParams.get('invite')?.trim() ||
    undefined;

  useEffect(() => {
    let cancelled = false;

    async function checkAuth() {
      const result = await refreshAccessToken().catch(() => null);
      if (!cancelled && result?.success) {
        router.push('/');
        return;
      }
      if (!cancelled) {
        setIsChecking(false);
      }
    }

    void checkAuth();

    return () => {
      cancelled = true;
    };
  }, [router]);

  async function handleGoogleSignIn() {
    const clientId = getGoogleClientId(mode);
    if (!clientId) return;

    setGoogleError(null);
    setIsGoogleStarting(true);
    try {
      const startResult = await startGoogleSignIn();
      if (
        !startResult.success ||
        !startResult.challengeId ||
        !startResult.nonce
      ) {
        setGoogleError(
          startResult.error ||
            'Unable to start Google sign-in. Please try again.',
        );
        return;
      }

      const google = await loadGoogleIdentityServices();
      const idToken = await new Promise<string>((resolve, reject) => {
        let settled = false;
        const resolveCredential = (credential: string) => {
          if (settled) return;
          settled = true;
          resolve(credential);
        };
        const rejectGooglePrompt = () => {
          if (settled) return;
          settled = true;
          reject(
            new Error(
              'Google sign-in was cancelled or unavailable. Please try again.',
            ),
          );
        };

        google.accounts.id.initialize({
          client_id: clientId,
          nonce: startResult.nonce!,
          callback: (response) => {
            if (response.credential) {
              resolveCredential(response.credential);
              return;
            }
            rejectGooglePrompt();
          },
        });

        const promptFn = google.accounts.id.prompt;
        if (typeof promptFn === 'function') {
          promptFn((notification) => {
            const momentType = notification.getMomentType?.();
            const isNotDisplayed = notification.isNotDisplayed?.() ?? false;
            const isSkipped =
              notification.isSkippedMoment?.() ?? momentType === 'skipped';
            const isDismissed =
              notification.isDismissedMoment?.() ?? momentType === 'dismissed';

            if (isNotDisplayed || isSkipped) {
              rejectGooglePrompt();
              return;
            }

            if (isDismissed) {
              const dismissedReason = notification.getDismissedReason?.();
              if (dismissedReason !== 'credential_returned') {
                rejectGooglePrompt();
              }
              return;
            }
          });
        }
      });

      const completeResult = await completeGoogleSignIn(
        startResult.challengeId,
        startResult.nonce,
        idToken,
        devicePersistence,
        inviteToken,
      );
      if (completeResult.success) {
        router.push('/');
      } else {
        setGoogleError(
          completeResult.error || 'Google sign-in failed. Please try again.',
        );
      }
    } catch (err) {
      setGoogleError(
        err instanceof Error
          ? err.message
          : 'Google sign-in failed. Please try again.',
      );
    } finally {
      setIsGoogleStarting(false);
    }
  }

  async function handleSetupSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSetupError(null);

    const token = setupToken.trim();
    if (!token) {
      setSetupError('Please enter the setup token.');
      return;
    }

    setIsSubmitting(true);
    try {
      const result = await completeSetup(
        token,
        displayName.trim() || undefined,
      );
      if (result.success) {
        router.push('/');
      } else {
        setSetupError(result.error || 'Setup failed. Please try again.');
      }
    } catch {
      setSetupError(
        'Network error. Please check your connection and try again.',
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  function parseEnrollmentPayload(
    payload: string,
  ): { pendingId: string; code: string } | null {
    const trimmed = payload.trim();
    if (trimmed.startsWith('daemon-enroll://')) {
      const inner = trimmed.slice('daemon-enroll://'.length);
      const parts = inner.split('#');
      if (parts.length === 2 && parts[0] && parts[1]) {
        return { pendingId: parts[0], code: parts[1] };
      }
    }
    return null;
  }

  async function handleEnrollSubmit(e: React.FormEvent) {
    e.preventDefault();
    setEnrollmentError(null);

    let pendingId = enrollmentPendingId.trim();
    let code = enrollmentCode.trim();

    const parsed = parseEnrollmentPayload(enrollmentPayload);
    if (parsed) {
      pendingId = parsed.pendingId;
      code = parsed.code;
    }

    if (!pendingId || !code) {
      setEnrollmentError(
        'Please provide both pending ID and code, or paste the full enrollment link.',
      );
      return;
    }

    setIsEnrolling(true);
    try {
      const result = await completeEnrollment(pendingId, code);
      if (result.success) {
        router.push('/');
      } else {
        setEnrollmentError(
          result.error || 'Enrollment failed. Please try again.',
        );
      }
    } catch {
      setEnrollmentError(
        'Network error. Please check your connection and try again.',
      );
    } finally {
      setIsEnrolling(false);
    }
  }

  async function handleEmailStart(e: React.FormEvent) {
    e.preventDefault();
    setEmailError(null);

    const trimmedEmail = email.trim();
    if (!trimmedEmail) {
      setEmailError('Please enter your email address.');
      return;
    }

    setIsEmailStarting(true);
    try {
      const result = await startEmailSignIn(trimmedEmail);
      if (result.success && result.challengeId) {
        setEmailChallengeId(result.challengeId);
        setEmailStep('code');
      } else {
        setEmailError(result.error || 'Unable to send code. Please try again.');
      }
    } catch {
      setEmailError(
        'Network error. Please check your connection and try again.',
      );
    } finally {
      setIsEmailStarting(false);
    }
  }

  async function handleEmailComplete(e: React.FormEvent) {
    e.preventDefault();
    setEmailError(null);

    const trimmedCode = emailCode.trim();
    if (!trimmedCode) {
      setEmailError('Please enter the verification code.');
      return;
    }

    setIsEmailCompleting(true);
    try {
      const result = await completeEmailSignIn(
        emailChallengeId,
        trimmedCode,
        devicePersistence,
        inviteToken,
      );
      if (result.success) {
        router.push('/');
      } else {
        setEmailError(result.error || 'Sign-in failed. Please try again.');
      }
    } catch {
      setEmailError(
        'Network error. Please check your connection and try again.',
      );
    } finally {
      setIsEmailCompleting(false);
    }
  }

  if (isChecking) {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-[var(--color-bg-tertiary)]">
        <div className="flex flex-col items-center gap-4">
          <div className="relative">
            <div className="absolute inset-0 bg-[var(--color-accent-primary)] blur-xl opacity-30 rounded-full animate-pulse" />
            <div className="relative w-12 h-12 rounded-xl bg-gradient-to-br from-[var(--color-accent-primary)] to-[var(--color-accent-hover)] flex items-center justify-center shadow-lg">
              <Sparkles
                className="w-6 h-6 text-white animate-spin"
                style={{ animationDuration: '2s' }}
              />
            </div>
          </div>
          <p className="text-sm text-[var(--color-text-muted)]">
            Checking session...
          </p>
        </div>
      </div>
    );
  }

  const isHosted = mode === 'hosted';
  const googleClientId = isHosted
    ? runtimeConfig
      ? runtimeConfig.google.enabled
        ? runtimeConfig.google.clientId
        : ''
      : getGoogleClientId(mode)
    : '';
  const emailEnabled = isHosted
    ? runtimeConfig
      ? runtimeConfig.email.enabled
      : getEmailEnabled(mode)
    : false;

  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-[var(--color-bg-tertiary)] px-4 py-12">
      <div className="w-full max-w-md space-y-8">
        <div className="flex flex-col items-center text-center space-y-4">
          <div className="relative">
            <div className="absolute inset-0 bg-[var(--color-accent-primary)] blur-xl opacity-30 rounded-full" />
            <div className="relative w-14 h-14 rounded-2xl bg-gradient-to-br from-[var(--color-accent-primary)] to-[var(--color-accent-hover)] flex items-center justify-center shadow-lg">
              <Sparkles className="w-7 h-7 text-white" />
            </div>
          </div>
          <div>
            <h1 className="text-2xl font-bold text-[var(--color-text-primary)] tracking-tight">
              Welcome to Daemon
            </h1>
            <p className="text-sm text-[var(--color-text-muted)] mt-1">
              {isHosted
                ? 'Sign in to your account to get started'
                : 'Complete first-boot setup to get started'}
            </p>
          </div>
        </div>

        {isHosted && (
          <div className="space-y-3">
            {runtimeConfigLoading ? (
              <IdentityCard
                icon={<Chrome className="w-5 h-5" />}
                label="Loading sign-in providers..."
                disabled
              />
            ) : googleClientId ? (
              <IdentityCard
                icon={<Chrome className="w-5 h-5" />}
                label={
                  isGoogleStarting
                    ? 'Starting Google sign-in...'
                    : 'Continue with Google'
                }
                onClick={handleGoogleSignIn}
                disabled={isGoogleStarting}
              />
            ) : (
              <IdentityCard
                icon={<Chrome className="w-5 h-5" />}
                label="Google sign-in unavailable"
                disabled
                disabledReason="No Google client ID configured"
              />
            )}

            <DevicePersistenceChooser
              devicePersistence={devicePersistence}
              disabled={isGoogleStarting || isEmailCompleting}
              onChange={setDevicePersistence}
            />

            {googleError && (
              <div className="flex items-start gap-2.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2.5">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
                <p className="text-sm text-red-300">{googleError}</p>
              </div>
            )}

            {emailEnabled &&
              (emailStep === 'idle' ? (
                <form onSubmit={handleEmailStart} className="space-y-4">
                  <div>
                    <label
                      htmlFor="email-address"
                      className="block text-sm font-medium text-[var(--color-text-secondary)] mb-1.5"
                    >
                      Email Address
                    </label>
                    <input
                      id="email-address"
                      type="email"
                      autoComplete="email"
                      placeholder="you@example.com"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      disabled={isEmailStarting}
                      className="w-full rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-3 py-2.5 text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent-primary)] disabled:opacity-50 disabled:cursor-not-allowed"
                    />
                  </div>

                  {emailError && (
                    <div className="flex items-start gap-2.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2.5">
                      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
                      <p className="text-sm text-red-300">{emailError}</p>
                    </div>
                  )}

                  <button
                    type="submit"
                    disabled={isEmailStarting || !email.trim()}
                    className="w-full rounded-xl bg-[var(--color-accent-primary)] px-4 py-3 text-sm font-semibold text-white shadow-sm hover:bg-[var(--color-accent-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--color-bg-tertiary)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {isEmailStarting
                      ? 'Sending code...'
                      : 'Send verification code'}
                  </button>
                </form>
              ) : (
                <form onSubmit={handleEmailComplete} className="space-y-4">
                  <div>
                    <label
                      htmlFor="email-code"
                      className="block text-sm font-medium text-[var(--color-text-secondary)] mb-1.5"
                    >
                      Verification Code
                    </label>
                    <input
                      id="email-code"
                      type="text"
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      placeholder="Enter 6-digit code"
                      value={emailCode}
                      onChange={(e) => setEmailCode(e.target.value)}
                      disabled={isEmailCompleting}
                      className="w-full rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-3 py-2.5 text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent-primary)] disabled:opacity-50 disabled:cursor-not-allowed"
                    />
                  </div>

                  {emailError && (
                    <div className="flex items-start gap-2.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2.5">
                      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
                      <p className="text-sm text-red-300">{emailError}</p>
                    </div>
                  )}

                  <button
                    type="submit"
                    disabled={isEmailCompleting || !emailCode.trim()}
                    className="w-full rounded-xl bg-[var(--color-accent-primary)] px-4 py-3 text-sm font-semibold text-white shadow-sm hover:bg-[var(--color-accent-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--color-bg-tertiary)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {isEmailCompleting ? 'Verifying...' : 'Verify and sign in'}
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setEmailStep('idle');
                      setEmailError(null);
                      setEmailCode('');
                    }}
                    disabled={isEmailCompleting}
                    className="w-full text-center text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors disabled:opacity-50"
                  >
                    Use a different email
                  </button>
                </form>
              ))}
          </div>
        )}

        {!isHosted && (
          <form onSubmit={handleSetupSubmit} className="space-y-5">
            <div className="space-y-4">
              <div>
                <label
                  htmlFor="setup-token"
                  className="block text-sm font-medium text-[var(--color-text-secondary)] mb-1.5"
                >
                  Setup Token
                </label>
                <input
                  id="setup-token"
                  type="text"
                  autoComplete="off"
                  placeholder="Paste your setup token here"
                  value={setupToken}
                  onChange={(e) => setSetupToken(e.target.value)}
                  disabled={isSubmitting}
                  className="w-full rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-3 py-2.5 text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent-primary)] disabled:opacity-50 disabled:cursor-not-allowed"
                />
              </div>

              <div>
                <label
                  htmlFor="display-name"
                  className="block text-sm font-medium text-[var(--color-text-secondary)] mb-1.5"
                >
                  Display Name{' '}
                  <span className="text-[var(--color-text-muted)] font-normal">
                    (optional)
                  </span>
                </label>
                <input
                  id="display-name"
                  type="text"
                  autoComplete="off"
                  placeholder="e.g. MacBook Pro, Work Laptop"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  disabled={isSubmitting}
                  className="w-full rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-3 py-2.5 text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent-primary)] disabled:opacity-50 disabled:cursor-not-allowed"
                />
              </div>
            </div>

            {setupError && (
              <div className="flex items-start gap-2.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2.5">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
                <p className="text-sm text-red-300">{setupError}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={isSubmitting || !setupToken.trim()}
              className="w-full rounded-xl bg-[var(--color-accent-primary)] px-4 py-3 text-sm font-semibold text-white shadow-sm hover:bg-[var(--color-accent-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--color-bg-tertiary)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {isSubmitting ? 'Setting up...' : 'Complete Setup'}
            </button>
          </form>
        )}

        {!isHosted && (
          <div className="flex items-start gap-3 rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-4 py-3">
            <Shield className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-accent-primary)]" />
            <div className="space-y-1">
              <p className="text-xs font-medium text-[var(--color-text-secondary)]">
                Why a form, not a URL?
              </p>
              <p className="text-xs text-[var(--color-text-muted)] leading-relaxed">
                Pasting the token into this form avoids leakage through browser
                history, Referer headers, access logs, and bookmarks. The token
                is sent in a POST body only.
              </p>
              <p className="text-xs text-[var(--color-text-muted)] leading-relaxed">
                Server logs remain sensitive — the startup token is printed
                there at first boot. Treat your logs as confidential.
              </p>
            </div>
          </div>
        )}

        <div className="border-t border-[var(--color-border-primary)] pt-6">
          <h2 className="text-lg font-semibold text-[var(--color-text-primary)] mb-1">
            Continue Enrollment
          </h2>
          <p className="text-sm text-[var(--color-text-muted)] mb-4">
            Have a pending enrollment from another browser? Complete it here.
          </p>

          <form onSubmit={handleEnrollSubmit} className="space-y-4">
            <div>
              <label
                htmlFor="enrollment-payload"
                className="block text-sm font-medium text-[var(--color-text-secondary)] mb-1.5"
              >
                Enrollment Link or Token
              </label>
              <input
                id="enrollment-payload"
                type="text"
                autoComplete="off"
                placeholder="Paste daemon-enroll://... or leave empty for manual entry"
                value={enrollmentPayload}
                onChange={(e) => setEnrollmentPayload(e.target.value)}
                disabled={isEnrolling}
                className="w-full rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-3 py-2.5 text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent-primary)] disabled:opacity-50 disabled:cursor-not-allowed"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label
                  htmlFor="enrollment-pending-id"
                  className="block text-sm font-medium text-[var(--color-text-secondary)] mb-1.5"
                >
                  Pending ID
                </label>
                <input
                  id="enrollment-pending-id"
                  type="text"
                  autoComplete="off"
                  placeholder="Pending ID"
                  value={enrollmentPendingId}
                  onChange={(e) => setEnrollmentPendingId(e.target.value)}
                  disabled={isEnrolling}
                  className="w-full rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-3 py-2.5 text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent-primary)] disabled:opacity-50 disabled:cursor-not-allowed"
                />
              </div>

              <div>
                <label
                  htmlFor="enrollment-code"
                  className="block text-sm font-medium text-[var(--color-text-secondary)] mb-1.5"
                >
                  Code
                </label>
                <input
                  id="enrollment-code"
                  type="text"
                  autoComplete="off"
                  placeholder="Code"
                  value={enrollmentCode}
                  onChange={(e) => setEnrollmentCode(e.target.value)}
                  disabled={isEnrolling}
                  className="w-full rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-3 py-2.5 text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent-primary)] disabled:opacity-50 disabled:cursor-not-allowed"
                />
              </div>
            </div>

            {enrollmentError && (
              <div className="flex items-start gap-2.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2.5">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
                <p className="text-sm text-red-300">{enrollmentError}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={isEnrolling}
              className="w-full rounded-xl bg-[var(--color-accent-primary)] px-4 py-3 text-sm font-semibold text-white shadow-sm hover:bg-[var(--color-accent-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--color-bg-tertiary)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {isEnrolling ? 'Completing enrollment...' : 'Complete Enrollment'}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}

function IdentityCard({
  icon,
  label,
  onClick,
  disabled,
  disabledReason,
}: {
  icon: React.ReactNode;
  label: string;
  onClick?: () => void;
  disabled?: boolean;
  disabledReason?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="w-full flex items-center gap-3 rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-4 py-3 text-left hover:border-[var(--color-border-secondary)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--color-bg-tertiary)] disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-[var(--color-bg-secondary)] disabled:hover:border-[var(--color-border-primary)] transition-colors"
    >
      <div className="flex-shrink-0 text-[var(--color-accent-primary)]">
        {icon}
      </div>
      <div className="flex-1">
        <p className="text-sm font-medium text-[var(--color-text-primary)]">
          {label}
        </p>
        {disabledReason && (
          <p className="text-xs text-[var(--color-text-muted)]">
            {disabledReason}
          </p>
        )}
      </div>
    </button>
  );
}

function DevicePersistenceChooser({
  devicePersistence,
  disabled,
  onChange,
}: {
  devicePersistence: 'private' | 'temporary';
  disabled: boolean;
  onChange: (value: 'private' | 'temporary') => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-start gap-2 rounded-lg border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-3 py-2">
        <Monitor className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-text-muted)]" />
        <div>
          <p className="text-xs font-medium text-[var(--color-text-secondary)]">
            Device
          </p>
          <p className="text-xs text-[var(--color-text-muted)]">
            Web Sign-In Device (this browser)
          </p>
        </div>
      </div>

      <p className="text-sm font-medium text-[var(--color-text-secondary)]">
        This device is:
      </p>
      <div className="flex gap-3">
        <label className="flex-1 flex items-center gap-2 rounded-lg border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-3 py-2.5 cursor-pointer hover:border-[var(--color-border-secondary)] transition-colors">
          <input
            type="radio"
            name="device-persistence"
            value="private"
            checked={devicePersistence === 'private'}
            onChange={() => onChange('private')}
            disabled={disabled}
            className="text-[var(--color-accent-primary)] focus:ring-[var(--color-accent-primary)]"
          />
          <div>
            <p className="text-sm font-medium text-[var(--color-text-primary)]">
              Private
            </p>
            <p className="text-xs text-[var(--color-text-muted)]">
              Stay signed in
            </p>
          </div>
        </label>
        <label className="flex-1 flex items-center gap-2 rounded-lg border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-3 py-2.5 cursor-pointer hover:border-[var(--color-border-secondary)] transition-colors">
          <input
            type="radio"
            name="device-persistence"
            value="temporary"
            checked={devicePersistence === 'temporary'}
            onChange={() => onChange('temporary')}
            disabled={disabled}
            className="text-[var(--color-accent-primary)] focus:ring-[var(--color-accent-primary)]"
          />
          <div>
            <p className="text-sm font-medium text-[var(--color-text-primary)]">
              Public
            </p>
            <p className="text-xs text-[var(--color-text-muted)]">
              Forget when I leave
            </p>
          </div>
        </label>
      </div>
    </div>
  );
}
