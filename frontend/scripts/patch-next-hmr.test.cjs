'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawnSync } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const vm = require('node:vm');
const {
  inspectInstallation,
  applyPatch,
  checkPatched,
  targets,
  hash,
} = require('./patch-next-hmr.cjs');
const {
  loadMiddleware,
  loadClient,
  createFixtureRun,
  ownFixture,
  validateIdentity,
  controlFrame,
  admitRun,
  installLaunchRecorder,
  browserLaunchReceipt,
  parseLaunchLine,
  observePage,
} = require('../hmr-tests/protocol-harness.cjs');
const frontend = path.resolve(__dirname, '..');
const {
  createUpgradeGate,
  createStartupSequence,
  startupUpgradeLimits,
} = require('../hmr-tests/server.cjs');

function upgradeFixture(dispatch = () => {}) {
  const { EventEmitter } = require('node:events');
  const activeTimers = new Set();
  const callbacks = [];
  const records = [];
  const failures = [];
  const calls = [];
  const gate = createUpgradeGate({
    dispatch: (...args) => {
      calls.push(args);
      return dispatch(...args);
    },
    failure: (error) => failures.push(error),
    record: (event, detail) => records.push({ event, ...detail }),
    timers: {
      setTimeout(fn, ms) {
        assert.equal(ms, 120000);
        callbacks.push(fn);
        activeTimers.add(fn);
        return fn;
      },
      clearTimeout(fn) {
        activeTimers.delete(fn);
      },
    },
  });
  function socket() {
    const s = new EventEmitter();
    Object.assign(s, {
      destroyed: false,
      pauses: 0,
      resumes: 0,
      pause() {
        this.pauses++;
      },
      resume() {
        this.resumes++;
      },
      destroy() {
        if (!this.destroyed) {
          this.destroyed = true;
          this.emit('close');
        }
      },
    });
    return s;
  }
  return { gate, socket, callbacks, activeTimers, records, failures, calls };
}

test('startup/admission: exact tuple, no-ID, single forward, stale timer, ordinary armed upgrade', async () => {
  const p = upgradeFixture();
  const req = { url: '/_next/hmr' },
    socket = p.socket(),
    head = Buffer.from('untouched-head');
  p.gate.upgrade(req, socket, head);
  assert.equal(socket.pauses, 1);
  assert.equal(p.calls.length, 0);
  assert.equal(p.gate.arm(), true);
  assert.deepEqual(p.calls, [[req, socket, head]]);
  assert.equal(p.calls[0][0], req);
  assert.equal(p.calls[0][2], head);
  assert.equal(p.activeTimers.size, 0);
  assert.equal(socket.listenerCount('error'), 0);
  assert.equal(socket.listenerCount('close'), 0);
  p.callbacks[0]();
  assert.equal(p.failures.length, 0);
  await Promise.resolve();
  assert.equal(socket.resumes, 1);
  assert.throws(() => p.gate.arm(), /cannot arm/);
  const normal = p.socket();
  p.gate.upgrade({ url: '/_next/hmr?id=ordinary' }, normal, Buffer.alloc(0));
  assert.equal(p.calls.length, 2);
  assert.equal(normal.pauses, 0);
  p.gate.close();
});

test('startup/admission: peer close, ended peer, stop and late timers never resurrect sockets', async () => {
  const p = upgradeFixture();
  const closed = p.socket(),
    ended = p.socket(),
    live = p.socket();
  for (const socket of [closed, ended, live])
    p.gate.upgrade({ url: '/_next/hmr?id=x' }, socket, Buffer.alloc(0));
  closed.destroy();
  ended.readableEnded = true;
  assert.equal(p.gate.size, 2);
  p.gate.arm();
  assert.deepEqual(
    p.calls.map((call) => call[1]),
    [live],
  );
  p.gate.close();
  await Promise.resolve();
  assert.equal(
    live.resumes,
    0,
    'stop before dispatch resolution cannot resume',
  );
  assert.equal(p.gate.size, 0);
  assert.equal(p.activeTimers.size, 0);
  for (const callback of p.callbacks) callback();
  assert.equal(p.failures.length, 0);
  const late = p.socket();
  p.gate.upgrade({ url: '/_next/hmr' }, late, Buffer.alloc(0));
  assert.equal(late.destroyed, true);
  assert.equal(p.calls.length, 1);
  assert.throws(() => p.gate.arm(), /cannot arm/);
});

test('startup/admission: pending stop clears all ownership and unrelated upgrades are not queued', () => {
  const p = upgradeFixture();
  const unrelated = p.socket(),
    held = p.socket();
  p.gate.upgrade(
    { url: '/not-hmr?next=/_next/hmr' },
    unrelated,
    Buffer.alloc(0),
  );
  assert.equal(unrelated.destroyed, true);
  p.gate.upgrade({ url: '/_next/hmr' }, held, Buffer.alloc(0));
  p.gate.close();
  p.gate.close();
  assert.equal(held.destroyed, true);
  assert.equal(held.listenerCount('error') + held.listenerCount('close'), 0);
  assert.equal(p.gate.size + p.activeTimers.size, 0);
  for (const callback of p.callbacks) callback();
  assert.equal(p.calls.length + p.failures.length, 0);
});

for (const cause of ['count', 'head', 'expiry', 'error', 'bad-url'])
  test(`startup/admission: ${cause} fails visibly and cancels the whole pending queue`, () => {
    const p = upgradeFixture();
    const held = p.socket();
    p.gate.upgrade({ url: '/_next/hmr?id=held' }, held, Buffer.alloc(0));
    if (cause === 'count')
      for (let i = 0; i < startupUpgradeLimits.count; i++)
        p.gate.upgrade({ url: '/_next/hmr' }, p.socket(), Buffer.alloc(0));
    if (cause === 'head')
      p.gate.upgrade({ url: '/_next/hmr' }, p.socket(), Buffer.alloc(65537));
    if (cause === 'expiry') p.callbacks[0]();
    if (cause === 'error') held.emit('error', new Error('real socket failure'));
    if (cause === 'bad-url')
      p.gate.upgrade({ url: 'http://[' }, p.socket(), Buffer.alloc(0));
    assert.equal(p.failures.length, 1);
    assert.equal(p.gate.size + p.activeTimers.size, 0);
    assert.equal(held.destroyed, true);
    assert.equal(p.calls.length, 0);
    assert.throws(() => p.gate.arm(), /cannot arm/);
  });

for (const mode of ['throw', 'reject', 'reentrant-stop'])
  test(`startup/admission: upstream ${mode} cannot leak or release later entries`, async () => {
    let p;
    p = upgradeFixture(() => {
      if (mode === 'throw') throw new Error('sync upstream failure');
      if (mode === 'reject')
        return Promise.reject(new Error('async upstream failure'));
      p.gate.close();
    });
    const first = p.socket(),
      second = p.socket();
    for (const s of [first, second])
      p.gate.upgrade({ url: '/_next/hmr' }, s, Buffer.alloc(0));
    p.gate.arm();
    await Promise.resolve();
    assert.equal(p.gate.size + p.activeTimers.size, 0);
    assert.equal(first.resumes + second.resumes, 0);
    // Both synchronous handoffs have begun before promise rejections run. Each
    // rejection stays visible; the first closes admission and prevents resumes.
    assert.equal(
      p.failures.length,
      mode === 'reentrant-stop' ? 0 : mode === 'reject' ? 2 : 1,
    );
    assert.equal(p.calls.length, mode === 'reject' ? 2 : 1);
  });

