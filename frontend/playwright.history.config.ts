import { existsSync } from 'node:fs';
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  testMatch: 'history-pagination.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: {
    baseURL: 'http://127.0.0.1:3173',
    serviceWorkers: 'block',
    trace: 'retain-on-failure',
    launchOptions: {
      // Chromium's singleton UNIX socket cannot fit the long managed TMPDIR.
      // Relative browser temp stays inside this registered checkout (npm cwd),
      // with no external scratch, symlink or lifecycle admission override.
      env: { TMPDIR: '.' },
      executablePath: existsSync('/usr/bin/chromium')
        ? '/usr/bin/chromium'
        : undefined,
    },
  },
  // Own a separate loopback development server; never reuse/stop a shared app.
  webServer: {
    command: 'node_modules/.bin/next dev -H 127.0.0.1 -p 3173 --webpack',
    url: 'http://127.0.0.1:3173',
    env: { NEXT_PUBLIC_API_URL: 'http://127.0.0.1:3173' },
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
