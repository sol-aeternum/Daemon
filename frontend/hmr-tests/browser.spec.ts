import {
  test as base,
  expect,
  type Page,
  type BrowserContext,
} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const {
  createFixtureRun,
  ownFixture,
  observePage,
  browserLaunchReceipt,
} = require('./protocol-harness.cjs');
const origin = 'http://127.0.0.1:3151';
type Message = {
  type?: string;
  hash?: string;
  errors?: unknown[];
  devToolsConfig?: { theme?: string };
  devIndicator?: { disabledUntil?: number };
  data?: { theme?: string };
};
type RecordEntry = {
  event: string;
  at: number;
  socketId?: string;
  message?: Message;
  type?: string;
  text?: string;
  error?: string;
  url?: string;
  status?: number;
};
type Observer = {
  label: string;
  records: RecordEntry[];
  bodies: Record<string, string>;
  waitFor: (
    predicate: (entry: RecordEntry) => boolean,
    after?: number,
  ) => Promise<RecordEntry>;
  flush: () => Promise<void>;
};
type Client = { page: Page; context: BrowserContext; observation: Observer };
type Session = {
  A: Client;
  C: Client;
  all: Client[];
  run: { dir: string; mutate: (action: string) => unknown; records: unknown[] };
  owner: {
    records: unknown[];
    closeHmr: (id: string) => Promise<void>;
    stop: () => Promise<void>;
    start: () => Promise<void>;
    arm: () => Promise<void>;
  };
  open: (label: string, route?: string) => Promise<Client>;
  phases: { name: string; started: number; ended?: number }[];
};
const received = (type: string) => (entry: RecordEntry) =>
  entry.event === 'framereceived' && entry.message?.type === type;
const syncs = (client: Client) =>
  client.observation.records.filter(received('sync'));
const documents = (client: Client) =>
  client.observation.records.filter((entry) => entry.event === 'document')
    .length;
const lastSync = (client: Client) => syncs(client).at(-1)?.message?.hash;
const socketId = (client: Client) =>
  client.observation.records.filter((entry) => entry.event === 'socket').at(-1)
    ?.socketId;
async function renderBarrier(client: Client) {
  await client.page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
}
async function ready(client: Client, version?: string) {
  await expect(client.page.getByTestId('client-version')).toHaveText(
    version ?? /client-/,
  );
  await expect(
    client.page.getByRole('button', { name: /^Count / }),
  ).toBeVisible();
  // A recovered document can render before its replacement HMR socket connects.
  // Never accept a SYNC retained from the document/socket it replaced.
  await client.observation.waitFor((entry) => {
    const records = client.observation.records;
    const documentStart = records.findLastIndex(
      (record) => record.event === 'document',
    );
    const connection = records.findLastIndex(
      (record) => record.event === 'socket',
    );
    const id = records[connection]?.socketId;
    return (
      received('sync')(entry) &&
      Boolean(id) &&
      connection > documentStart &&
      records.indexOf(entry) > connection &&
      entry.socketId === id &&
      !records
        .slice(connection + 1)
        .some(
          (record) => record.event === 'socket-close' && record.socketId === id,
        )
    );
  });
}
async function overlay(client: Client, text: string) {
  const portal = client.page.locator('nextjs-portal');
  await expect(portal).toContainText(text);
  await expect(portal.locator('[data-nextjs-dialog]')).toBeVisible();
}
async function noCollateralJoin(s: Session, label: string) {
  const before = [s.A, s.C].map((client) => ({
    sync: syncs(client).length,
    documents: documents(client),
    socket: socketId(client),
  }));
  const newcomer = await s.open(label);
  const snapshot = await newcomer.observation.waitFor(received('sync'));
  for (const [index, client] of [s.A, s.C].entries()) {
    await renderBarrier(client);
    expect(syncs(client)).toHaveLength(before[index].sync);
    expect(documents(client)).toBe(before[index].documents);
    expect(socketId(client)).toBe(before[index].socket);
  }
  return { newcomer, snapshot };
}

