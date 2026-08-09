'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import AuthLanding from './AuthLanding';
import {
  fetchAuthConfig,
  getCachedAuthConfig,
  subscribeAuthConfig,
  type AuthConfig,
  type AuthConfigResult,
} from '../lib/auth-config';

export default function AuthPageContent() {
  const router = useRouter();
  const [runtimeConfig, setRuntimeConfig] = useState<AuthConfig | undefined>(
    () => getCachedAuthConfig(),
  );
  const [isLoadingConfig, setIsLoadingConfig] = useState(
    () => !getCachedAuthConfig(),
  );

  useEffect(() => {
    let mounted = true;

    function applyConfig(result: AuthConfigResult): void {
      if (!mounted) return;
      setIsLoadingConfig(false);
      setRuntimeConfig(
        result.status === 'resolved' ? result.config : undefined,
      );
    }

    const unsubscribe = subscribeAuthConfig(applyConfig);
    if (!runtimeConfig) {
      void fetchAuthConfig().then(applyConfig);
    }

    return () => {
      mounted = false;
      unsubscribe();
    };
  }, [runtimeConfig]);

  const redirectToSetup = !isLoadingConfig && runtimeConfig?.mode !== 'hosted';

  useEffect(() => {
    if (redirectToSetup) {
      router.replace('/setup');
    }
  }, [redirectToSetup, router]);

  if (redirectToSetup) {
    return null;
  }

  return (
    <AuthLanding
      mode="hosted"
      runtimeConfig={runtimeConfig}
      runtimeConfigLoading={isLoadingConfig}
    />
  );
}
