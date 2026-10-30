import { defineConfig } from '@playwright/test';
import base from './playwright.config';

// Combined qualification runs this phase BEFORE the production build/server.
export default defineConfig({
  ...base,
  projects: (base.projects ?? [])
    .filter((project) => project.name === 'development-runtime')
    .map((project) => ({
      ...project,
      use: { ...project.use, baseURL: 'http://127.0.0.1:3181' },
    })),
  webServer: {
    command: 'node_modules/.bin/next dev -H 127.0.0.1 -p 3181 --webpack',
    url: 'http://127.0.0.1:3181',
    env: { NEXT_PUBLIC_API_URL: 'http://127.0.0.1:3181' },
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