const test = base.extend<{}, { protocolSession: Session }>({
  protocolSession: [
    async ({ browser }, provide, workerInfo) => {
      const frontend = path.resolve(__dirname, '..');
      const run = createFixtureRun(frontend, 'j490-hmr-protocol');
      const owner = await ownFixture(run);
      const all: Client[] = [];
      const phases: Session['phases'] = [];
      let session: Session | undefined;
      const open = async (label: string, route = '/') => {
        const context = await browser.newContext();
        const page = await context.newPage();
        const observation = await observePage(page, label);
        const client = { context, page, observation };
        all.push(client);
        await page.goto(`${origin}${route}`);
        return client;
      };
      try {
        const browserCDP = await browser.newBrowserCDPSession();
        const launchReceipt = browserLaunchReceipt(browser);
        const {
          executablePath,
          resolvedExecutablePath,
          sha256AfterLaunch: executableSha256,
        } = launchReceipt.actual;
        expect(path.isAbsolute(executablePath)).toBe(true);
        expect(launchReceipt.version).toBe(browser.version());
        fs.writeFileSync(
          path.join(run.dir, 'browser-identity.json'),
          JSON.stringify(
            {
              version: browser.version(),
              executablePath,
              resolvedExecutablePath,
              executableSha256,
              protocol: await browserCDP.send('Browser.getVersion'),
              launchReceipt,
              configuredWorkers: workerInfo.config.workers,
              workerIndex: workerInfo.workerIndex,
              retryPolicy: 0,
              generatedState:
                'fresh generated app; restart later reuses its own warm compilation/cache',
            },
            null,
            2,
          ),
        );
        const identity = JSON.parse(
          fs.readFileSync(path.join(run.dir, 'browser-identity.json'), 'utf8'),
        );
        expect(identity.resolvedExecutablePath).toBe(resolvedExecutablePath);
        expect(identity.executableSha256).toMatch(/^[a-f0-9]{64}$/);
        await browserCDP.detach();
        await owner.start();
        await owner.arm();
        const A = await open('A');
        const C = await open('C');
        await ready(A, 'client-v1');
        await ready(C, 'client-v1');
        session = { A, C, all, run, owner, open, phases };
        await provide(session);
      } finally {
        const failures = [];
        for (const client of all) {
          try {
            await client.observation.flush();
          } catch (error) {
            failures.push(String(error));
          }
          fs.writeFileSync(
            path.join(run.dir, `observer-${client.observation.label}.json`),
            JSON.stringify({
              records: client.observation.records,
              bodiesBase64BySha256: client.observation.bodies,
            }),
          );
          try {
            await client.context.close();
          } catch (error) {
            failures.push(String(error));
          }
        }
        try {
          await owner.stop();
        } catch (error) {
          failures.push(String(error));
        }
        fs.writeFileSync(
          path.join(run.dir, 'screen-summary.json'),
          JSON.stringify(
            {
              phases,
              mutations: run.records,
              ownership: owner.records,
              failures,
              initialized: !!session,
            },
            null,
            2,
          ),
        );
        // WorkerInfo has no attachment API. The owned run directory is the
        // durable artifact authority; never fabricate a per-test attachment.
        if (failures.length)
          throw new Error(`HMR teardown failure(s): ${failures.join('; ')}`);
      }
    },
    { scope: 'worker', timeout: 120_000 },
  ],
});

