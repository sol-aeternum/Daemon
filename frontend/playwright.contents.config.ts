import { defineConfig } from '@playwright/test';
import base from './playwright.production.config';

// Dedicated production qualification, without changing other projects/ports.
const port = 3102;
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  ...base,
  projects: [
    {
      name: 'conversation-contents',
      testMatch: [
        'conversation-contents.spec.ts',
        'conversation-ergonomics.spec.ts',
      ],
      use: { ...base.use, baseURL },
    },
  ],
  webServer: {
    command: `npm run start -- -H 127.0.0.1 -p ${port}`,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
