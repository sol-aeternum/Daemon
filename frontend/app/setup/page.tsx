'use client';

import { Suspense } from 'react';

import AuthLanding from '../../components/AuthLanding';

export default function SetupPage() {
  return (
    <Suspense fallback={null}>
      <AuthLanding mode="self-hosted" />
    </Suspense>
  );
}
