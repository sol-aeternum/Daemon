'use client';

import { Suspense } from 'react';

import AuthLanding from '../../components/AuthLanding';
import { getDeploymentMode } from '../../lib/deployment';

export default function SetupPage() {
  const mode = getDeploymentMode();
  return (
    <Suspense fallback={null}>
      <AuthLanding mode={mode} />
    </Suspense>
  );
}