for (const cancelledStage of [0, 1, 3])
  test(`startup/sequence: cancel during stage${cancelledStage} fences late prepare/bootstrap/bound`, async () => {
    let release, entered;
    const blocked = new Promise((resolve) => {
      release = resolve;
    });
    const started = new Promise((resolve) => {
      entered = resolve;
    });
    const calls = [];
    const stages = ['bind', 'prepare', 'handlers', 'bootstrap', 'bound'].map(
      (name, index) => async (signal) => {
        calls.push(name);
        if (index === cancelledStage) {
          entered(signal);
          await blocked;
        }
      },
    );
    const startup = createStartupSequence(stages);
    const signal = await started;
    startup.cancel();
    assert.equal(signal.aborted, true);
    let closed = false;
    const shutdown = startup.settled
      .catch(() => {})
      .then(() => {
        closed = true;
      });
    await Promise.resolve();
    assert.equal(closed, false, 'close must wait for preparation to settle');
    release();
    assert.equal(await startup.settled, false);
    await shutdown;
    assert.equal(closed, true);
    assert.deepEqual(
      calls,
      ['bind', 'prepare', 'handlers', 'bootstrap', 'bound'].slice(
        0,
        cancelledStage + 1,
      ),
    );
  });

test('startup/sequence: immediate cancellation, successful ordering and startup failure remain distinct', async () => {
  const calls = [];
  const cancelled = createStartupSequence([() => calls.push('unexpected')]);
  cancelled.cancel();
  assert.equal(await cancelled.settled, false);
  const ok = createStartupSequence(
    ['bind', 'prepare', 'handlers', 'bootstrap', 'bound'].map(
      (name) => () => calls.push(name),
    ),
  );
  assert.equal(await ok.settled, true);
  assert.deepEqual(calls, [
    'bind',
    'prepare',
    'handlers',
    'bootstrap',
    'bound',
  ]);
  const broken = createStartupSequence([
    () => {
      throw new Error('honest preparation failure');
    },
    () => calls.push('resurrect'),
  ]);
  await assert.rejects(broken.settled, /honest preparation failure/);
  assert.equal(calls.includes('resurrect'), false);
});

test('startup/admission: real loopback WebSocket is held, then hands off to pinned readers and exchanges data', async () => {
  const http = require('node:http');
  const { once } = require('node:events');
  const { WebSocket, WebSocketServer } = require('next/dist/compiled/ws');
  const server = http.createServer();
  const wsServer = new WebSocketServer({ noServer: true });
  const sockets = new Set();
  let queued,
    opened = false,
    calls = 0;
  const held = new Promise((resolve) => {
    queued = resolve;
  });
  const failures = [];
  const gate = createUpgradeGate({
    dispatch(req, socket, head) {
      calls++;
      wsServer.handleUpgrade(req, socket, head, (ws) => {
        ws.on('message', (data) => ws.send(data));
      });
    },
    failure: (error) => failures.push(error),
    record: (event) => {
      if (event === 'upgrade-queued') queued();
    },
  });
  server.on('connection', (s) => {
    sockets.add(s);
    s.on('close', () => sockets.delete(s));
  });
  server.on('upgrade', gate.upgrade);
  let client;
  try {
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    client = new WebSocket(`ws://127.0.0.1:${server.address().port}/_next/hmr`);
    client.on('open', () => {
      opened = true;
    });
    await held;
    assert.equal(opened, false);
    assert.equal(calls, 0);
    const open = once(client, 'open');
    assert.equal(gate.arm(), true);
    await open;
    const echoed = once(client, 'message');
    client.send('real-frame-after-arm');
    assert.equal((await echoed)[0].toString(), 'real-frame-after-arm');
    assert.equal(calls, 1);
    assert.equal(gate.size, 0);
    assert.deepEqual(failures, []);
    const closed = once(client, 'close');
    client.close();
    await closed;
  } finally {
    gate.close();
    client?.terminate();
    const closed = new Promise((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
    for (const s of sockets) s.destroy();
    await closed;
    await new Promise((resolve) => wsServer.close(resolve));
  }
});

test('observer/metadata-before-body: unavailable body preserves response status and error', async () => {
  const { EventEmitter } = require('node:events');
  const cdp = new EventEmitter();
  cdp.send = async (method) => assert.equal(method, 'Network.enable');
  const page = new EventEmitter();
  page.context = () => ({ newCDPSession: async () => cdp });
  const observation = await observePage(page, 'offline-response');
  const url =
    'http://127.0.0.1:3151/_next/static/webpack/fixture.hot-update.json';
  page.emit('response', {
    url: () => url,
    status: () => 404,
    body: async () => {
      assert.equal(observation.records[0].event, 'response');
      throw new Error('synthetic unavailable body');
    },
  });
  await observation.flush();
  assert.deepEqual(
    observation.records.map(({ event }) => event),
    ['response', 'body-error'],
  );
  assert.equal(observation.records[0].status, 404);
  assert.equal(observation.records[0].url, url);
  assert.match(observation.records[1].error, /synthetic unavailable body/);
  assert.deepEqual(observation.bodies, {});
  page.emit('response', {
    url: () => 'http://127.0.0.1:3101/foreign-to-this-observer',
    status: () => 200,
    body: async () => assert.fail('Foreign origin body must not be read'),
  });
  assert.equal(observation.records.length, 2);
});

test('identity/launch-owned provenance (synthetic logger, no browser)', async () => {
  const { EventEmitter } = require('node:events');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hmr-launch-'));
  const executable = path.join(dir, 'synthetic-browser');
  fs.writeFileSync(executable, 'synthetic executable bytes, never executed');
  const browser = new EventEmitter();
  browser.version = () => '145.synthetic';
  browser.close = async () => browser.emit('disconnected');
  const options = { headless: true };
  const type = {
    executablePath: () => '/catalog/not-proof',
    async launch(actual) {
      assert.equal(actual, options, 'launch options forwarded by identity');
      process.stderr.write(
        `pw:browser <launching> ${executable} --headless --no-startup-window +0ms\n`,
      );
      process.stderr.write('pw:browser <launched> pid=12345 +1ms\n');
      return browser;
    },
  };
  installLaunchRecorder({ chromium: type });
  assert.equal(await type.launch(options), browser);
  const receipt = browserLaunchReceipt(browser);
  assert.equal(receipt.actual.executablePath, executable);
  assert.equal(
    receipt.actual.observedArgumentsText,
    '--headless --no-startup-window',
  );
  assert.equal(
    receipt.actual.sha256BeforeSpawn,
    hash(fs.readFileSync(executable)),
  );
  assert.equal(
    receipt.actual.sha256AfterLaunch,
    receipt.actual.sha256BeforeSpawn,
  );
  assert.equal(receipt.browserPid, 12345);
  assert.throws(
    () => browserLaunchReceipt({ version: browser.version }),
    /matching/,
  );
  assert.throws(() => parseLaunchLine('catalog label only'), /unparseable/);
  assert.match(
    fs.readFileSync(receipt.logPath, 'utf8'),
    /<launched> pid=12345/,
  );
  await browser.close();
  assert.ok(fs.existsSync(path.join(receipt.dir, 'disconnected.json')));
});
const nextRoot = path.join(frontend, 'node_modules/next');
const read = (relative) => fs.readFileSync(path.join(nextRoot, relative));
const preimages = targets.map((target) => {
  const bytes = read(target.file);
  const original =
    hash(bytes) === target.before
      ? bytes
      : Buffer.from(
          target.edits.reduce(
            (text, edit) => text.replace(edit.after, edit.before),
            bytes.toString(),
          ),
        );
  assert.equal(hash(original), target.before);
  return original;
});
function installation() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hmr-guard-'));
  const next = path.join(root, 'node_modules/next');
  fs.mkdirSync(next, { recursive: true });
  fs.writeFileSync(
    path.join(next, 'package.json'),
    JSON.stringify({ name: 'next', version: '16.4.1' }),
  );
  targets.forEach((target, index) => {
    const file = path.join(next, target.file);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, preimages[index]);
  });
  return root;
}
const targetPath = (root, index = 0) =>
  path.join(root, 'node_modules/next', targets[index].file);
