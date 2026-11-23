import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import type { Browser } from '@playwright/test';

// A cache directory named for the right revision can contain the wrong binary.
// Compare the browser that will do the measurement, not a separate CLI launch.
export function checkBenchmarkBrowser(browser: Browser) {
  const corePackage = require.resolve('playwright-core/package.json');
  const core = JSON.parse(readFileSync(corePackage, 'utf8'));
  const lock = JSON.parse(
    readFileSync(join(__dirname, '../package-lock.json'), 'utf8'),
  );
  if (core.version !== lock.packages['node_modules/playwright-core'].version) {
    throw new Error(
      'Benchmark requires the locked playwright-core; run npm ci',
    );
  }
  const manifest = JSON.parse(
    readFileSync(join(dirname(corePackage), 'browsers.json'), 'utf8'),
  );
  const pin = manifest.browsers.find(
    (entry: { name: string }) => entry.name === 'chromium-headless-shell',
  );
  if (!pin?.browserVersion) throw new Error('Missing Chromium benchmark pin');
  const actual = browser.version();
  console.info(
    `Scroll benchmark: Chromium ${actual}; expected ${pin.browserVersion} (Playwright ${core.version}, revision ${pin.revision})`,
  );
  if (
    browser.browserType().name() !== 'chromium' ||
    actual !== pin.browserVersion
  ) {
    throw new Error(
      `Scroll benchmark requires pinned Chromium ${pin.browserVersion}; got ${browser.browserType().name()} ${actual}. Check the Playwright browser cache.`,
    );
  }
}
