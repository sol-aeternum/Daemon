import { defineConfig } from '@playwright/test';

// Build with NEXT_PUBLIC_API_URL=http://127.0.0.1:3103/daemon/ first. The local
// fixture server handles actual SW fetches; there are no page API interceptions.
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: {
    baseURL: 'http://127.0.0.1:3102',
    serviceWorkers: 'allow',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'sources-production', testMatch: 'sources*.spec.ts' }],
  webServer: {
    command: 'node e2e/sources-fixture-server.mjs',
    url: 'http://127.0.0.1:3102/landing',
    env: { NEXT_PUBLIC_API_URL: 'http://127.0.0.1:3103/daemon/' },
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
