import { defineConfig } from '@playwright/test';
import base from './playwright.config';

// Complete gate: npm run test:browser:qualification (dev -> build -> production).
// Standalone production diagnostics require a fresh build with API origin3181.
// Main-app projects use production CSP/nonce handling without DevTools; the
// separate CSP-preview fixture still owns its original isolated Next server.
export default defineConfig({
  ...base,
  // API fixtures must intercept every request; service-worker fetches bypass
  // Playwright page routes. Offline/PWA behavior is a separate test surface.
  use: {
    ...base.use,
    baseURL: 'http://127.0.0.1:3180',
    serviceWorkers: 'block',
  },
  projects: [
    ...(base.projects ?? [])
      .filter((project) => project.name !== 'development-runtime')
      .map((project) => ({
        ...project,
        use: {
          ...project.use,
          baseURL:
            project.name === 'csp-previews'
              ? 'http://127.0.0.1:3180'
              : 'http://127.0.0.1:3181',
        },
      })),
    {
      name: 'midnight',
      testMatch: 'midnight.spec.ts',
      use: { baseURL: 'http://127.0.0.1:3181' },
    },
    {
      name: 'contextual-home',
      testMatch: 'contextual-home.spec.ts',
      use: { baseURL: 'http://127.0.0.1:3181' },
    },
  ],
  webServer: [
    {
      command:
        'node_modules/.bin/next dev e2e/csp-preview-app -H 127.0.0.1 -p 3180 --webpack',
      url: 'http://127.0.0.1:3180',
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: 'npm run start -- -H 127.0.0.1 -p 3181',
      url: 'http://127.0.0.1:3181',
      env: { NEXT_PUBLIC_API_URL: 'http://127.0.0.1:3181' },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
