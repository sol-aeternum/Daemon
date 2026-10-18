import { defineConfig } from '@playwright/test';

export default defineConfig({
  // Own child pipes instead of webServer's default process-group SIGKILL.
  globalSetup: './e2e/cache-fixture-setup.mjs',
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: {
    baseURL: 'http://127.0.0.1:3142',
    serviceWorkers: 'block',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'http-cache-isolation',
      testMatch: ['browser-cache.spec.ts', 'p02-cookie-coordination.spec.ts'],
    },
  ],
});
