'use strict';

// Imports are inert. Actual Next modules are evaluated only by explicit loaders.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter, once } = require('node:events');
const { spawn } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const { hash, checkPatched } = require('../scripts/patch-next-hmr.cjs');

const launchReceipts = new WeakMap();
const recorderKey = Symbol.for('daemon.hmr.launch-recorder');

function parseLaunchLine(line) {
  const plain = line.replace(/\u001b\[[0-9;]*m/g, '');
  const match = plain.match(
    /pw:browser\s+<launching> (\/\S+) (.*?)(?:\s+\+\d+(?:ms|s|m))?\s*$/,
  );
  if (!match) throw new Error('Missing/unparseable Playwright launch line');
  return { executablePath: match[1], observedArgumentsText: match[2] };
}

// Explicit installation only: importing this module never observes or launches.
// DEBUG=pw:browser is enabled before Node startup by the HMR command. This is
// launcher-owned logging, not another query for refused browser-process data.
function installLaunchRecorder(playwright = require('playwright-core')) {
  const type = playwright.chromium;
  if (type[recorderKey]) return;
  type[recorderKey] = true;
  const originalLaunch = type.launch;
  const originalWrite = process.stderr.write;
  let active;
  process.stderr.write = function (chunk, ...args) {
    if (active) {
      const text = Buffer.isBuffer(chunk)
        ? chunk.toString('utf8')
        : String(chunk);
      fs.appendFileSync(active.logPath, text);
      active.buffer += text;
      while (active.buffer.includes('\n')) {
        const end = active.buffer.indexOf('\n');
        const line = active.buffer.slice(0, end);
        active.buffer = active.buffer.slice(end + 1);
        const plain = line.replace(/\u001b\[[0-9;]*m/g, '');
        if (/pw:browser\s+<launching>/.test(plain)) {
          if (active.actual)
            throw new Error('Ambiguous multiple browser launches');
          const actual = parseLaunchLine(line);
          actual.resolvedExecutablePath = fs.realpathSync(
            actual.executablePath,
          );
          if (!fs.statSync(actual.resolvedExecutablePath).isFile())
            throw new Error('Browser executable is not a file');
          actual.sha256BeforeSpawn = hash(
            fs.readFileSync(actual.resolvedExecutablePath),
          );
          active.actual = actual;
        }
        const pid = plain.match(/pw:browser\s+<launched> pid=(\d+)/);
        if (pid) {
          if (active.browserPid)
            throw new Error('Ambiguous browser launch PID');
          active.browserPid = Number(pid[1]);
        }
      }
    }
    // Forward every byte, callback and return value; never suppress diagnostics.
    return originalWrite.call(this, chunk, ...args);
  };
  type.launch = async function (...args) {
    if (active) throw new Error('Concurrent browser launches in one recorder');
    const frontend = path.resolve(__dirname, '..');
    const parent = realPath(frontend, '.hmr-runs', 'directory');
    const dir = path.join(parent, `launch-${randomBytes(16).toString('hex')}`);
    fs.mkdirSync(dir, { mode: 0o700 });
    const options = args[0] ?? {};
    const receipt = {
      method:
        'prospective API receipt + actual Playwright pw:browser launch log',
      observerPid: process.pid,
      dir,
      logPath: path.join(dir, 'launch.log'),
      started: Date.now(),
      declaredExecutablePath: type.executablePath(),
      requested: Object.fromEntries(
        ['executablePath', 'channel', 'headless', 'args', 'ignoreDefaultArgs']
          .filter((key) => Object.hasOwn(options, key))
          .map((key) => [key, options[key]]),
      ),
      limitations:
        'Log-rendered launcher arguments, not CDP/process argv or loaded-memory attestation; file hashes bracket launch. Synchronous recording/hashing has timing cost. No flags changed.',
      buffer: '',
    };
    fs.writeFileSync(
      path.join(dir, 'prospective.json'),
      JSON.stringify(receipt, null, 2),
      { flag: 'wx' },
    );
    active = receipt;
    const start = performance.now();
    let launchedBrowser;
    try {
      const browser = await originalLaunch.apply(this, args);
      launchedBrowser = browser;
      receipt.version = browser.version();
      receipt.launchObservationMs = performance.now() - start;
      if (!receipt.actual || !receipt.browserPid)
        throw new Error('Missing actual launcher path/arguments/PID receipt');
      receipt.actual.sha256AfterLaunch = hash(
        fs.readFileSync(receipt.actual.resolvedExecutablePath),
      );
      if (receipt.actual.sha256AfterLaunch !== receipt.actual.sha256BeforeSpawn)
        throw new Error('Browser executable changed across launch');
      receipt.launchLogSha256 = hash(fs.readFileSync(receipt.logPath));
      fs.writeFileSync(
        path.join(dir, 'launched.json'),
        JSON.stringify(receipt, null, 2),
        { flag: 'wx' },
      );
      launchReceipts.set(browser, receipt);
      browser.once('disconnected', () => {
        receipt.disconnected = Date.now();
        fs.writeFileSync(
          path.join(dir, 'disconnected.json'),
          JSON.stringify(receipt, null, 2),
          { flag: 'wx' },
        );
        active = undefined;
      });
      return browser;
    } catch (error) {
      receipt.error = String(error);
      if (launchedBrowser) {
        try {
          await launchedBrowser.close();
        } catch (closeError) {
          receipt.closeError = String(closeError);
        }
      }
      fs.writeFileSync(
        path.join(dir, 'failure.json'),
        JSON.stringify(receipt, null, 2),
        { flag: 'wx' },
      );
      active = undefined;
      throw error;
    }
  };
}

function browserLaunchReceipt(browser) {
  const receipt = launchReceipts.get(browser);
  if (
    !receipt?.actual ||
    !receipt.browserPid ||
    receipt.version !== browser.version()
  )
    throw new Error('Browser object has no matching launcher receipt');
  return receipt;
}

function evaluate(
  source,
  filename,
  table,
  globals = {},
  module = { exports: {} },
) {
  const context = vm.createContext({
    ...globals,
    module,
    exports: module.exports,
    require(name) {
      if (!Object.hasOwn(table, name))
        throw new Error(`Unexpected require ${name} in ${filename}`);
      return table[name];
    },
  });
  new vm.Script(source.toString(), { filename }).runInContext(context);
  return { exports: module.exports, context };
}

function loadMiddleware({ format, sourceBytes, collaborators }) {
  if (!['cjs', 'esm'].includes(format))
    throw new Error('Unknown middleware format');
  const source =
    format === 'esm'
      ? require('typescript').transpileModule(sourceBytes.toString(), {
          compilerOptions: {
            module: require('typescript').ModuleKind.CommonJS,
            target: require('typescript').ScriptTarget.ES2020,
          },
        }).outputText
      : sourceBytes;
  return evaluate(
    source,
    `actual-hot-middleware.${format}.js`,
    collaborators.table,
    {
      Date: collaborators.Date ?? Date,
      process: { env: collaborators.env ?? {} },
    },
  ).exports.WebpackHotMiddleware;
}

function loadClient({
  moduleBytes,
  hotRuntime,
  clock,
  socketFactory,
  dispatcher,
  router,
}) {
  const messages = require('next/dist/server/dev/hot-reloader-types');
  const runtimeError = { hadRuntimeError: false };
  const events = [];
  const listeners = { window: {}, document: {} };
  const window = {
    WebSocket: socketFactory,
    location: {
      href: 'http://fixture/',
      reload: () => events.push(['reload']),
    },
    console: { log: (...args) => events.push(['log', ...args]) },
    addEventListener: (type, callback) => {
      listeners.window[type] = callback;
    },
  };
  const document = {
    visibilityState: 'visible',
    documentElement: { id: '' },
    addEventListener: (type, callback) => {
      listeners.document[type] = callback;
    },
  };
  const shared = {
    reportInvalidHmrMessage: (...args) => events.push(['invalid', ...args]),
    REACT_REFRESH_FULL_RELOAD: 'full-reload',
    REACT_REFRESH_FULL_RELOAD_FROM_ERROR: 'runtime-full-reload',
  };
  const table = {
    react: { startTransition: (fn) => fn() },
    'react/jsx-runtime': {},
    '@swc/helpers/_/_interop_require_default': {
      _: (value) => ({ default: value }),
    },
    'next/dist/compiled/strip-ansi': (value) => value,
    '../../../../shared/lib/format-webpack-messages': (value) => value,
    '../shared': shared,
    'next/dist/compiled/next-devtools': { dispatcher },
    '../../../../next-devtools/userspace/app/errors/replay-ssr-only-errors': {},
    '../../../../next-devtools/userspace/app/app-dev-overlay-error-boundary':
      {},
    '../../../../next-devtools/userspace/app/errors/use-error-handler': {},
    '../../runtime-error-handler': { RuntimeErrorHandler: runtimeError },
    '../../../../server/dev/hot-reloader-types': messages,
    '../../../components/navigation-untracked': {},
    '../../report-hmr-latency': (...args) => events.push(['latency', ...args]),
    '../turbopack-hot-reloader-common': {},
    '../../../components/app-router-instance': {
      publicAppRouterInstance: router,
    },
    '../../../../shared/lib/invariant-error': { InvariantError: Error },
    '../../../../next-devtools/shared/forward-logs-shared': {},
    '../../debug-channel': {},
    'react-server-dom-webpack/client': {},
    '../../../app-find-source-map-url': {},
    '../../../../shared/lib/app-router-context.shared-runtime': {},
    '../get-socket-url': { getSocketUrl: () => 'ws://fixture' },
    '../../../../next-devtools/userspace/app/forward-logs': {
      logQueue: { onSocketReady: () => {} },
    },
    '../../../../lib/constants': require('next/dist/lib/constants'),
  };
  // Lazy circular exports link the two actual modules, not surrogate decisions.
  const socketModule = { exports: {} };
  const appModule = { exports: {}, hot: hotRuntime };
  table['./web-socket'] = socketModule.exports;
  table['./hot-reloader-app'] = appModule.exports;
  const globals = {
    window,
    document,
    self: { __next_r: 'synthetic-document' },
    WebSocket: socketFactory,
    console: {
      log: (...args) => events.push(['log', ...args]),
      warn: (...args) => events.push(['warn', ...args]),
      error: (...args) => events.push(['error', ...args]),
    },
    process: { env: {} },
    TextDecoder,
    ArrayBuffer,
    DataView,
    Uint8Array,
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    Date,
    Math,
  };
  const app = evaluate(
    moduleBytes.app,
    'actual-hot-reloader-app.js',
    table,
    { ...globals, __webpack_hash__: hotRuntime.hash },
    appModule,
  );
  const socket = evaluate(
    moduleBytes.socket,
    'actual-web-socket.js',
    table,
    globals,
    socketModule,
  );
  return {
    app: app.exports,
    socket: socket.exports,
    runtimeError,
    events,
    listeners,
    document,
    setHash(value) {
      app.context.__webpack_hash__ = value;
    },
  };
}

const templateFiles = [
  'package.json',
  'next.config.mjs',
  'app/layout.jsx',
  'app/page.jsx',
  'app/counter.jsx',
  'app/lazy/page.jsx',
];
function realPath(root, relative, kind = 'file') {
  if (fs.realpathSync(root) !== path.resolve(root))
    throw new Error('Non-real fixture root');
  let current = root;
  for (const component of relative.split('/')) {
    if (!component || component === '..' || component === '.')
      throw new Error('Invalid fixture path');
    current = path.join(current, component);
    const stat = fs.lstatSync(current);
    if (stat.isSymbolicLink() || stat.uid !== process.getuid())
      throw new Error('Foreign/symlink fixture path');
    if (current !== path.join(root, relative) && !stat.isDirectory())
      throw new Error('Invalid fixture parent');
  }
  if (
    !(kind === 'file'
      ? fs.statSync(current).isFile()
      : fs.statSync(current).isDirectory())
  )
    throw new Error('Invalid fixture kind');
  return current;
}
function validateIdentity(task, nonce) {
  if (
    !/^[\w.-]{1,100}$/.test(task ?? '') ||
    !/^[a-f0-9]{32}$/.test(nonce ?? '')
  )
    throw new Error('Invalid HMR fixture task/nonce');
}
function admitRun(frontendRoot, task, nonce) {
  validateIdentity(task, nonce);
  const dir = realPath(frontendRoot, `.hmr-runs/fixture-${nonce}`, 'directory');
  const owner = JSON.parse(
    fs.readFileSync(realPath(dir, 'owner.json'), 'utf8'),
  );
  if (
    owner.task !== task ||
    owner.nonce !== nonce ||
    owner.frontendRoot !== frontendRoot
  )
    throw new Error('Foreign fixture owner');
  return dir;
}

function createFixtureRun(frontendRoot, task) {
  frontendRoot = path.resolve(frontendRoot);
  const nonce = randomBytes(16).toString('hex');
  validateIdentity(task, nonce);
  checkPatched(frontendRoot);
  const parent = realPath(frontendRoot, '.hmr-runs', 'directory');
  const dir = path.join(parent, `fixture-${nonce}`);
  fs.mkdirSync(dir, { mode: 0o700 });
  const templateManifest = {};
  const pristine = {};
  for (const relative of templateFiles) {
    const input = realPath(
      frontendRoot,
      `hmr-tests/fixture-template/${relative}`,
    );
    const bytes = fs.readFileSync(input);
    const output = path.join(dir, relative);
    fs.mkdirSync(path.dirname(output), { recursive: true });
    fs.writeFileSync(output, bytes, {
      flag: 'wx',
      mode: fs.statSync(input).mode & 0o777,
    });
    templateManifest[relative] = {
      hash: hash(bytes),
      mode: fs.statSync(input).mode & 0o777,
    };
    pristine[relative] = bytes.toString();
  }
  fs.writeFileSync(
    path.join(dir, 'owner.json'),
    JSON.stringify({ frontendRoot, task, nonce, templateManifest }),
    { flag: 'wx', mode: 0o600 },
  );
  const records = [];
  const latest = Object.fromEntries(
    Object.entries(templateManifest).map(([key, value]) => [key, value.hash]),
  );
  const counter = (version) =>
    pristine['app/counter.jsx'].replaceAll('client-v1', version);
  const actions = {
    'counter-v2': ['app/counter.jsx', counter('client-v2')],
    'counter-v3': ['app/counter.jsx', counter('client-v3')],
    'counter-restart-v4': ['app/counter.jsx', counter('client-v4')],
    'counter-syntax-error': [
      'app/counter.jsx',
      counter('client-healthy') + '\nexport const HMR_FIXTURE_SYNTAX = ;\n',
    ],
    'counter-healthy': ['app/counter.jsx', counter('client-healthy')],
    'counter-render-error': [
      'app/counter.jsx',
      counter('client-render').replace(
        "const renderPhase = 'healthy';",
        "const renderPhase = 'error';",
      ),
    ],
    'server-v2': [
      'app/page.jsx',
      pristine['app/page.jsx'].replace('server-v1', 'server-v2'),
    ],
    'lazy-v2': [
      'app/lazy/page.jsx',
      pristine['app/lazy/page.jsx'].replace('lazy-v1', 'lazy-v2'),
    ],
  };
  return {
    frontendRoot,
    task,
    nonce,
    dir,
    templateManifest,
    records,
    mutate(action) {
      admitRun(frontendRoot, task, nonce);
      if (!Object.hasOwn(actions, action))
        throw new Error('Unlisted fixture edit');
      const [relative, text] = actions[action];
      const file = realPath(dir, relative);
      const before = hash(fs.readFileSync(file));
      if (before !== latest[relative])
        throw new Error('Stale fixture preimage');
      fs.writeFileSync(file, text);
      latest[relative] = hash(Buffer.from(text));
      const record = {
        action,
        relative,
        before,
        after: latest[relative],
        at: Date.now(),
      };
      records.push(record);
      fs.appendFileSync(
        path.join(dir, 'mutations.jsonl'),
        JSON.stringify(record) + '\n',
      );
      return record;
    },
  };
}

function controlFrame(frame, expected, phase, direction) {
  const extras =
    frame.event === 'close-hmr' || frame.event === 'socket-closed'
      ? ['socketId']
      : [];
  const keys = ['event', 'task', 'nonce', 'generation', ...extras];
  if (frame.event === 'bound') keys.push('host', 'port');
  if (JSON.stringify(Object.keys(frame).sort()) !== JSON.stringify(keys.sort()))
    throw new Error('Unexpected control fields');
  if (
    frame.task !== expected.task ||
    frame.nonce !== expected.nonce ||
    !/^[a-f0-9]{32}$/.test(frame.generation ?? '') ||
    (expected.generation && frame.generation !== expected.generation)
  )
    throw new Error('Control identity mismatch');
  if (
    extras.length &&
    (typeof frame.socketId !== 'string' ||
      !frame.socketId ||
      frame.socketId.length > 200)
  )
    throw new Error('Invalid socket ID');
  if (
    frame.event === 'bound' &&
    (frame.host !== '127.0.0.1' || frame.port !== 3151)
  )
    throw new Error('Unexpected bind identity');
  const allowed =
    direction === 'command'
      ? {
          bound: ['arm', 'shutdown'],
          armed: ['close-hmr', 'shutdown'],
          launching: ['shutdown'],
        }
      : {
          launching: ['bound'],
          arming: ['armed'],
          closing: ['socket-closed'],
          stopping: ['stopped'],
        };
  if (!allowed[phase]?.includes(frame.event))
    throw new Error('Unexpected control phase');
  return frame;
}

async function ownFixture(run, collaborators = {}) {
  const shared = await import('../e2e/cache-fixture-control.mjs');
  const { readFrames, matches } = shared;
  const waitFor = collaborators.waitFor ?? shared.waitFor;
  const launch = collaborators.spawn ?? spawn;
  const records = [];
  let current;
  function record(event, detail = {}) {
    const entry = { event, at: Date.now(), ...detail };
    records.push(entry);
    fs.appendFileSync(
      path.join(run.dir, 'ownership.jsonl'),
      JSON.stringify(entry) + '\n',
    );
  }
  async function start() {
    if (current && !current.stopped)
      throw new Error('Fixture generation still owned/live');
    admitRun(run.frontendRoot, run.task, run.nonce);
    checkPatched(run.frontendRoot);
    const log = fs.openSync(
      path.join(run.dir, `server-${records.length}.log`),
      'ax',
      0o600,
    );
    const child = launch(
      process.execPath,
      [path.join(__dirname, 'server.cjs'), run.task, run.nonce],
      { cwd: run.frontendRoot, stdio: ['pipe', log, log, 'pipe'] },
    );
    fs.closeSync(log);
    const state = {
      child,
      phase: 'launching',
      expected: { task: run.task, nonce: run.nonce },
      failure: null,
      stopped: false,
      pending: null,
    };
    current = state;
    const exited = once(child, 'exit');
    exited.catch(() => {});
    state.exited = exited;
    function fail(error) {
      state.failure ??= error;
      state.pending?.reject(error);
      record('failure', { error: String(error) });
    }
    child.on('error', fail);
    child.stdin.on('error', fail);
    child.on('exit', (code, signal) => {
      record('exit', { generation: state.expected.generation, code, signal });
      if (!['stopping', 'stopped'].includes(state.phase))
        fail(new Error('Unexpected fixture child exit'));
    });
    function receive(frame) {
      controlFrame(frame, state.expected, state.phase, 'receipt');
      if (frame.event === 'bound') state.expected.generation = frame.generation;
      if (
        !matches(
          {
            task: frame.task,
            nonce: frame.nonce,
            generation: frame.generation,
          },
          state.expected,
        )
      )
        throw new Error('Receipt identity mismatch');
      if (frame.event === 'socket-closed' && frame.socketId !== state.socketId)
        throw new Error('Wrong socket-close receipt');
      state.phase =
        frame.event === 'bound'
          ? 'bound'
          : frame.event === 'stopped'
            ? 'stopped'
            : 'armed';
      record(frame.event, frame);
      state.pending?.resolve(frame);
      state.pending = null;
    }
    const bound = new Promise((resolve, reject) => {
      state.pending = { resolve, reject };
    });
    bound.catch(() => {});
    readFrames(child.stdio[3], receive, (error) => {
      if (
        state.phase === 'stopped' &&
        error.message === 'Fixture control pipe closed'
      )
        return;
      fail(error);
    });
    await waitFor(bound, 120000, 'HMR fixture bound/bootstrap');
    return state.expected;
  }
  async function command(event, socketId) {
    const state = current;
    if (
      !state ||
      state.failure ||
      state.child.exitCode !== null ||
      state.child.signalCode !== null
    )
      throw state?.failure ?? new Error('Fixture not live');
    const frame = {
      event,
      ...state.expected,
      ...(socketId === undefined ? {} : { socketId }),
    };
    controlFrame(frame, state.expected, state.phase, 'command');
    state.phase =
      event === 'arm'
        ? 'arming'
        : event === 'shutdown'
          ? 'stopping'
          : 'closing';
    state.socketId = socketId;
    const receipt = new Promise((resolve, reject) => {
      state.pending = { resolve, reject };
    });
    state.child.stdin.write(JSON.stringify(frame) + '\n');
    try {
      return await waitFor(receipt, 10000, `HMR ${event} receipt`);
    } catch (error) {
      state.failure ??= error;
      record('failure', { error: String(error) });
      throw error;
    }
  }
  async function stop() {
    if (!current || current.stopped) return;
    const state = current;
    if (state.child.exitCode !== null || state.child.signalCode !== null)
      throw state.failure ?? new Error('Child exited without stopped receipt');
    // Even a startup failure only authorizes graceful stop of this exact handle.
    if (state.failure) {
      state.phase = 'stopping';
      state.child.stdin.end();
    } else await command('shutdown');
    const [code, signal] = await waitFor(
      state.exited,
      10000,
      'HMR owned child exit',
    );
    if (
      code !== 0 ||
      signal !== null ||
      state.phase !== 'stopped' ||
      state.failure
    )
      throw state.failure ?? new Error('Unclean fixture shutdown');
    state.stopped = true;
  }
  return {
    records,
    start,
    arm: () => command('arm'),
    closeHmr: (id) => command('close-hmr', id),
    stop,
    async restart() {
      await stop();
      await start();
      await command('arm');
    },
  };
}

async function observePage(page, label) {
  const bus = new EventEmitter();
  const records = [];
  const bodies = {};
  const pending = new Set();
  function record(event, detail) {
    const item = { label, event, at: Date.now(), ...detail };
    records.push(item);
    bus.emit('record', item);
  }
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.enable');
  for (const event of [
    'requestWillBeSent',
    'webSocketCreated',
    'webSocketFrameReceived',
    'webSocketFrameSent',
    'webSocketClosed',
    'webSocketFrameError',
  ])
    cdp.on(`Network.${event}`, (detail) => record(`cdp:${event}`, { detail }));
  page.on('request', (request) => {
    if (request.resourceType() === 'document')
      record('document', { url: request.url(), method: request.method() });
  });
  page.on('console', (message) =>
    record('console', {
      type: message.type(),
      text: message.text(),
      location: message.location(),
    }),
  );
  page.on('pageerror', (error) =>
    record('pageerror', { error: String(error) }),
  );
  page.on('requestfailed', (request) =>
    record('requestfailed', { url: request.url(), failure: request.failure() }),
  );
  page.on('websocket', (socket) => {
    const socketId = new URL(socket.url()).searchParams.get('id');
    record('socket', { url: socket.url(), socketId });
    for (const direction of ['framereceived', 'framesent'])
      socket.on(direction, ({ payload }) => {
        let message;
        if (typeof payload === 'string') {
          try {
            message = JSON.parse(payload);
          } catch {
            /* record undecodable frames verbatim */
          }
        }
        record(direction, {
          socketId,
          payload:
            typeof payload === 'string' ? payload : payload.toString('base64'),
          message,
        });
      });
    socket.on('close', () => record('socket-close', { socketId }));
    socket.on('socketerror', (error) =>
      record('socket-error', { socketId, error }),
    );
  });
  page.on('response', (response) => {
    if (!response.url().startsWith('http://127.0.0.1:3151/')) return;
    // Preserve the observed status even if navigation discards the body before
    // CDP can retrieve it. This is metadata, never proof of served body bytes.
    record('response', { url: response.url(), status: response.status() });
    const promise = (async () => {
      try {
        const body = await response.body();
        const digest = hash(body);
        bodies[digest] = body.toString('base64');
        record('served', {
          url: response.url(),
          status: response.status(),
          sha256: digest,
          size: body.length,
        });
      } catch (error) {
        record('body-error', { url: response.url(), error: String(error) });
      }
    })();
    pending.add(promise);
    promise.finally(() => pending.delete(promise));
  });
  return {
    label,
    records,
    bodies,
    async waitFor(predicate, after = 0) {
      const existing = records.slice(after).find(predicate);
      if (existing) return existing;
      const { waitFor } = await import('../e2e/cache-fixture-control.mjs');
      let listener;
      try {
        return await waitFor(
          new Promise((resolve) => {
            listener = (item) => {
              if (predicate(item)) resolve(item);
            };
            bus.on('record', listener);
          }),
          5000,
          `page ${label} event`,
        );
      } finally {
        bus.off('record', listener);
      }
    },
    async flush() {
      await Promise.all([...pending]);
    },
  };
}

module.exports = {
  installLaunchRecorder,
  browserLaunchReceipt,
  parseLaunchLine,
  loadMiddleware,
  loadClient,
  createFixtureRun,
  ownFixture,
  observePage,
  validateIdentity,
  admitRun,
  controlFrame,
};