function snapshot(root) {
  return targets.map((_, index) => {
    const file = targetPath(root, index);
    return {
      hash: hash(fs.readFileSync(file)),
      mtime: fs.statSync(file, { bigint: true }).mtimeNs,
    };
  });
}

test('guard/preimages', async (t) => {
  const root = installation();
  const before = snapshot(root);
  assert.equal(inspectInstallation(root).state, 'original');
  assert.throws(() => checkPatched(root), /not fully patched/);
  assert.deepEqual(snapshot(root), before);
  await t.test('clean four-old apply and exact four-new check', () => {
    assert.equal(applyPatch(root).state, 'patched');
    assert.equal(checkPatched(root).state, 'patched');
    targets.forEach((target, index) =>
      assert.equal(
        hash(fs.readFileSync(targetPath(root, index))),
        target.after,
      ),
    );
  });
  await t.test(
    'all-patched idempotence preserves target bytes and nanosecond mtimes',
    () => {
      const patched = snapshot(root);
      applyPatch(root);
      assert.deepEqual(snapshot(root), patched);
    },
  );
});

test('guard/exact server-only migration preserves server bytes and mtimes', () => {
  const root = installation();
  for (const index of [0, 1]) {
    const target = targets[index];
    fs.writeFileSync(
      targetPath(root, index),
      target.edits.reduce(
        (text, edit) => text.replace(edit.before, edit.after),
        preimages[index].toString(),
      ),
    );
  }
  const servers = snapshot(root).slice(0, 2);
  assert.equal(inspectInstallation(root).state, 'server-only');
  assert.throws(() => checkPatched(root), /not fully patched/);
  assert.equal(applyPatch(root).state, 'patched');
  assert.deepEqual(snapshot(root).slice(0, 2), servers);
  const patched = snapshot(root);
  applyPatch(root);
  assert.deepEqual(snapshot(root), patched);
});

test('guard/client drift and partial client extension are refused', () => {
  for (const index of [2, 3]) {
    const drifted = installation();
    fs.appendFileSync(targetPath(drifted, index), '// drift');
    assert.throws(() => inspectInstallation(drifted), /Unknown bytes/);
    const partial = installation();
    applyPatch(partial);
    fs.writeFileSync(targetPath(partial, index), preimages[index]);
    assert.throws(() => inspectInstallation(partial), /Mixed/);
    assert.throws(() => applyPatch(partial), /Mixed/);
    assert.throws(() => checkPatched(partial), /lock/);
  }
});

test('guard/negatives', async (t) => {
  const changes = {
    'previous supported version': (root) =>
      fs.writeFileSync(
        path.join(root, 'node_modules/next/package.json'),
        '{"name":"next","version":"16.3.8"}',
      ),
    'wrong version': (root) =>
      fs.writeFileSync(
        path.join(root, 'node_modules/next/package.json'),
        '{"name":"next","version":"16.4.2"}',
      ),
    'wrong name': (root) =>
      fs.writeFileSync(
        path.join(root, 'node_modules/next/package.json'),
        '{"name":"other","version":"16.4.1"}',
      ),
    'changed bytes same version': (root) =>
      fs.appendFileSync(targetPath(root), '// changed'),
    'zero seam': (root) =>
      fs.writeFileSync(
        targetPath(root),
        preimages[0]
          .toString()
          .replace(
            'HMR_MESSAGE_SENT_TO_BROWSER.SYNC',
            'HMR_MESSAGE_SENT_TO_BROWSER.OTHER',
          ),
      ),
    'two seams': (root) =>
      fs.appendFileSync(
        targetPath(root),
        'this.publish({\n                    type: _hotreloadertypes.HMR_MESSAGE_SENT_TO_BROWSER.SYNC,',
      ),
    'missing target': (root) => fs.unlinkSync(targetPath(root)),
    'truncated partial output': (root) =>
      fs.writeFileSync(targetPath(root), 'this.publishToClient(client,'),
    'mixed pair': (root) => {
      applyPatch(root);
      fs.writeFileSync(targetPath(root, 1), preimages[1]);
    },
    'target symlink': (root) => {
      const file = targetPath(root);
      fs.renameSync(file, `${file}.retained`);
      fs.symlinkSync(`${file}.retained`, file);
    },
    'parent symlink': (root) => {
      const dir = path.dirname(targetPath(root));
      fs.renameSync(dir, `${dir}-retained`);
      fs.symlinkSync(`${dir}-retained`, dir);
    },
  };
  for (const [name, mutate] of Object.entries(changes))
    await t.test(name, () => {
      const root = installation();
      mutate(root);
      assert.throws(() => applyPatch(root));
      assert.ok(
        fs.existsSync(path.join(root, '.hmr-runs/patch.lock')),
        'failure lock retained',
      );
      assert.throws(() => checkPatched(root));
    });
  await t.test(
    'in-progress lock never deleted; read-only check does not follow it',
    () => {
      const root = installation();
      applyPatch(root);
      const lock = path.join(root, '.hmr-runs/patch.lock');
      fs.writeFileSync(lock, 'foreign-exact-evidence');
      assert.throws(() => applyPatch(root), /EEXIST/);
      assert.throws(() => checkPatched(root), /lock/);
      assert.equal(fs.readFileSync(lock, 'utf8'), 'foreign-exact-evidence');
    },
  );
  await t.test(
    'read-only check refuses a dangling generated-directory symlink',
    () => {
      const root = installation();
      applyPatch(root);
      const generated = path.join(root, '.hmr-runs');
      fs.renameSync(generated, `${generated}-retained`);
      fs.symlinkSync(path.join(root, 'absent-target'), generated);
      assert.throws(() => checkPatched(root), /Symlink/);
      assert.throws(() => applyPatch(root), /Symlink/);
    },
  );
  for (const name of [
    'stage-write',
    'first-rename',
    'second-rename',
    'third-rename',
    'fourth-rename',
    'final-readback',
  ])
    await t.test(name, () => {
      const root = installation();
      let renames = 0;
      const io = new Proxy(fs, {
        get(object, key) {
          if (key === 'writeFileSync' && name === 'stage-write')
            return (file, ...args) => {
              if (
                typeof file === 'number' &&
                renames === 0 &&
                fs.fstatSync(file).size === 0
              ) {
                // The lock write is the first FD write; target the staged module instead.
                const destination = fs.readlinkSync(`/proc/self/fd/${file}`);
                if (destination.includes('patch-staging'))
                  throw new Error('INJECTED_STAGE_WRITE');
              }
              return object[key](file, ...args);
            };
          if (key === 'renameSync')
            return (...args) => {
              renames++;
              if (
                (name === 'first-rename' && renames === 1) ||
                (name === 'second-rename' && renames === 2) ||
                (name === 'third-rename' && renames === 3) ||
                (name === 'fourth-rename' && renames === 4)
              )
                throw new Error('INJECTED_RENAME');
              return object[key](...args);
            };
          if (key === 'readFileSync' && name === 'final-readback')
            return (file, ...args) => {
              if (renames === targets.length && file === targetPath(root))
                throw new Error('INJECTED_READBACK');
              return object[key](file, ...args);
            };
          return object[key];
        },
      });
      assert.throws(() => applyPatch(root, io), /INJECTED/);
      assert.ok(fs.existsSync(path.join(root, '.hmr-runs/patch.lock')));
      assert.ok(
        fs
          .readdirSync(path.join(root, '.hmr-runs'))
          .some((name) => name.startsWith('patch-failure-')),
      );
      assert.throws(() => checkPatched(root), /lock/);
      if (['second-rename', 'fourth-rename'].includes(name))
        assert.throws(() => inspectInstallation(root), /Mixed/);
    });
  for (const flags of [
    [],
    ['--apply', '/foreign-root'],
    ['--check', '--apply'],
    ['--root'],
    ['--unknown'],
  ])
    await t.test(`CLI rejects ${JSON.stringify(flags)}`, () => {
      const child = spawnSync(
        process.execPath,
        [path.join(__dirname, 'patch-next-hmr.cjs'), ...flags],
        { encoding: 'utf8' },
      );
      assert.equal(child.status, 1);
      assert.match(child.stderr, /Usage:/);
    });
});

