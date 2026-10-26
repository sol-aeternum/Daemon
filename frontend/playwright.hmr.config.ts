import { defineConfig } from '@playwright/test';

require('./hmr-tests/protocol-harness.cjs').installLaunchRecorder();

export default defineConfig({
  testDir: './hmr-tests',
  testMatch: 'browser.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  outputDir: './.hmr-runs/playwright-results',
  use: { baseURL: 'http://127.0.0.1:3151', trace: 'retain-on-failure' },
  projects: [{ name: 'webpack-hmr-protocol' }],
});
