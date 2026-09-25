'use client';

import { useState, useEffect, useRef } from 'react';
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
import type { AuthConfig } from '../lib/auth-config';
import {
  Sparkles,
  Shield,
  AlertCircle,
  Loader2,
  RefreshCw,
} from 'lucide-react';

export type DeploymentMode = 'hosted' | 'self-hosted';

interface GoogleCredentialResponse {
  credential?: string;
}

interface GoogleInitializeConfig {
  client_id: string;
  nonce: string;
  callback: (response: GoogleCredentialResponse) => void;
  auto_select?: boolean;
  cancel_on_tap_outside?: boolean;
}

interface GoogleRenderButtonOptions {
  type: 'standard';
  theme: 'outline';
  size: 'large';
  text: 'continue_with';
  shape: 'pill';
  width: number;
  logo_alignment: 'left';
}

interface GoogleIdentityServices {
  accounts: {
    id: {
      initialize: (config: GoogleInitializeConfig) => void;
      renderButton: (
        parent: HTMLElement,
        options: GoogleRenderButtonOptions,
      ) => void;
      cancel?: () => void;
    };
  };
}

declare global {
  interface Window {
    google?: GoogleIdentityServices;
  }
}

const GOOGLE_GIS_SCRIPT_SRC = 'https://accounts.google.com/gsi/client';
const GOOGLE_SCRIPT_LOAD_TIMEOUT_MS = 10_000;
const GOOGLE_CHALLENGE_TIMEOUT_MS = 10_000;
const GOOGLE_COMPLETION_TIMEOUT_MS = 15_000;
const GOOGLE_UNAVAILABLE_MESSAGE =
  'Google sign-in is temporarily unavailable. Please try again.';
const GOOGLE_CANCELLED_MESSAGE =
  'Google sign-in was cancelled. Please try again.';

let googleScriptPromise: Promise<GoogleIdentityServices> | null = null;

