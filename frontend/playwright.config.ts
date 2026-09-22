import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: 'http://127.0.0.1:3100',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'csp-previews', testMatch: 'csp-previews.spec.ts' },
    {
      name: 'design-tokens',
      testMatch: 'design-tokens.spec.ts',
      use: { baseURL: 'http://127.0.0.1:3101' },
    },
  ],
  webServer: [
    {
      command:
        'node_modules/.bin/next dev e2e/csp-preview-app -H 127.0.0.1 -p 3100 --webpack',
      url: 'http://127.0.0.1:3100',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: 'node_modules/.bin/next dev -H 127.0.0.1 -p 3101 --webpack',
      url: 'http://127.0.0.1:3101',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
