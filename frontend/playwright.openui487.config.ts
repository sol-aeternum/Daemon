import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  testMatch: 'openui487.spec.ts',
  use: { serviceWorkers: 'block' },
  projects: [
    {
      name: 'desktop',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1280, height: 800 },
      },
    },
    {
      name: 'narrow-android-viewport',
      use: { ...devices['Pixel 7'], viewport: { width: 320, height: 640 } },
    },
  ],
});