function middleware(format, bytes, options = {}) {
  const types = require('next/dist/server/dev/hot-reloader-types');
  const state = { disabledUntil: 0 };
  let now = 1000;
  const table = {
    '../../build/utils': {
      isMiddlewareFilename: (name) => name === 'middleware',
    },
    './hot-reloader-types': types,
    './dev-indicator-server-state': { devIndicatorServerState: state },
    './messages': {
      createBinaryHmrMessageData: () => {
        throw new Error('Unexpected binary message in JSON controls');
      },
    },
    '../lib/trace/request-insights': {
      isRequestInsightsEnabled: () => false,
      getRequestInsightsSnapshot: () => ({ synthetic: 'insight' }),
    },
  };
  const Class = loadMiddleware({
    format,
    sourceBytes: bytes,
    collaborators: { table, Date: { now: () => ++now } },
  });
  const hooks = Array.from({ length: 3 }, () => ({
    hooks: {
      invalid: {
        tap(_name, callback) {
          this.callback = callback;
        },
      },
      done: {
        tap(_name, callback) {
          this.callback = callback;
        },
      },
    },
  }));
  const instance = new Class(
    hooks,
    { installed: '16.4.1' },
    'http://debug.invalid',
    {
      experimental: {
        requestInsights: true,
        exposeRuntimeErrorsToHMR: options.runtimeErrors ?? false,
      },
    },
    { theme: 'dark' },
    options.insight ?? Promise.resolve(false),
  );
  return { instance, hooks, state };
}
function stats(hash, errors = [], warnings = [], entry = 'app/page') {
  return {
    compilation: { entrypoints: new Map([[entry, {}]]) },
    hasErrors: () => errors.length > 0,
    toJson: () => ({ hash, errors, warnings }),
  };
}
function socket() {
  return {
    frames: [],
    listeners: {},
    send(data) {
      this.frames.push(JSON.parse(data));
    },
    addEventListener(type, callback) {
      this.listeners[type] = callback;
    },
    terminate() {
      this.listeners.close?.();
    },
  };
}
for (const [index, format] of ['cjs', 'esm'].entries())
  test(`middleware/recipients (${format === 'esm' ? 'transformed ESM, not native loader' : 'actual CJS'})`, async (t) => {
    const {
      instance: m,
      hooks,
      state,
    } = middleware(format, read(targets[index].file));
    const A = socket(),
      C = socket(),
      B = socket();
    m.onHMR(A, 'A');
    m.onHMR(C);
    assert.equal(
      A.frames.length + C.frames.length,
      0,
      'no-stats joins do not synthesize snapshots',
    );
    hooks[0].hooks.done.callback(stats('H1'));
    assert.deepEqual(
      A.frames.map((message) => message.type),
      ['built'],
    );
    A.frames.length = C.frames.length = 0;
    m.onHMR(B, 'B');
    assert.equal(B.frames[0].hash, 'H1');
    assert.equal(A.frames.length + C.frames.length, 0);
    assert.equal(m.getClient('B'), B);
    hooks[0].hooks.invalid.callback();
    hooks[0].hooks.done.callback(stats('H2', [], ['warning']));
    for (const client of [A, B, C])
      assert.deepEqual(
        client.frames.slice(-2).map((message) => message.type),
        ['building', 'built'],
      );
    A.frames.length = C.frames.length = 0;
    B.listeners.close();
    const replacement = socket();
    m.onHMR(replacement, 'B');
    assert.equal(replacement.frames[0].hash, 'H2');
    assert.equal(A.frames.length + C.frames.length, 0);
    await t.test(
      'server errors preferred, middleware errors/warnings merged; recovery broadcasts preserved',
      () => {
        hooks[1].hooks.done.callback(stats('SERVER_ERROR', ['server-error']));
        hooks[0].hooks.done.callback(stats('H3'));
        hooks[2].hooks.done.callback(
          stats('EDGE_ERROR', ['edge-error'], ['edge-warning'], 'middleware'),
        );
        state.disabledUntil = 100000;
        const E = socket();
        m.onHMR(E, 'E');
        assert.deepEqual(E.frames[0].errors, ['server-error', 'edge-error']);
        assert.deepEqual(E.frames[0].warnings, ['edge-warning']);
        assert.equal(E.frames[0].hash, 'SERVER_ERROR');
        assert.equal(E.frames[0].devIndicator.disabledUntil, 100000);
        assert.deepEqual(E.frames[0].requestInsights, { synthetic: 'insight' });
        hooks[1].hooks.invalid.callback();
        hooks[2].hooks.invalid.callback();
        assert.equal(E.frames.at(-1).hash, 'H3');
        state.disabledUntil = 1;
        const F = socket();
        m.onHMR(F);
        assert.equal(
          F.frames[0].devIndicator.disabledUntil,
          0,
          'actual onHMR expiry path under fake time',
        );
        m.updateDevToolsConfig({ theme: 'light' });
        const G = socket();
        m.onHMR(G);
        assert.equal(G.frames[0].devToolsConfig.theme, 'light');
        hooks[2].hooks.done.callback(
          stats('EDGE_APP_ERROR', ['edge-app-error']),
        );
        assert.equal(G.frames.at(-1).hash, 'EDGE_APP_ERROR');
      },
    );
    m.close();
    const closed = socket();
    m.onHMR(closed);
    assert.equal(closed.frames.length, 0);
  });
