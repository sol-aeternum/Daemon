'use client';

import { Suspense, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import AuthLanding from '../../components/AuthLanding';
import {
  fetchAuthConfig,
  getCachedAuthConfig,
  subscribeAuthConfig,
  type AuthConfigResult,
} from '../../lib/auth-config';

function getInitialConfigResult(): AuthConfigResult | null {
  const cached = getCachedAuthConfig();
  return cached ? { status: 'resolved', config: cached } : null;
}

export default function SetupPage() {
  const router = useRouter();
  const [configResult, setConfigResult] = useState<AuthConfigResult | null>(
    getInitialConfigResult,
  );
  const [isLoadingConfig, setIsLoadingConfig] = useState(
    () => !getCachedAuthConfig(),
  );

  useEffect(() => {
    let mounted = true;
    const applyConfig = (result: AuthConfigResult) => {
      if (!mounted) return;
      setConfigResult(result);
      setIsLoadingConfig(false);
    };
    const unsubscribe = subscribeAuthConfig(applyConfig);

    if (!getCachedAuthConfig()) {
      void fetchAuthConfig().then(applyConfig);
    }

    return () => {
      mounted = false;
      unsubscribe();
    };
  }, []);

  useEffect(() => {
    if (isLoadingConfig) return;
    if (
      !configResult ||
      configResult.status === 'error' ||
      configResult.config.mode === 'hosted'
    ) {
      router.replace('/auth');
    }
  }, [configResult, isLoadingConfig, router]);

  if (
    isLoadingConfig ||
    !configResult ||
    configResult.status !== 'resolved' ||
    configResult.config.mode !== 'self_hosted'
  ) {
    return null;
  }

  return (
    <Suspense fallback={null}>
      <AuthLanding mode="self-hosted" />
    </Suspense>
  );
}