function withTimeout<T>(
  promise: Promise<T>,
  timeoutMs: number,
  message: string,
  onTimeout?: () => void,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      onTimeout?.();
      reject(new Error(message));
    }, timeoutMs);

    promise.then(
      (value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

function loadGoogleIdentityServices(): Promise<GoogleIdentityServices> {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return Promise.reject(new Error('Google sign-in is unavailable here.'));
  }
  if (window.google?.accounts?.id) {
    return Promise.resolve(window.google);
  }
  if (googleScriptPromise) {
    return googleScriptPromise;
  }

  const promise = new Promise<GoogleIdentityServices>((resolve, reject) => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const existingScript = document.querySelector<HTMLScriptElement>(
      `script[src="${GOOGLE_GIS_SCRIPT_SRC}"]`,
    );
    const script = existingScript ?? document.createElement('script');

    const cleanup = () => {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      script.removeEventListener('load', handleLoad);
      script.removeEventListener('error', handleError);
    };

    const fail = (message: string) => {
      if (settled) return;
      settled = true;
      cleanup();
      // A failed external script cannot be reused. Removing it lets the retry
      // action install a fresh script instead of waiting forever on stale load
      // listeners.
      script.remove();
      reject(new Error(message));
    };

    const finish = () => {
      if (settled) return;
      if (window.google?.accounts?.id) {
        settled = true;
        cleanup();
        resolve(window.google);
      } else {
        fail('Google sign-in did not finish loading.');
      }
    };

    function handleLoad() {
      finish();
    }

    function handleError() {
      fail('Google sign-in failed to load.');
    }

    script.addEventListener('load', handleLoad);
    script.addEventListener('error', handleError);
    timer = setTimeout(
      () => fail('Google sign-in timed out while loading.'),
      GOOGLE_SCRIPT_LOAD_TIMEOUT_MS,
    );

    if (!existingScript) {
      script.src = GOOGLE_GIS_SCRIPT_SRC;
      // GIS copies currentScript.nonce onto its injected button stylesheet.
      // Reuse the document nonce rather than weakening the style CSP.
      const nonce = document.querySelector<HTMLMetaElement>(
        'meta[name="csp-nonce"]',
      )?.content;
      if (nonce) script.nonce = nonce;
      script.async = true;
      script.defer = true;
      document.head.appendChild(script);
    }
  });

  googleScriptPromise = promise;
  void promise.then(
    () => {
      if (googleScriptPromise === promise) {
        googleScriptPromise = null;
      }
    },
    () => {
      if (googleScriptPromise === promise) {
        googleScriptPromise = null;
      }
    },
  );
  return promise;
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
  const routerPushRef = useRef(router.push);
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
  const [googleStatus, setGoogleStatus] = useState<
    'idle' | 'loading' | 'ready' | 'completing' | 'error'
  >('idle');
  const [googleRetryToken, setGoogleRetryToken] = useState(0);
  const googleButtonHostRef = useRef<HTMLDivElement>(null);
  const googleGenerationRef = useRef(0);
  const inviteToken =
    searchParams.get('invite_token')?.trim() ||
    searchParams.get('invite')?.trim() ||
    undefined;
  const isHosted = mode === 'hosted';
  const googleEnabled = isHosted && runtimeConfig?.google.enabled === true;
  const googleClientId = googleEnabled
    ? (runtimeConfig?.google.clientId.trim() ?? '')
    : '';
  const emailEnabled = isHosted ? runtimeConfig?.email.enabled === true : false;

  useEffect(() => {
    routerPushRef.current = router.push;
  }, [router]);

  useEffect(() => {
    let cancelled = false;

    async function checkAuth() {
      const result = await refreshAccessToken().catch(() => null);
      if (!cancelled && result?.success) {
        routerPushRef.current('/');
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
  }, []);

  useEffect(() => {
    if (!isHosted || !googleClientId || isChecking || runtimeConfigLoading) {
      googleGenerationRef.current += 1;
      googleButtonHostRef.current?.replaceChildren();
      setGoogleStatus('idle');
      setGoogleError(null);
      return;
    }

    const generation = ++googleGenerationRef.current;
    const buttonHost = googleButtonHostRef.current;
    let cancelled = false;
    let completionStarted = false;
    let loadedGoogle: GoogleIdentityServices | null = null;
    const completionController = new AbortController();

    setGoogleStatus('loading');
    setGoogleError(null);
    googleButtonHostRef.current?.replaceChildren();

    async function prepareGoogleButton() {
      try {
        // Load the official client before asking the backend for a nonce. A
        // blocked script must not leave an apparently usable button that can
        // never complete, and it avoids creating an unused server challenge.
        const google = await withTimeout(
          loadGoogleIdentityServices(),
          GOOGLE_SCRIPT_LOAD_TIMEOUT_MS,
          'Google sign-in timed out while loading.',
        );
        loadedGoogle = google;
        if (cancelled || generation !== googleGenerationRef.current) return;

        const startResult = await withTimeout(
          startGoogleSignIn(),
          GOOGLE_CHALLENGE_TIMEOUT_MS,
          'Google sign-in challenge timed out.',
        );
        if (cancelled || generation !== googleGenerationRef.current) return;

        if (
          !startResult.success ||
          !startResult.challengeId ||
          !startResult.nonce
        ) {
          setGoogleStatus('error');
          setGoogleError(GOOGLE_UNAVAILABLE_MESSAGE);
          return;
        }

        const challengeId = startResult.challengeId;
        const nonce = startResult.nonce;
        const callback = (response: GoogleCredentialResponse) => {
          // Google can dispatch a callback after a retry or after the user
          // navigates away. Only the challenge that owns the currently rendered
          // button may complete a session.
          if (
            cancelled ||
            generation !== googleGenerationRef.current ||
            completionStarted
          ) {
            return;
          }

          const credential = response?.credential;
          if (!credential) {
            googleButtonHostRef.current?.replaceChildren();
            setGoogleStatus('error');
            setGoogleError(GOOGLE_CANCELLED_MESSAGE);
            return;
          }

          completionStarted = true;
          googleButtonHostRef.current?.replaceChildren();
          setGoogleStatus('completing');
          void withTimeout(
            completeGoogleSignIn(
              challengeId,
              nonce,
              credential,
              'private',
              inviteToken,
              completionController.signal,
            ),
            GOOGLE_COMPLETION_TIMEOUT_MS,
            'Google sign-in completion timed out.',
            () => completionController.abort(),
          )
            .then((result) => {
              if (cancelled || generation !== googleGenerationRef.current) {
                return;
              }
              if (result.success) {
                routerPushRef.current('/');
                return;
              }
              setGoogleStatus('error');
              setGoogleError(GOOGLE_UNAVAILABLE_MESSAGE);
            })
            .catch(() => {
              if (cancelled || generation !== googleGenerationRef.current) {
                return;
              }
              setGoogleStatus('error');
              setGoogleError(GOOGLE_UNAVAILABLE_MESSAGE);
            });
        };

        google.accounts.id.initialize({
          client_id: googleClientId,
          nonce,
          callback,
          // The rendered button is an explicit user action. Do not enable One
          // Tap or auto-selection as a prerequisite for hosted login.
          auto_select: false,
          cancel_on_tap_outside: false,
        });

        const host = buttonHost;
        if (!host) {
          throw new Error('Google sign-in button host is unavailable.');
        }
        host.replaceChildren();
        google.accounts.id.renderButton(host, {
          type: 'standard',
          theme: 'outline',
          size: 'large',
          text: 'continue_with',
          shape: 'pill',
          width: Math.min(400, host.clientWidth || 400),
          logo_alignment: 'left',
        });
        if (cancelled || generation !== googleGenerationRef.current) return;
        setGoogleStatus('ready');
      } catch {
        if (cancelled || generation !== googleGenerationRef.current) return;
        setGoogleStatus('error');
        setGoogleError(GOOGLE_UNAVAILABLE_MESSAGE);
      }
    }

    void prepareGoogleButton();

    return () => {
      cancelled = true;
      completionController.abort();
      if (generation === googleGenerationRef.current) {
        googleGenerationRef.current += 1;
      }
      buttonHost?.replaceChildren();
      loadedGoogle?.accounts.id.cancel?.();
    };
  }, [
    googleClientId,
    googleRetryToken,
    inviteToken,
    isChecking,
    isHosted,
    runtimeConfigLoading,
  ]);

  function handleGoogleRetry() {
    setGoogleError(null);
    setGoogleRetryToken((value) => value + 1);
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
        routerPushRef.current('/');
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
        routerPushRef.current('/');
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
        'private',
        inviteToken,
      );
      if (result.success) {
        routerPushRef.current('/');
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
                className="w-6 h-6 text-[var(--color-text-on-accent)] animate-spin"
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

  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-[var(--color-bg-tertiary)] px-4 py-12">
      <div className="w-full max-w-md space-y-8">
        <div className="flex flex-col items-center text-center space-y-4">
          <div className="relative">
            <div className="absolute inset-0 bg-[var(--color-accent-primary)] blur-xl opacity-30 rounded-full" />
            <div className="relative w-14 h-14 rounded-2xl bg-gradient-to-br from-[var(--color-accent-primary)] to-[var(--color-accent-hover)] flex items-center justify-center shadow-lg">
              <Sparkles className="w-7 h-7 text-[var(--color-text-on-accent)]" />
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
          <div className="space-y-4">
            {runtimeConfigLoading ? (
              <div
                role="status"
                className="flex items-center justify-center gap-2 rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-4 py-3 text-sm text-[var(--color-text-secondary)]"
              >
                <Loader2 className="h-4 w-4 animate-spin text-[var(--color-accent-primary)]" />
                Loading sign-in providers...
              </div>
            ) : !googleClientId ? (
              <div
                role="alert"
                className="flex items-start gap-2.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2.5"
              >
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
                <p className="text-sm text-red-300">
                  Google sign-in is temporarily unavailable. Please try again.
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="flex min-h-11 items-center justify-center">
                  <div
                    ref={googleButtonHostRef}
                    data-testid="google-signin-button"
                    aria-label="Continue with Google"
                    aria-busy={
                      googleStatus === 'loading' ||
                      googleStatus === 'completing'
                    }
                    className="min-h-11 w-full max-w-sm"
                    style={{ colorScheme: 'light' }}
                  />
                </div>

                {googleStatus === 'loading' && (
                  <p
                    role="status"
                    className="text-center text-sm text-[var(--color-text-muted)]"
                  >
                    Preparing Google sign-in...
                  </p>
                )}
                {googleStatus === 'completing' && (
                  <p
                    role="status"
                    className="text-center text-sm text-[var(--color-text-muted)]"
                  >
                    Finishing sign-in...
                  </p>
                )}
                {googleStatus === 'error' && (
                  <div
                    role="alert"
                    className="flex items-start gap-2.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2.5"
                  >
                    <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
                    <div className="flex-1 space-y-2">
                      <p className="text-sm text-red-300">
                        {googleError || GOOGLE_UNAVAILABLE_MESSAGE}
                      </p>
                      <button
                        type="button"
                        onClick={handleGoogleRetry}
                        className="inline-flex items-center gap-1.5 text-sm font-medium text-red-200 hover:text-[var(--color-text-primary)] focus:outline-none focus:ring-2 focus:ring-red-300/50"
                      >
                        <RefreshCw className="h-3.5 w-3.5" />
                        Try again
                      </button>
                    </div>
                  </div>
                )}
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
                    className="w-full rounded-xl bg-[var(--color-accent-primary)] px-4 py-3 text-sm font-semibold text-[var(--color-text-on-accent)] shadow-sm hover:bg-[var(--color-accent-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--color-bg-tertiary)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
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
                    className="w-full rounded-xl bg-[var(--color-accent-primary)] px-4 py-3 text-sm font-semibold text-[var(--color-text-on-accent)] shadow-sm hover:bg-[var(--color-accent-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--color-bg-tertiary)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
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
              className="w-full rounded-xl bg-[var(--color-accent-primary)] px-4 py-3 text-sm font-semibold text-[var(--color-text-on-accent)] shadow-sm hover:bg-[var(--color-accent-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--color-bg-tertiary)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
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

        {!isHosted && (
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
                className="w-full rounded-xl bg-[var(--color-accent-primary)] px-4 py-3 text-sm font-semibold text-[var(--color-text-on-accent)] shadow-sm hover:bg-[var(--color-accent-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--color-bg-tertiary)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {isEnrolling
                  ? 'Completing enrollment...'
                  : 'Complete Enrollment'}
              </button>
            </form>
          </div>
        )}
      </div>
    </div>
  );
}