test('middleware/discriminator: exact unpatched preimages broadcast changed join to A/C', () => {
  for (const [index, format] of ['cjs', 'esm'].entries()) {
    const { instance: m, hooks } = middleware(format, preimages[index]);
    hooks[0].hooks.done.callback(stats('H1'));
    const A = socket(),
      C = socket(),
      B = socket();
    m.onHMR(A, 'A');
    m.onHMR(C);
    hooks[0].hooks.done.callback(stats('H2'));
    A.frames.length = C.frames.length = 0;
    m.onHMR(B, 'B');
    assert.equal(A.frames[0].type, 'sync');
    assert.equal(C.frames[0].hash, 'H2');
    assert.equal(B.frames[0].hash, 'H2');
  }
});
for (const [index, format] of ['cjs', 'esm'].entries())
  test(`middleware/${format}/16.4.1 insight broadcasts and runtime-error replay remain upstream behavior`, async () => {
    let resolveInsight;
    const insight = new Promise((resolve) => {
      resolveInsight = resolve;
    });
    const { instance: m, hooks } = middleware(
      format,
      read(targets[index].file),
      { insight, runtimeErrors: true },
    );
    hooks[0].hooks.done.callback(stats('H1'));
    const A = socket();
    m.onHMR(A, 'A');
    assert.equal(A.frames.at(-1).hasVulnerabilityInsight, false);
    A.frames.length = 0;
    resolveInsight(true);
    await Promise.resolve();
    assert.deepEqual(A.frames, [
      { type: 'vulnerabilityInsight', hasVulnerabilityInsight: true },
    ]);
    const runtimeError = {
      type: 'runtimeErrors',
      clientId: 'A',
      errors: [{ message: 'synthetic-runtime-error' }],
    };
    m.publish(runtimeError);
    assert.deepEqual(A.frames.at(-1), runtimeError);
    A.frames.length = 0;
    const B = socket();
    m.onHMR(B, 'B');
    assert.deepEqual(B.frames[0], runtimeError);
    assert.equal(B.frames[1].type, 'sync');
    assert.equal(B.frames[1].hash, 'H1');
    assert.equal(B.frames[1].hasVulnerabilityInsight, true);
    assert.equal(A.frames.length, 0, 'replayed errors and SYNC target only B');
    m.publish({ ...runtimeError, errors: [] });
    const C = socket();
    m.onHMR(C, 'C');
    assert.deepEqual(
      C.frames.map((frame) => frame.type),
      ['sync'],
    );
    m.publish(runtimeError);
    m.close();
    assert.equal(m.runtimeErrorStates.size, 0);
    const closed = socket();
    m.onHMR(closed, 'closed');
    assert.equal(closed.frames.length, 0);
  });
test('middleware/expected baseline limitation: same request-ID replacement then old close removes replacement', () => {
  const { instance: m } = middleware('cjs', read(targets[0].file));
  const old = socket(),
    replacement = socket();
  m.onHMR(old, 'same');
  m.onHMR(replacement, 'same');
  old.listeners.close();
  assert.equal(
    m.getClient('same'),
    undefined,
    'characterization only; not successful reconnect coverage',
  );
});

function client(options = {}) {
  const sockets = [],
    timers = new Map(),
    calls = [];
  let timerId = 0,
    loaded = 'H0';
  class Socket {
    static OPEN = 1;
    constructor(url) {
      this.url = url;
      this.OPEN = 1;
      this.readyState = 1;
      sockets.push(this);
    }
    send(data) {
      calls.push(['send', JSON.parse(data)]);
    }
    close() {
      this.readyState = 3;
    }
  }
  const hot = {
    hash: loaded,
    status: () => 'idle',
    check: async () => {
      if (options.checkError) throw new Error('check-failed');
      return options.nullCheck ? null : ['counter'];
    },
    apply: async () => {
      if (options.applyError) throw new Error('apply-failed');
      loaded = 'H2';
      instance.setHash(loaded);
      calls.push(['apply']);
      return options.nullApply ? null : ['counter'];
    },
  };
  const dispatcher = Object.fromEntries(
    [
      'onBuildOk',
      'onBuildError',
      'onBeforeRefresh',
      'onRefresh',
      'buildingIndicatorShow',
      'buildingIndicatorHide',
      'onVersionInfo',
      'onVulnerabilityInsight',
      'onDebugInfo',
      'onDevIndicator',
      'onDevToolsConfig',
      'onRequestInsightsSnapshot',
      'onRequestInsightsUpdate',
    ].map((name) => [name, (...args) => calls.push([name, ...args])]),
  );
  const instance = loadClient({
    moduleBytes: {
      socket: read('dist/client/dev/hot-reloader/app/web-socket.js'),
      app: read('dist/client/dev/hot-reloader/app/hot-reloader-app.js'),
    },
    hotRuntime: hot,
    clock: {
      setTimeout: (fn, delay) => {
        const id = ++timerId;
        timers.set(id, { fn, delay });
        return id;
      },
      clearTimeout: (id) => timers.delete(id),
    },
    socketFactory: Socket,
    dispatcher,
    router: { hmrRefresh: () => calls.push(['hmrRefresh']) },
  });
  const first = instance.socket.createWebSocket('', {});
  first.onopen();
  const send = (message) =>
    sockets.at(-1).onmessage({
      data: JSON.stringify({ errors: [], warnings: [], ...message }),
    });
  return {
    ...instance,
    sockets,
    timers,
    calls,
    send,
    reloads: () =>
      instance.events.filter(([event]) => event === 'reload').length,
    reconnect() {
      sockets.at(-1).onclose();
      const [id, timer] = [...timers].at(-1);
      timers.delete(id);
      timer.fn();
      sockets.at(-1).onopen();
    },
  };
}
const settle = async () => {
  for (let index = 0; index < 10; index++) await Promise.resolve();
};

// Execute the whole installed Pages decision module, not a rewritten decision.
// Only transport, DOM, dispatcher and webpack runtime are synthetic collaborators.
function pagesClient(format, options = {}) {
  const calls = [];
  const target = targets[format === 'cjs' ? 2 : 3];
  const sourceBytes = options.original
    ? preimages[format === 'cjs' ? 2 : 3]
    : read(target.file);
  const source =
    format === 'esm'
      ? require('typescript').transpileModule(sourceBytes.toString(), {
          compilerOptions: {
            module: require('typescript').ModuleKind.CommonJS,
            target: require('typescript').ScriptTarget.ES2020,
            esModuleInterop: true,
          },
        }).outputText
      : sourceBytes.toString();
  let listener;
  let context;
  let status = 'idle';
  const hot = {
    status: () => status,
    check: async () => {
      calls.push(['check']);
      if (options.checkError) throw new Error('honest-check-failure');
      return options.nullCheck ? null : ['counter'];
    },
    apply: async () => {
      calls.push(['apply']);
      context.__webpack_hash__ = 'H2';
      return ['counter'];
    },
  };
  const dispatcher = new Proxy(
    {},
    {
      get:
        (_, key) =>
        (...args) =>
          calls.push([key, ...args]),
    },
  );
  const table = {
    '@swc/helpers/_/_interop_require_default': {
      _: (value) => ({ default: value }),
    },
    'next/dist/compiled/next-devtools': { dispatcher },
    '../../../../next-devtools/userspace/pages/pages-dev-overlay-setup': {
      register() {},
    },
    'next/dist/compiled/strip-ansi': (value) => value,
    './websocket': {
      addMessageListener: (fn) => {
        listener = fn;
      },
      sendMessage: (value) => calls.push(['send', JSON.parse(value)]),
    },
    '../../../../shared/lib/format-webpack-messages': (value) => value,
    '../../../../server/dev/hot-reloader-types': require('next/dist/server/dev/hot-reloader-types'),
    '../shared': {
      reportInvalidHmrMessage: (...args) => calls.push(['invalid', ...args]),
    },
    '../../runtime-error-handler': {
      RuntimeErrorHandler: { hadRuntimeError: false },
    },
    '../../report-hmr-latency': () => {},
    '../turbopack-hot-reloader-common': {},
  };
  const evaluatedModule = { exports: {}, hot };
  context = vm.createContext({
    module: evaluatedModule,
    exports: evaluatedModule.exports,
    require: (name) => {
      assert.ok(
        Object.hasOwn(table, name),
        `Unlisted Pages dependency: ${name}`,
      );
      return table[name];
    },
    window: {
      __NEXT_DATA__: { page: options.page ?? '/_error' },
      location: { reload: () => calls.push(['reload']) },
    },
    document: {
      scripts: (
        options.scripts ?? [
          'https://cdn.test/asset/_next/static/chunks/fallback/webpack.js?ts=1',
        ]
      ).map((src) => ({ src })),
    },
    console: Object.fromEntries(
      ['log', 'warn', 'error', 'clear'].map((name) => [
        name,
        (...args) => calls.push([name, ...args]),
      ]),
    ),
    process: {
      env: { NODE_ENV: options.production ? 'production' : 'development' },
    },
    URL,
    __webpack_hash__: 'H0',
    self: {},
  });
  new vm.Script(source, { filename: `actual-pages-${format}.js` }).runInContext(
    context,
  );
  evaluatedModule.exports.default();
  return {
    calls,
    send: (message) => listener({ errors: [], warnings: [], ...message }),
    count: (name) => calls.filter((call) => call[0] === name).length,
    setStatus: (value) => {
      status = value;
    },
  };
}

