'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { AlertCircle, RefreshCw } from 'lucide-react';
import AuthLanding from './AuthLanding';
import {
  fetchAuthConfig,
  getCachedAuthConfig,
  refreshAuthConfig,
  subscribeAuthConfig,
  type AuthConfig,
  type AuthConfigResult,
} from '../lib/auth-config';

function AuthConfigError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-[var(--color-bg-tertiary)] px-4 py-12">
      <div className="w-full max-w-md rounded-2xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-6 text-center shadow-sm">
        <AlertCircle className="mx-auto h-8 w-8 text-[var(--color-status-error)]" />
        <h1 className="mt-4 text-xl font-semibold text-[var(--color-text-primary)]">
          Sign-in is temporarily unavailable
        </h1>
        <p className="mt-2 text-sm text-[var(--color-text-muted)]">
          We could not load the sign-in options. Check your connection and try
          again.
        </p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-5 inline-flex items-center gap-2 rounded-xl bg-[var(--color-accent-primary)] px-4 py-2.5 text-sm font-semibold text-[var(--color-text-on-accent)] transition-colors hover:bg-[var(--color-accent-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--color-bg-tertiary)]"
        >
          <RefreshCw className="h-4 w-4" />
          Try again
        </button>
      </div>
    </div>
  );
}

export default function AuthPageContent() {
  const router = useRouter();
  const cachedConfig = getCachedAuthConfig();
  const [configResult, setConfigResult] = useState<AuthConfigResult | null>(
    () => (cachedConfig ? { status: 'resolved', config: cachedConfig } : null),
  );
  const [isLoadingConfig, setIsLoadingConfig] = useState(!cachedConfig);

  const applyConfig = useCallback((result: AuthConfigResult) => {
    setConfigResult(result);
    setIsLoadingConfig(false);
  }, []);

  const loadConfig = useCallback(
    async (forceRefresh: boolean) => {
      setIsLoadingConfig(true);
      const result = forceRefresh
        ? await refreshAuthConfig()
        : await fetchAuthConfig();
      applyConfig(result);
    },
    [applyConfig],
  );

  useEffect(() => {
    const unsubscribe = subscribeAuthConfig(applyConfig);
    if (!getCachedAuthConfig()) {
      void fetchAuthConfig().then(applyConfig);
    }
    return unsubscribe;
  }, [applyConfig]);

  const runtimeConfig: AuthConfig | undefined =
    configResult?.status === 'resolved' ? configResult.config : undefined;
  const shouldRedirectToSetup =
    !isLoadingConfig &&
    configResult?.status === 'resolved' &&
    configResult.config.mode === 'self_hosted';

  useEffect(() => {
    if (shouldRedirectToSetup) {
      router.replace('/setup');
    }
  }, [router, shouldRedirectToSetup]);

  if (isLoadingConfig) {
    return <AuthLanding mode="hosted" runtimeConfigLoading />;
  }

  if (!configResult || configResult.status === 'error') {
    return (
      <AuthConfigError
        onRetry={() => {
          void loadConfig(true);
        }}
      />
    );
  }

  if (shouldRedirectToSetup) {
    return null;
  }

  return <AuthLanding mode="hosted" runtimeConfig={runtimeConfig} />;
}
