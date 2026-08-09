import { Suspense } from 'react';

import AuthPageContent from '../../components/AuthPageContent';

export default function AuthPage() {
  return (
    <Suspense fallback={null}>
      <AuthPageContent />
    </Suspense>
  );
}