for (const format of ['cjs', 'esm']) {
  test(`pages/${format}/16.4.1 insight metadata and notifications do not trigger updates`, () => {
    const c = pagesClient(format);
    c.send({ type: 'sync', hash: 'H0', hasVulnerabilityInsight: false });
    c.send({ type: 'vulnerabilityInsight', hasVulnerabilityInsight: true });
    assert.deepEqual(
      c.calls.filter(([name]) => name === 'onVulnerabilityInsight'),
      [
        ['onVulnerabilityInsight', false],
        ['onVulnerabilityInsight', true],
      ],
    );
    assert.equal(c.count('reload') + c.count('check') + c.count('apply'), 0);
    assert.equal(c.count('invalid'), 0);
  });
  test(`pages/${format}/fallback compiler recovery reloads once without wrong-hash check`, async () => {
    const c = pagesClient(format);
    c.send({
      type: 'sync',
      hash: 'main-error',
      errors: ['HMR_FIXTURE_SYNTAX'],
    });
    assert.equal(c.count('reload'), 0);
    assert.equal(c.count('check'), 0);
    assert.equal(c.count('onBuildError'), 1);
    assert.equal(
      c.count('error'),
      1,
      'genuine compilation errors stay visible',
    );
    c.send({ type: 'built', hash: 'H2' });
    c.send({ type: 'built', hash: 'H2' });
    c.send({ type: 'sync', hash: 'H3' });
    await settle();
    assert.equal(c.count('reload'), 1);
    assert.equal(c.count('check'), 0);
    assert.equal(c.count('apply'), 0);
    assert.equal(c.count('invalid'), 0);
  });
  test(`pages/${format}/original fallback discriminator attempts wrong-compiler check`, async () => {
    const c = pagesClient(format, { original: true, nullCheck: true });
    c.send({
      type: 'sync',
      hash: 'main-error',
      errors: ['HMR_FIXTURE_SYNTAX'],
    });
    c.send({ type: 'built', hash: 'H2' });
    await settle();
    assert.equal(c.count('check'), 1);
    assert.equal(c.count('reload'), 1);
  });
  test(`pages/${format}/fallback warnings and existing hash/status gates remain effective`, async () => {
    const c = pagesClient(format);
    c.send({
      type: 'sync',
      hash: 'main-error',
      errors: ['HMR_FIXTURE_SYNTAX'],
    });
    c.send({ type: 'built', hash: 'H0' });
    assert.equal(c.count('reload'), 0, 'matching runtime hash does not reload');
    c.setStatus('apply');
    c.send({ type: 'built', hash: 'H2' });
    assert.equal(c.count('reload'), 0, 'busy webpack runtime stays gated');
    c.setStatus('idle');
    c.send({ type: 'built', hash: 'H2', warnings: ['honest-warning'] });
    await settle();
    assert.equal(c.count('warn'), 1);
    assert.equal(c.count('reload'), 1);
    assert.equal(c.count('check'), 0);
  });
  for (const [label, options] of [
    [
      'normal-error-page',
      { scripts: ['http://fixture/_next/static/chunks/webpack.js'] },
    ],
    ['normal-page', { page: '/' }],
    [
      'query-is-not-runtime-identity',
      {
        scripts: [
          'http://fixture/_next/static/chunks/webpack.js?next=/_next/static/chunks/fallback/webpack.js',
        ],
      },
    ],
    ['production', { production: true }],
  ])
    test(`pages/${format}/${label}: legitimate update and reconnect SYNC still work`, async () => {
      const c = pagesClient(format, options);
      c.send({ type: 'sync', hash: 'H0' });
      c.send({ type: 'built', hash: 'H2' });
      await settle();
      assert.equal(c.count('check'), 1);
      assert.equal(c.count('apply'), 1);
      assert.equal(c.count('reload'), 0);
      c.send({ type: 'sync', hash: 'H2' });
      await settle();
      assert.equal(
        c.count('check'),
        1,
        'equal reconnect snapshot does not create update',
      );
      assert.equal(c.count('reload'), 0);
    });
  for (const option of ['nullCheck', 'checkError'])
    test(`pages/${format}/normal ${option} remains an honest reload`, async () => {
      const c = pagesClient(format, { scripts: [], [option]: true });
      c.send({ type: 'sync', hash: 'H0' });
      c.send({ type: 'built', hash: 'H2' });
      await settle();
      assert.equal(c.count('check'), 1);
      assert.equal(c.count('reload'), 1);
    });
}

