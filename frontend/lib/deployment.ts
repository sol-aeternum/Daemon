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

export function getDeploymentMode(): DeploymentMode {
  const env =
    typeof process !== 'undefined'
      ? process.env.NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE
      : undefined;
  return env === 'hosted' ? 'hosted' : 'self-hosted';
}
