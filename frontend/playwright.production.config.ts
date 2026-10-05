import { defineConfig } from '@playwright/test';
import base from './playwright.config';

// Run after `npm run build`; preserves production CSP and nonce handling.
export default defineConfig({
  ...base,
  // API fixtures must intercept every request; service-worker fetches bypass
  // Playwright page routes. Offline/PWA behavior is a separate test surface.
  use: { ...base.use, serviceWorkers: 'block' },
  projects: [
    {
      name: 'midnight',
      testMatch: 'midnight.spec.ts',
      use: { baseURL: 'http://127.0.0.1:3101' },
    },
    {
      name: 'contextual-home',
      testMatch: 'contextual-home.spec.ts',
      use: { baseURL: 'http://127.0.0.1:3101' },
    },
  ],
  webServer: {
    command: 'npm run start -- -H 127.0.0.1 -p 3101',
    url: 'http://127.0.0.1:3101',
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
