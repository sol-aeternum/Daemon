import {
  chromium,
  expect,
  test,
  type Browser,
  type Page,
} from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { gzipSync } from 'node:zlib';
import { checkBenchmarkBrowser } from '../benchmark-browser';
import { conversation, mockApi } from '../conversation-fixture';
import { historyFixtureId } from '../history-fixtures';
import { withoutServiceWorker } from '../online-ui-fixture';
import { screenshotFontLaunchOptions } from '../screenshot-fonts.cjs';

declare global {
  interface Window {
    rail765Writes?: () => number;
  }
}

const railSelector = 'nav[aria-label="Conversation contents"] ol';
const transcriptSelector = 'main[aria-label="Conversation messages"]';

async function prepare(page: Page) {
  await mockApi(page);
  await page.addInitScript(withoutServiceWorker);
  await page.goto(`/?id=${conversation.id}`);
  expect(await page.evaluate(() => 'serviceWorker' in navigator)).toBe(false);
  const main = page.locator(transcriptSelector);
  await expect(main.locator('article')).toHaveCount(100);
  await expect(
    main.getByRole('button', { name: 'Show more', exact: true }),
  ).toHaveCount(85);
  const old = main.locator(`[data-message-id="${historyFixtureId(2)}"]`);
  await old.scrollIntoViewIfNeeded();
  await old.getByRole('button', { name: 'Show more' }).click();
  await expect(old.getByText('Full content 2.')).toBeVisible();
  await old.getByRole('button', { name: 'Show less' }).click();
  await expect(old.getByText('Full content 2.')).toBeHidden();
  await expect(page.locator(railSelector)).toBeVisible();
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
}

// Shadow one instance only. In both arms the effect still reads the button
// bounds, updates aria-current, and attempts the write.
function installProbe(page: Page, suppress: boolean) {
  return page.locator(railSelector).evaluate((rail, skip) => {
    const native = Object.getOwnPropertyDescriptor(
      Element.prototype,
      'scrollTop',
    );
    if (!native?.get || !native.set || Object.hasOwn(rail, 'scrollTop')) {
      throw new Error('Cannot isolate the rail scrollTop setter');
    }
    let writes = 0;
    const { get, set } = native;
    Object.defineProperty(rail, 'scrollTop', {
      get: () => get.call(rail),
      set(value: number) {
        writes++;
        if (!skip) set.call(rail, value);
      },
    });
    Object.assign(window, { rail765Writes: () => writes });
  }, suppress);
}

