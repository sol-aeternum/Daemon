'use client';

import AuthLanding from '../../components/AuthLanding';
import { getDeploymentMode } from '../../lib/deployment';

export default function SetupPage() {
  const mode = getDeploymentMode();
  return <AuthLanding mode={mode} />;
}