test('client/sync-and-recovery (actual decision modules, mocked transport/DOM/runtime)', async (t) => {
  await t.test(
    'first/equal SYNC no reload, changed later SYNC still reloads without disconnect',
    () => {
      const c = client();
      c.send({ type: 'sync', hash: 'H1' });
      c.send({ type: 'sync', hash: 'H1' });
      assert.equal(c.reloads(), 0);
      c.send({ type: 'sync', hash: 'H2' });
      assert.equal(c.reloads(), 1);
      c.send({ type: 'sync', hash: 'H3' });
      assert.equal(c.reloads(), 1);
    },
  );
  await t.test(
    'BUILT hot apply leaves outer SYNC hash; H1 -> H2 -> reconnect H2 conservatively reloads',
    async () => {
      const c = client();
      c.send({ type: 'sync', hash: 'H1' });
      c.send({ type: 'built', hash: 'H2' });
      await settle();
      assert.ok(c.calls.some(([name]) => name === 'apply'));
      assert.equal(c.reloads(), 0);
      c.reconnect();
      c.send({ type: 'sync', hash: 'H2' });
      assert.equal(c.reloads(), 1);
    },
  );
  await t.test(
    'same-hash real mocked reconnect still receives subsequent updates',
    async () => {
      const c = client();
      c.send({ type: 'sync', hash: 'H1' });
      c.reconnect();
      c.send({ type: 'sync', hash: 'H1' });
      assert.equal(c.reloads(), 0);
      c.send({ type: 'built', hash: 'H2' });
      await settle();
      assert.ok(c.calls.some(([name]) => name === 'onRefresh'));
    },
  );
  await t.test(
    'session IDs, malformed messages and disconnect duplicate protection',
    () => {
      const c = client();
      c.sockets[0].onmessage({ data: 'not-json' });
      assert.equal(c.events.filter(([name]) => name === 'invalid').length, 1);
      c.send({ type: 'turbopack-connected', data: { sessionId: 'one' } });
      c.send({ type: 'turbopack-connected', data: { sessionId: 'one' } });
      assert.equal(c.reloads(), 0);
      c.send({ type: 'turbopack-connected', data: { sessionId: 'two' } });
      assert.equal(c.reloads(), 1);
      const d = client();
      d.sockets[0].onerror();
      assert.equal(d.sockets[0].onclose, null);
      assert.equal(d.sockets[0].onerror, null);
      assert.equal(d.timers.size, 1);
      d.listeners.window.online();
      assert.equal(d.sockets.length, 2);
      assert.equal(d.timers.size, 0);
      d.sockets.at(-1).close();
      d.listeners.document.visibilitychange();
      assert.equal(d.sockets.length, 3);
    },
  );
  await t.test('retry exhaustion is finite and still reloads', () => {
    const c = client();
    const limit =
      require('next/dist/lib/constants').WEB_SOCKET_MAX_RECONNECTIONS;
    for (let index = 0; index <= limit; index++) {
      c.sockets.at(-1).onclose();
      if (index < limit) {
        const [id, timer] = [...c.timers].at(-1);
        assert.equal(timer.delay, index >= 5 ? 5000 : 1000);
        c.timers.delete(id);
        timer.fn();
      }
    }
    assert.equal(c.reloads(), 1);
  });
});
test('client/update-errors-metadata (synthetic dispatcher/DOM effects)', async (t) => {
  for (const option of [
    'nullCheck',
    'nullApply',
    'checkError',
    'applyError',
    'runtimeError',
  ])
    await t.test(`needed reload: ${option}`, async () => {
      const c = client({ [option]: true });
      c.runtimeError.hadRuntimeError = option === 'runtimeError';
      c.send({ type: 'sync', hash: 'H1' });
      c.send({ type: 'built', hash: 'H2' });
      await settle();
      assert.equal(c.reloads(), 1);
    });
  await t.test('server-component refresh versus error-document reload', () => {
    const c = client();
    c.send({ type: 'serverComponentChanges' });
    assert.ok(c.calls.some(([name]) => name === 'hmrRefresh'));
    assert.equal(c.reloads(), 0);
    c.document.documentElement.id = '__next_error__';
    c.send({ type: 'serverComponentChanges' });
    assert.equal(c.reloads(), 1);
  });
  await t.test(
    'errors/warnings/build recovery and snapshot/independent metadata',
    async () => {
      const c = client();
      c.send({
        type: 'sync',
        hash: 'H1',
        errors: ['synthetic-compile-error'],
        versionInfo: { installed: '16.4.1' },
        hasVulnerabilityInsight: false,
        debug: { devtoolsFrontendUrl: 'debug' },
        devIndicator: { disabledUntil: 10 },
        devToolsConfig: { theme: 'light' },
        requestInsights: { synthetic: true },
      });
      assert.ok(
        c.calls.some(
          ([name, value]) =>
            name === 'onBuildError' && value === 'synthetic-compile-error',
        ),
      );
      for (const name of [
        'onVersionInfo',
        'onVulnerabilityInsight',
        'onDebugInfo',
        'onDevIndicator',
        'onDevToolsConfig',
        'onRequestInsightsSnapshot',
      ])
        assert.ok(c.calls.some(([call]) => name === call));
      c.send({ type: 'vulnerabilityInsight', hasVulnerabilityInsight: true });
      assert.ok(
        c.calls.some(
          ([name, value]) =>
            name === 'onVulnerabilityInsight' && value === true,
        ),
      );
      c.send({ type: 'devtoolsConfig', data: { theme: 'dark' } });
      c.send({ type: 'requestInsightsUpdate', insight: { id: 'update' } });
      assert.ok(c.calls.some(([name]) => name === 'onRequestInsightsUpdate'));
      c.send({ type: 'building' });
      c.send({ type: 'built', hash: 'H2', warnings: ['synthetic-warning'] });
      await settle();
      assert.ok(c.calls.some(([name]) => name === 'onBuildOk'));
      assert.ok(
        c.events.some(
          ([name, value]) => name === 'warn' && value === 'synthetic-warning',
        ),
      );
      assert.equal(c.reloads(), 0);
    },
  );
});

test('ownership/fixture-contract', async (t) => {
  for (const pair of [
    [undefined, undefined],
    ['task', 'bad'],
    ['../foreign', 'a'.repeat(32)],
  ])
    assert.throws(() => validateIdentity(...pair));
  const expected = {
    task: 'node-hmr-controls',
    nonce: 'a'.repeat(32),
    generation: 'b'.repeat(32),
  };
  const bound = { event: 'bound', ...expected, host: '127.0.0.1', port: 3151 };
  assert.equal(controlFrame(bound, expected, 'launching', 'receipt'), bound);
  for (const frame of [
    { ...bound, task: 'foreign' },
    { ...bound, nonce: 'bad' },
    { ...bound, generation: 'c'.repeat(32) },
    { ...bound, port: 3152 },
    { ...bound, arbitraryPath: '/' },
  ])
    assert.throws(() => controlFrame(frame, expected, 'launching', 'receipt'));
  assert.throws(
    () => controlFrame(bound, expected, 'armed', 'receipt'),
    /phase/,
  );
  assert.throws(() =>
    controlFrame(
      { event: 'close-hmr', ...expected },
      expected,
      'armed',
      'command',
    ),
  );
  await t.test('malformed/oversize private frames fail closed', async () => {
    const { PassThrough } = require('node:stream');
    const { readFrames } = await import('../e2e/cache-fixture-control.mjs');
    for (const value of ['{bad}\n', 'x'.repeat(4097)]) {
      const stream = new PassThrough();
      const errors = [];
      readFrames(
        stream,
        () => assert.fail('must not admit malformed frame'),
        (error) => errors.push(error),
      );
      stream.write(value);
      assert.equal(errors.length, 1);
    }
  });
  await t.test(
    'generated source admission/unlisted edit/stale preimage/foreign owner/symlink',
    () => {
      const run = createFixtureRun(frontend, 'node-hmr-controls');
      assert.equal(admitRun(frontend, run.task, run.nonce), run.dir);
      assert.throws(() => run.mutate('../../app/page.tsx'), /Unlisted/);
      run.mutate('counter-v2');
      fs.appendFileSync(path.join(run.dir, 'app/counter.jsx'), '//foreign');
      assert.throws(() => run.mutate('counter-v3'), /Stale/);
      const other = createFixtureRun(frontend, 'node-hmr-controls');
      assert.throws(
        () => admitRun(frontend, 'foreign-task', other.nonce),
        /Foreign/,
      );
      const file = path.join(other.dir, 'app/page.jsx');
      fs.renameSync(file, `${file}.retained`);
      fs.symlinkSync(`${file}.retained`, file);
      assert.throws(() => other.mutate('server-v2'), /symlink/);
    },
  );
});