// Only the target emits trace marks and timestamps every callback. Fresh
// contexts on separate browsers keep background tabs out of the comparison.
function scroll(page: Page, target: boolean, barrier: number) {
  return page.evaluate(
    async ({ selector, target, barrier }) => {
      const main = document.querySelector<HTMLElement>(selector)!;
      const now = () => performance.timeOrigin + performance.now();
      await new Promise<void>((resolve) =>
        setTimeout(resolve, Math.max(0, barrier - now())),
      );
      const firstTop = main.scrollTop;
      const visibility = document.visibilityState;
      const writesBefore = target ? window.rail765Writes!() : 0;
      const started = performance.now();
      const epoch = performance.timeOrigin + started;
      const times: number[] = [];
      let frames = 0;
      if (target) performance.mark('765-start');
      const elapsed = await new Promise<number>((resolve) => {
        function step(t: number) {
          frames++;
          if (target) times.push(t);
          main.scrollTop =
            ((t - started) / 1000) * (main.scrollHeight - main.clientHeight);
          if (t - started >= 1000) {
            if (target) performance.mark('765-end');
            resolve(t - started);
          } else requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
      });
      return {
        measuredWrites: target ? window.rail765Writes!() - writesBefore : null,
        epoch,
        endEpoch: now(),
        frames,
        elapsed,
        times,
        firstTop,
        top: main.scrollTop,
        range: main.scrollHeight - main.clientHeight,
        visible:
          visibility === 'visible' && document.visibilityState === 'visible',
      };
    },
    { selector: transcriptSelector, target, barrier },
  );
}

async function capture(
  browser: Browser,
  pages: Page[],
  suppress: boolean,
  dir: string,
) {
  const [target] = pages;
  await installProbe(target, suppress);
  const rail = target.locator(railSelector);
  const geometry = () =>
    rail.evaluate((el) => ({
      top: el.scrollTop,
      range: el.scrollHeight - el.clientHeight,
      active: el.querySelector<HTMLElement>('[aria-current="location"]')
        ?.dataset.contentsMessageId,
      writes: window.rail765Writes!(),
    }));
  const before = await geometry();
  const root = await browser.newBrowserCDPSession();
  const done = new Promise<{ stream?: string; dataLossOccurred: boolean }>(
    (resolve) => root.once('Tracing.tracingComplete', resolve),
  );
  await root.send('Tracing.start', {
    categories: 'devtools.timeline,blink.user_timing,cc',
    transferMode: 'ReturnAsStream',
  });
  const barrier = Date.now() + 750;
  const results = await Promise.all(
    pages.map((p, i) => scroll(p, i === 0, barrier)),
  );
  await root.send('Tracing.end');
  const { stream, dataLossOccurred } = await done;
  if (!stream) throw new Error('Chromium did not return a trace stream');
  const chunks: Buffer[] = [];
  for (;;) {
    const chunk = await root.send('IO.read', { handle: stream });
    chunks.push(
      Buffer.from(chunk.data, chunk.base64Encoded ? 'base64' : 'utf8'),
    );
    if (chunk.eof) break;
  }
  await root.send('IO.close', { handle: stream });
  await root.detach();
  await mkdir(dir, { recursive: true });
  await writeFile(
    join(dir, 'renderer-trace.json.gz'),
    gzipSync(Buffer.concat(chunks)),
  );
  // The rail effect may trail the last measured rAF. Observe settled state
  // outside the marks; the final prompt to cross the reading line is not
  // necessarily the last message on the page.
  await target.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  const after = await geometry();
  const overlapMs =
    Math.min(...results.map((x) => x.endEpoch)) -
    Math.max(...results.map((x) => x.epoch));
  const { times, elapsed, frames } = results[0];
  const gaps = times
    .slice(1)
    .map((t, i) => t - times[i])
    .sort((a, b) => a - b);
  const report = {
    browser: browser.version(),
    suppress,
    before,
    after,
    results,
    overlapMs,
    dataLossOccurred,
    fps: (frames * 1000) / elapsed,
    p95GapMs: gaps[Math.ceil(0.95 * gaps.length) - 1],
  };
  await writeFile(join(dir, 'window.json'), JSON.stringify(report, null, 2));
  expect(dataLossOccurred).toBe(false);
  expect(results[0].measuredWrites).toBeGreaterThan(0);
  expect(after.active).toBeTruthy();
  // A sliver of overlap isn't a four-browser scroll window.
  expect(overlapMs).toBeGreaterThanOrEqual(0.9 * elapsed);
  for (const result of results) {
    expect(result.visible).toBe(true);
    expect(result.range).toBeGreaterThan(200);
    expect(Math.abs(result.top - result.range)).toBeLessThanOrEqual(1);
  }
  if (suppress) expect(after.top).toBe(before.top);
  else expect(after.top).toBeGreaterThan(before.top);
  return {
    initial: before.active,
    final: after.active,
    railRange: before.range,
    railStart: before.top,
    range: results[0].range,
    start: results[0].firstTop,
  };
}

test('paired #765 rail capture (manual)', async ({ baseURL }, info) => {
  const browsers: Browser[] = [];
  try {
    for (let i = 0; i < 4; i++) {
      const browser = await chromium.launch(screenshotFontLaunchOptions());
      browsers.push(browser);
      checkBenchmarkBrowser(browser);
    }
    let reference: Awaited<ReturnType<typeof capture>> | undefined;
    for (const [i, arm] of ['A', 'B', 'B', 'A'].entries()) {
      const pages = await Promise.all(
        browsers.map((b) =>
          b.newPage({
            baseURL,
            serviceWorkers: 'block',
            viewport: { width: 1440, height: 1000 },
          }),
        ),
      );
      try {
        await Promise.all(pages.map(prepare));
        const observed = await capture(
          browsers[0],
          pages,
          arm === 'B',
          info.outputPath(`window-${i + 1}-${arm}`),
        );
        if (reference) expect(observed).toEqual(reference);
        else reference = observed;
      } finally {
        await Promise.all(pages.map((p) => p.close()));
      }
    }
  } finally {
    await Promise.all(browsers.map((b) => b.close()));
  }
});
