/**
 * Deployment mode detection for the Daemon frontend.
 *
 * Hosted mode   -> identity-first landing (Google / email sign-in primary).
 * Self-hosted   -> setup-first landing (setup token primary).
 *
 * Defaults to "self-hosted" when no env var is set so that a fresh clone
 * never accidentally exposes an open sign-in surface.
 */

export type DeploymentMode = 'hosted' | 'self-hosted';

type RuntimeProcessEnv = Record<string, string | undefined>;

function getRuntimeEnv(): RuntimeProcessEnv | undefined {
  if (typeof globalThis === 'undefined') {
    return undefined;
  }
  const runtime = globalThis as typeof globalThis & {
    process?: { env?: RuntimeProcessEnv };
  };
  return runtime.process?.env;
}

export function getDeploymentMode(): DeploymentMode {
  const env = getRuntimeEnv()?.NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE;
  return env === 'hosted' ? 'hosted' : 'self-hosted';
}

export function getGoogleClientId(mode?: DeploymentMode): string {
  if ((mode ?? getDeploymentMode()) !== 'hosted') {
    return '';
  }
  return getRuntimeEnv()?.NEXT_PUBLIC_GOOGLE_CLIENT_ID?.trim() ?? '';
}
