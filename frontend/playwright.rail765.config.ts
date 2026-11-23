import { defineConfig } from '@playwright/test';
import { screenshotFontLaunchOptions } from './e2e/screenshot-fonts.cjs';

// Manual-only #765 diagnostic. Not referenced by playwright.config.ts, so the
// normal test:browser / qualification projects never collect this spec.
// Requires a production build: `npm run build` and a free 3181.
const port = 3181;
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './e2e/manual',
  testMatch: 'rail765-capture.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 600_000,
  expect: { timeout: 15_000 },
  reporter: [['list']],
  use: {
    baseURL,
    serviceWorkers: 'block',
    trace: 'off',
    video: 'off',
    screenshot: 'off',
    launchOptions: screenshotFontLaunchOptions(),
  },
  webServer: {
    command: `npm run start -- -H 127.0.0.1 -p ${port}`,
    url: baseURL,
    env: { NEXT_PUBLIC_API_URL: baseURL },
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