test.describe('webpack-hmr-protocol', () => {
  test.describe.configure({ mode: 'serial' });
  test.beforeEach(async ({ protocolSession }, info) => {
    protocolSession.phases.push({ name: info.title, started: Date.now() });
  });
  test.afterEach(async ({ protocolSession }) => {
    protocolSession.phases.at(-1)!.ended = Date.now();
  });

  test('initial-and-equal-join', async ({ protocolSession: s }) => {
    expect(lastSync(s.A)).toBeTruthy();
    expect(lastSync(s.C)).toBe(lastSync(s.A));
    const { newcomer: B, snapshot } = await noCollateralJoin(s, 'B-equal');
    await ready(B, 'client-v1');
    expect(snapshot.message?.hash).toBe(lastSync(s.A));
    expect(syncs(B)).toHaveLength(1);
    await B.context.close();
  });

  test('ordinary-compile-and-changed-join', async ({ protocolSession: s }) => {
    const H1 = lastSync(s.A);
    const starts = [s.A, s.C].map(
      (client) => client.observation.records.length,
    );
    const docs = [documents(s.A), documents(s.C)];
    const D = await s.open('D-lazy', '/lazy');
    await expect(D.page.getByTestId('lazy-version')).toHaveText('lazy-v1');
    s.run.mutate('lazy-v2');
    await expect(D.page.getByTestId('lazy-version')).toHaveText('lazy-v2');
    const builds = await Promise.all(
      [s.A, s.C].map((client, index) =>
        client.observation.waitFor(
          (entry) =>
            received('built')(entry) &&
            !!entry.message?.hash &&
            entry.message.hash !== H1,
          starts[index],
        ),
      ),
    );
    expect(builds[0].message?.hash).not.toBe(H1);
    expect(lastSync(s.A)).toBe(H1);
    const { newcomer: B, snapshot } = await noCollateralJoin(s, 'B-changed');
    expect(snapshot.message?.hash).not.toBe(H1);
    await ready(B, 'client-v1');
    expect([documents(s.A), documents(s.C)]).toEqual(docs);
    await B.context.close();
    await D.context.close();
  });

  test('visible-client-and-server-update', async ({ protocolSession: s }) => {
    const docs = [documents(s.A), documents(s.C)];
    for (const client of [s.A, s.C])
      await client.page
        .getByRole('button', { name: 'Count 0', exact: true })
        .click();
    s.run.mutate('counter-v2');
    for (const client of [s.A, s.C]) {
      await expect(client.page.getByTestId('client-version')).toHaveText(
        'client-v2',
      );
      await expect(
        client.page.getByRole('button', { name: 'Count 1', exact: true }),
      ).toBeVisible();
    }
    s.run.mutate('server-v2');
    for (const client of [s.A, s.C])
      await expect(client.page.getByTestId('server-version')).toHaveText(
        'server-v2',
      );
    expect([documents(s.A), documents(s.C)]).toEqual(docs);
  });

  test('compile-overlay-and-recovery', async ({ protocolSession: s }) => {
    s.run.mutate('counter-syntax-error');
    for (const client of [s.A, s.C]) await overlay(client, 'counter.jsx');
    const B = await s.open('B-compile-error');
    const snapshot = await B.observation.waitFor(received('sync'));
    expect(JSON.stringify(snapshot.message?.errors)).toContain('counter.jsx');
    await overlay(B, 'counter.jsx');
    s.run.mutate('counter-healthy');
    for (const client of [s.A, s.C, B]) {
      await expect(client.page.getByTestId('client-version')).toHaveText(
        'client-healthy',
      );
      await expect(
        client.page.locator('nextjs-portal [data-nextjs-dialog]'),
      ).not.toBeVisible();
      await ready(client, 'client-healthy');
    }
    await B.context.close();
  });

  test('runtime-error-needed-reload', async ({ protocolSession: s }) => {
    s.run.mutate('counter-render-error');
    for (const client of [s.A, s.C]) {
      await expect(client.page.getByTestId('client-version')).toHaveText(
        'client-render',
      );
      await client.page
        .getByRole('button', { name: 'Trigger render error', exact: true })
        .click();
      await overlay(client, 'HMR_FIXTURE_RENDER_V1');
    }
    const docs = [documents(s.A), documents(s.C)];
    s.run.mutate('counter-healthy');
    for (const [index, client] of [s.A, s.C].entries()) {
      await expect(client.page.getByTestId('client-version')).toHaveText(
        'client-healthy',
      );
      expect(documents(client)).toBeGreaterThan(docs[index]);
      await expect(
        client.page.getByRole('button', { name: 'Count 0', exact: true }),
      ).toBeVisible();
      await ready(client, 'client-healthy');
    }
  });

  test('equal-and-changed-reconnect', async ({ protocolSession: s }) => {
    expect(lastSync(s.A)).toBe(lastSync(s.C));
    const oldSocket = socketId(s.A)!;
    const starts = [s.A, s.C].map(
      (client) => client.observation.records.length,
    );
    const docs = [documents(s.A), documents(s.C)];
    await s.owner.closeHmr(oldSocket);
    const equal = await s.A.observation.waitFor(received('sync'), starts[0]);
    expect(equal.message?.hash).toBe(lastSync(s.C));
    expect(
      s.A.observation.records
        .slice(starts[0])
        .filter((entry) => entry.event === 'socket'),
    ).toHaveLength(1);
    await renderBarrier(s.A);
    expect(documents(s.A)).toBe(docs[0]);
    s.run.mutate('counter-v3');
    for (const client of [s.A, s.C])
      await expect(client.page.getByTestId('client-version')).toHaveText(
        'client-v3',
      );
    const changedStart = s.A.observation.records.length;
    await s.owner.closeHmr(socketId(s.A)!);
    const changed = await s.A.observation.waitFor(
      received('sync'),
      changedStart,
    );
    expect(changed.message?.hash).not.toBe(equal.message?.hash);
    await ready(s.A, 'client-v3');
    expect(documents(s.A)).toBeGreaterThan(docs[0]);
    expect(documents(s.C)).toBe(docs[1]);
    expect(
      s.C.observation.records.slice(starts[1]).filter(received('sync')),
    ).toHaveLength(0);
    // Prove the replacement actually continues receiving real source updates.
    s.run.mutate('counter-v2');
    for (const client of [s.A, s.C])
      await expect(client.page.getByTestId('client-version')).toHaveText(
        'client-v2',
      );
  });

  test('changed-hash-server-restart', async ({ protocolSession: s }) => {
    const old = [lastSync(s.A), lastSync(s.C)];
    const starts = [s.A, s.C].map(
      (client) => client.observation.records.length,
    );
    const docs = [documents(s.A), documents(s.C)];
    await s.owner.stop();
    s.run.mutate('counter-restart-v4');
    await s.owner.start();
    await s.owner.arm();
    for (const [index, client] of [s.A, s.C].entries()) {
      const snapshot = await client.observation.waitFor(
        received('sync'),
        starts[index],
      );
      expect(snapshot.message?.hash).not.toBe(old[index]);
      await ready(client, 'client-v4');
      expect(documents(client)).toBeGreaterThan(docs[index]);
    }
    const generations = s.owner.records.filter(
      (record) => (record as { event: string }).event === 'bound',
    );
    expect(generations).toHaveLength(2);
  });

  test('metadata-and-final-integrity', async ({ protocolSession: s }) => {
    for (const theme of ['light', 'dark']) {
      const starts = [s.A, s.C].map(
        (client) => client.observation.records.length,
      );
      const response = await s.A.page.request.post(
        `${origin}/__nextjs_devtools_config`,
        { data: { theme } },
      );
      expect(response.status()).toBe(204);
      for (const [index, client] of [s.A, s.C].entries()) {
        const message = await client.observation.waitFor(
          received('devtoolsConfig'),
          starts[index],
        );
        expect(message.message?.data?.theme).toBe(theme);
        await expect(client.page.locator('nextjs-portal')).toHaveClass(
          new RegExp(`\\b${theme}\\b`),
        );
        await expect(
          client.page
            .locator('nextjs-portal [data-nextjs-dev-tools-button]')
            .first(),
        ).toBeVisible();
      }
    }
    const { newcomer: B, snapshot } = await noCollateralJoin(s, 'B-metadata');
    expect(snapshot.message?.devToolsConfig?.theme).toBe('dark');
    await ready(B, 'client-v4');
    // Exercise the real UI removal path as well as its real POST, never fake expiry.
    await s.A.page
      .locator('nextjs-portal')
      .getByRole('button', { name: /Next.js Dev Tools/i })
      .click();
    await s.A.page.locator('nextjs-portal [data-preferences]').click();
    await s.A.page.locator('nextjs-portal [data-hide-dev-tools]').click();
    await expect(
      s.A.page
        .locator('nextjs-portal')
        .getByRole('button', { name: /Next.js Dev Tools/i }),
    ).not.toBeVisible();
    const response = await s.C.page.request.post(
      `${origin}/__nextjs_disable_dev_indicator`,
    );
    expect(response.status()).toBe(204);
    const { newcomer: D, snapshot: disabled } = await noCollateralJoin(
      s,
      'B-disabled',
    );
    expect(disabled.message?.devIndicator?.disabledUntil).toBeGreaterThan(
      Date.now(),
    );
    await ready(D, 'client-v4');
    await expect(
      D.page
        .locator('nextjs-portal')
        .getByRole('button', { name: /Next.js Dev Tools/i }),
    ).not.toBeVisible();
    for (const client of [s.A, s.C, B, D])
      await expect(client.page.getByTestId('client-version')).toHaveText(
        'client-v4',
      );
    const unexpected = [];
    for (const client of s.all)
      for (const entry of client.observation.records) {
        const phase = s.phases.find(
          (item) =>
            entry.at >= item.started && entry.at <= (item.ended ?? Infinity),
        )?.name;
        const detail = entry.text ?? entry.error ?? '';
        if (
          entry.event === 'pageerror' ||
          (entry.event === 'console' && entry.type === 'error')
        ) {
          const intentionalCompile =
            phase === 'compile-overlay-and-recovery' &&
            /counter\.jsx|HMR_FIXTURE_SYNTAX/.test(detail);
          const intentionalRender =
            phase === 'runtime-error-needed-reload' &&
            /HMR_FIXTURE_RENDER_V1/.test(detail);
          const intentionalHTTP =
            phase === 'compile-overlay-and-recovery' &&
            /Failed to load resource.*500/.test(detail);
          if (!intentionalCompile && !intentionalRender && !intentionalHTTP)
            unexpected.push({ label: client.observation.label, phase, entry });
        }
      }
    fs.writeFileSync(
      path.join(s.run.dir, 'unexpected-errors.json'),
      JSON.stringify(unexpected, null, 2),
    );
    expect(unexpected).toEqual([]);
    await B.context.close();
    await D.context.close();
  });
});