test('ownership/synthetic-child-contract (no real server/browser or foreign stop)', async (t) => {
  const { EventEmitter } = require('node:events');
  const { PassThrough, Writable } = require('node:stream');
  const shared = await import('../e2e/cache-fixture-control.mjs');
  function peer(run, behavior = 'normal') {
    const child = new EventEmitter();
    child.exitCode = null;
    child.signalCode = null;
    const output = new PassThrough();
    const identity = {
      task: run.task,
      nonce: run.nonce,
      generation: 'd'.repeat(32),
    };
    const emit = (frame) => output.write(JSON.stringify(frame) + '\n');
    const exit = (code) => {
      child.exitCode = code;
      child.emit('exit', code, null);
    };
    child.stdin = new Writable({
      write(chunk, _encoding, done) {
        for (const line of chunk.toString().trim().split('\n')) {
          const frame = JSON.parse(line);
          setImmediate(() => {
            if (frame.event === 'arm') emit({ event: 'armed', ...identity });
            if (frame.event === 'close-hmr' && behavior !== 'missing-close')
              emit({
                event: 'socket-closed',
                ...identity,
                socketId: frame.socketId,
              });
            if (frame.event === 'shutdown') {
              emit({ event: 'stopped', ...identity });
              setImmediate(() => exit(behavior === 'bad-shutdown' ? 1 : 0));
            }
          });
        }
        done();
      },
    });
    child.stdio = [child.stdin, null, null, output];
    child.kill = () => assert.fail('No PID/process kill is permitted');
    return { child, emit, exit, identity };
  }
  async function setup(behavior) {
    const run = createFixtureRun(frontend, 'node-owned-peer');
    const p = peer(run, behavior);
    let launches = 0;
    const owner = await ownFixture(run, {
      spawn(executable, args, options) {
        launches++;
        assert.equal(executable, process.execPath);
        assert.deepEqual(args, [
          path.join(frontend, 'hmr-tests/server.cjs'),
          run.task,
          run.nonce,
        ]);
        assert.equal(options.cwd, frontend);
        assert.equal(options.stdio[0], 'pipe');
        assert.equal(options.stdio[3], 'pipe');
        setImmediate(() => {
          if (behavior === 'occupied') {
            p.exit(1);
            return;
          }
          p.emit({
            event: 'bound',
            ...p.identity,
            host: '127.0.0.1',
            port: 3151,
          });
        });
        return p.child;
      },
      waitFor(promise, milliseconds, label) {
        // The synthetic timeout must observe the underlying promise just as
        // the real Promise.race-based timer does (including later rejection).
        promise.catch(() => {});
        if (behavior === 'missing-close' && label.includes('close-hmr'))
          return Promise.reject(
            new Error('Synthetic missing socket-close receipt'),
          );
        return shared.waitFor(promise, milliseconds, label);
      },
    });
    return { owner, ...p, launches: () => launches };
  }
  await t.test(
    'bound -> armed -> exact socket close -> stopped receipt and clean exit',
    async () => {
      const p = await setup('normal');
      await p.owner.start();
      await p.owner.arm();
      await p.owner.closeHmr('owned-socket');
      await p.owner.stop();
      assert.equal(p.launches(), 1);
      assert.deepEqual(
        p.owner.records
          .filter(({ event }) =>
            ['bound', 'armed', 'socket-closed', 'stopped', 'exit'].includes(
              event,
            ),
          )
          .map(({ event }) => event),
        ['bound', 'armed', 'socket-closed', 'stopped', 'exit'],
      );
    },
  );
  await t.test(
    'occupied3151 peer exit cannot trigger fallback, reuse or a second launch',
    async () => {
      const p = await setup('occupied');
      await assert.rejects(p.owner.start(), /Unexpected fixture child exit/);
      assert.equal(p.launches(), 1);
    },
  );
  await t.test(
    'duplicate bound/out-of-order receipt invalidates exact owner',
    async () => {
      const p = await setup('normal');
      await p.owner.start();
      p.emit({ event: 'bound', ...p.identity, host: '127.0.0.1', port: 3151 });
      await assert.rejects(p.owner.arm(), /phase/);
      p.exit(1);
    },
  );
  await t.test('unexpected child exit invalidates later commands', async () => {
    const p = await setup('normal');
    await p.owner.start();
    await p.owner.arm();
    p.exit(1);
    await assert.rejects(
      p.owner.closeHmr('owned-socket'),
      /Unexpected fixture child exit/,
    );
  });
  await t.test(
    'missing socket-close receipt cannot report success',
    async () => {
      const p = await setup('missing-close');
      await p.owner.start();
      await p.owner.arm();
      await assert.rejects(
        p.owner.closeHmr('owned-socket'),
        /missing socket-close/,
      );
      p.exit(1);
    },
  );
  await t.test(
    'failed shutdown remains failed even after stopped receipt',
    async () => {
      const p = await setup('bad-shutdown');
      await p.owner.start();
      await p.owner.arm();
      await assert.rejects(p.owner.stop(), /Unclean fixture shutdown/);
    },
  );
});

test('runner/serial-failure-contract: expected child exit1 / 1failed / 1skipped / 0retry / no marker', () => {
  const dir = path.join(
    frontend,
    '.hmr-runs/serial-contract',
    randomBytes(16).toString('hex'),
  );
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(
    path.join(dir, 'serial.config.cjs'),
    "module.exports={testDir:'.',testMatch:'serial.spec.cjs',respectGitIgnore:false,workers:1,retries:0,outputDir:'./results'};\n",
  );
  fs.writeFileSync(
    path.join(dir, 'serial.spec.cjs'),
    "const {test}=require('@playwright/test');const fs=require('node:fs');const path=require('node:path');test.describe('serial-contract',()=>{test.describe.configure({mode:'serial'});test('intentional-failure',()=>{throw new Error('HMR_SERIAL_CONTRACT')});test('must-skip',()=>{fs.writeFileSync(path.join(__dirname,'must-not-run.marker'),'bad')})});\n",
  );
  const args = [
    path.join(frontend, 'node_modules/playwright/cli.js'),
    'test',
    '--config',
    path.join(dir, 'serial.config.cjs'),
    '--reporter=json',
  ];
  const child = spawnSync(process.execPath, args, {
    cwd: frontend,
    encoding: 'utf8',
    timeout: 30000,
  });
  fs.writeFileSync(path.join(dir, 'stdout.json'), child.stdout ?? '');
  fs.writeFileSync(path.join(dir, 'stderr.log'), child.stderr ?? '');
  fs.writeFileSync(
    path.join(dir, 'exit.json'),
    JSON.stringify({
      command: [process.execPath, ...args],
      exit: child.status,
      signal: child.signal,
      error: String(child.error ?? ''),
      classification:
        'expected negative runner contract, not browser/product acceptance',
    }),
  );
  assert.equal(child.error, undefined);
  assert.equal(child.status, 1);
  assert.equal(child.signal, null);
  const report = JSON.parse(child.stdout);
  assert.equal(report.stats.expected, 0);
  assert.equal(report.stats.unexpected, 1);
  assert.equal(report.stats.skipped, 1);
  assert.equal(report.stats.flaky, 0);
  assert.equal(report.errors.length, 0);
  const results = [];
  function collect(suites) {
    for (const suite of suites) {
      for (const spec of suite.specs ?? [])
        for (const item of spec.tests) results.push(...item.results);
      collect(suite.suites ?? []);
    }
  }
  collect(report.suites);
  assert.deepEqual(
    results.map(({ status, retry }) => ({ status, retry })),
    [
      { status: 'failed', retry: 0 },
      { status: 'skipped', retry: 0 },
    ],
  );
  assert.match(JSON.stringify(results[0].errors), /HMR_SERIAL_CONTRACT/);
  assert.equal(fs.existsSync(path.join(dir, 'must-not-run.marker')), false);
});
