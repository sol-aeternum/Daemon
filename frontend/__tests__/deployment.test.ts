import { afterEach, describe, expect, it } from 'vitest';

import {
  getDeploymentMode,
  getEmailEnabled,
  getGoogleClientId,
} from '../lib/deployment';

describe('deployment config helpers', () => {
  afterEach(() => {
    delete process.env.NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE;
    delete process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;
    delete process.env.NEXT_PUBLIC_EMAIL_ENABLED;
  });

  it('defaults to self-hosted when no deployment mode is configured', () => {
    expect(getDeploymentMode()).toBe('self-hosted');
  });

  it('returns hosted when NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE is hosted', () => {
    process.env.NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE = 'hosted';
    expect(getDeploymentMode()).toBe('hosted');
  });

  it('suppresses Google client ID outside hosted mode', () => {
    process.env.NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE = 'self-hosted';
    process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID = 'public-client-id';

    expect(getGoogleClientId()).toBe('');
  });

  it('returns trimmed Google client ID in hosted mode', () => {
    process.env.NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE = 'hosted';
    process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID = ' public-client-id ';

    expect(getGoogleClientId()).toBe('public-client-id');
  });

  it('defaults hosted email to enabled when unset', () => {
    process.env.NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE = 'hosted';
    expect(getEmailEnabled()).toBe(true);
  });

  it('returns false for hosted email when NEXT_PUBLIC_EMAIL_ENABLED=false', () => {
    process.env.NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE = 'hosted';
    process.env.NEXT_PUBLIC_EMAIL_ENABLED = 'false';
    expect(getEmailEnabled()).toBe(false);
  });
});
