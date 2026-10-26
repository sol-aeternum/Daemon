'use strict';

const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { once } = require('node:events');
const { randomBytes } = require('node:crypto');
const {
  admitRun,
  controlFrame,
  validateIdentity,
} = require('./protocol-harness.cjs');
const { checkPatched } = require('../scripts/patch-next-hmr.cjs');

// Fixture resource bounds, not browser deadlines or a process memory limit.
const startupUpgradeLimits = Object.freeze({
  count: 16,
  head: 65536,
  ms: 120000,
});

function createUpgradeGate({ dispatch, failure, record, timers = globalThis }) {
  const pending = new Map();
  let state = 'holding';
  const live = (socket) =>
    !socket.destroyed && !socket.readableEnded && !socket.writableEnded;
  function detach(socket) {
    const entry = pending.get(socket);
    if (!entry) return;
    pending.delete(socket);
    timers.clearTimeout(entry.timer);
    socket.off('close', entry.closed);
    socket.off('error', entry.errored);
    return entry;
  }
  function close() {
    state = 'closed';
    for (const socket of pending.keys()) {
      const entry = detach(socket);
      record('upgrade-cancelled', { url: entry.req.url, reason: 'stopping' });
      socket.destroy();
    }
  }
  function fail(error, socket) {
    close();
    socket?.destroy();
    failure(error);
  }
  function forward(req, socket, head, held) {
    if (state !== 'armed' || !live(socket)) {
      socket.destroy();
      return;
    }
    try {
      // Invoke synchronously after the final state check. Next attaches its WS
      // readers before this promise resolves; only then release our pause.
      const result = dispatch(req, socket, head);
      Promise.resolve(result).then(
        () => {
          if (held && state === 'armed' && live(socket)) socket.resume();
        },
        (error) => fail(error, socket),
      );
    } catch (error) {
      fail(error, socket);
    }
  }
  return {
    get size() {
      return pending.size;
    },
    close,
    arm() {
      if (state !== 'holding') throw new Error('Upgrade gate cannot arm');
      state = 'armed';
      // Detach each entry before invoking any upstream callback. Reentrant
      // failure/stop closes admission and cancels every remaining entry.
      for (const socket of pending.keys()) {
        const entry = detach(socket);
        record('upgrade-released', { url: entry.req.url });
        forward(entry.req, socket, entry.head, true);
      }
      return state === 'armed';
    },
    upgrade(req, socket, head) {
      let url;
      try {
        url = new URL(req.url, 'http://127.0.0.1:3151');
      } catch (error) {
        fail(error, socket);
        return;
      }
      if (state === 'armed') {
        forward(req, socket, head, false);
        return;
      }
      if (state === 'closed' || url.pathname !== '/_next/hmr') {
        record('upgrade-rejected', { url: req.url, state });
        socket.destroy();
        return;
      }
      if (
        pending.has(socket) ||
        pending.size >= startupUpgradeLimits.count ||
        head.length > startupUpgradeLimits.head
      ) {
        record('upgrade-limit-failure', {
          url: req.url,
          headBytes: head.length,
          pending: pending.size,
        });
        fail(new Error('HMR startup upgrade admission limit'), socket);
        return;
      }
      if (!live(socket)) {
        socket.destroy();
        return;
      }
      const entry = { req, head };
      entry.closed = () => {
        if (detach(socket))
          record('upgrade-cancelled', { url: req.url, reason: 'peer-close' });
      };
      entry.errored = (error) => {
        detach(socket);
        fail(error, socket);
      };
      // Timer captures only the socket; detached entries do not retain req/head
      // in a stale timer callback. A paused stream can defer observing peer FIN.
      entry.timer = timers.setTimeout(() => {
        if (!pending.has(socket)) return;
        record('upgrade-expired', { url: pending.get(socket).req.url });
        fail(new Error('HMR startup upgrade expired'), socket);
      }, startupUpgradeLimits.ms);
      pending.set(socket, entry);
      socket.once('close', entry.closed);
      socket.once('error', entry.errored);
      socket.pause();
      record('upgrade-queued', {
        url: req.url,
        headBytes: head.length,
        pending: pending.size,
      });
    },
  };
}

// Cancellation waits for the current startup stage to settle before app.close;
// a late prepare cannot install resources after they were declared closed.
function createStartupSequence(stages) {
  const controller = new AbortController();
  const settled = Promise.resolve().then(async () => {
    for (const stage of stages) {
      if (controller.signal.aborted) return false;
      await stage(controller.signal);
    }
    return !controller.signal.aborted;
  });
  settled.catch(() => {});
  return { settled, cancel: () => controller.abort() };
}

async function main(args) {
  if (args.length !== 2) throw new Error('Expected exact task and nonce');
  const [task, nonce] = args;
  validateIdentity(task, nonce);
  const frontendRoot = path.resolve(__dirname, '..');
  const dir = admitRun(frontendRoot, task, nonce);
  checkPatched(frontendRoot);
  const { readFrames, waitFor } =
    await import('../e2e/cache-fixture-control.mjs');
  const generation = randomBytes(16).toString('hex');
  const identity = { task, nonce, generation };
  const receipts = fs.createWriteStream('', { fd: 3, autoClose: false });
  let phase = 'launching';
  let app;
  let requestHandler;
  let upgradeHandler;
  let stopping;
  let startup;
  const sockets = new Set();
  const hmr = new Map();
  const receipt = (event, extra = {}) =>
    receipts.write(JSON.stringify({ event, ...identity, ...extra }) + '\n');
  const record = (event, detail = {}) =>
    console.log(
      JSON.stringify({ event, ...identity, at: Date.now(), ...detail }),
    );
  const server = http.createServer((req, res) => {
    if (!requestHandler || stopping) {
      res.writeHead(503);
      res.end('Fixture bootstrapping/stopping');
      return;
    }
    Promise.resolve(requestHandler(req, res)).catch((error) => {
      console.error(error);
      process.exitCode = 1;
      res.destroy(error);
    });
  });
  server.on('connection', (socket) => {
    if (phase === 'stopping' || phase === 'stopped') {
      socket.destroy();
      return;
    }
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
  });
  const upgrades = createUpgradeGate({
    failure,
    record,
    dispatch(req, socket, head) {
      const url = new URL(req.url, 'http://127.0.0.1:3151');
      const socketId = url.searchParams.get('id');
      if (phase !== 'armed' || !upgradeHandler)
        throw new Error('Unready HMR dispatch');
      if (url.pathname === '/_next/hmr') {
        // Pages error fallback legitimately omits id. Use the owned socket only
        // as a local tracking key; never add an ID to the upstream request.
        const controlKey = socketId || socket;
        if (hmr.has(controlKey)) throw new Error('Duplicate HMR socket ID');
        hmr.set(controlKey, socket);
        record('hmr-accepted', { socketId, url: req.url });
        socket.on('close', () => {
          if (hmr.get(controlKey) === socket) hmr.delete(controlKey);
          record('hmr-closed', { socketId });
        });
      }
      return upgradeHandler(req, socket, head);
    },
  });
  server.on('upgrade', upgrades.upgrade);
  async function stop() {
    if (stopping) return stopping;
    phase = 'stopping';
    startup?.cancel();
    upgrades.close();
    stopping = Promise.resolve().then(async () => {
      // Include preparation settlement in the existing shutdown deadline. Abort
      // bootstrap fetch, but never close Next concurrently with app.prepare().
      await waitFor(
        (async () => {
          await startup?.settled.catch(() => {});
          const closed = server.listening
            ? new Promise((resolve, reject) =>
                server.close((error) => (error ? reject(error) : resolve())),
              )
            : Promise.resolve();
          const socketClosures = [...sockets].map((socket) =>
            once(socket, 'close'),
          );
          for (const socket of sockets) socket.destroy();
          await Promise.all([closed, ...socketClosures, app?.close()]);
        })(),
        10000,
        'HMR fixture shutdown',
      );
      if (sockets.size || hmr.size || upgrades.size)
        throw new Error('Owned sockets not closed');
      receipt('stopped');
      phase = 'stopped';
      process.stdin.pause();
      process.stdin.unref();
      await new Promise((resolve, reject) =>
        receipts.once('error', reject).end(resolve),
      );
      await Promise.all(
        [process.stdout, process.stderr].map(
          (stream) =>
            new Promise((resolve, reject) =>
              stream.write('', (error) => (error ? reject(error) : resolve())),
            ),
        ),
      );
      // Next may retain handles after app.close(). Exit only this fixture,
      // after graceful closure and receipt/diagnostic stream flushes.
      // Preserve prior failure status; a rejected closure/flush never gets here.
      process.exit(process.exitCode ?? 0);
    });
    return stopping;
  }
  function failure(error) {
    console.error(error);
    process.exitCode = 1;
    stop().catch((shutdownError) => {
      console.error(shutdownError);
      process.exitCode = 1;
    });
  }
  readFrames(
    process.stdin,
    (frame) => {
      controlFrame(frame, identity, phase, 'command');
      if (frame.event === 'arm') {
        phase = 'armed';
        if (upgrades.arm() && phase === 'armed') receipt('armed');
      } else if (frame.event === 'shutdown') stop().catch(failure);
      else {
        const socket = hmr.get(frame.socketId);
        if (!socket) throw new Error('Unknown owned HMR socket');
        once(socket, 'close')
          .then(() => receipt('socket-closed', { socketId: frame.socketId }))
          .catch(failure);
        socket.destroy();
      }
    },
    (error) => {
      if (phase === 'stopped') return;
      if (error.message === 'Fixture control pipe closed')
        stop().catch(failure);
      else failure(error);
    },
  );
  process.on('SIGTERM', () => stop().catch(failure));
  process.on('SIGINT', () => stop().catch(failure));
  let bootstrap;
  startup = createStartupSequence([
    async () => {
      // Bind exclusively FIRST: occupied 3151 cannot touch a foreign listener/app.
      server.listen(3151, '127.0.0.1');
      await once(server, 'listening');
      if (
        server.address().address !== '127.0.0.1' ||
        server.address().port !== 3151
      )
        throw new Error('Wrong bind');
    },
    async () => {
      app = require('next')({
        dev: true,
        webpack: true,
        dir,
        hostname: '127.0.0.1',
        port: 3151,
      });
      await app.prepare();
    },
    () => {
      const prototype = Object.getPrototypeOf(app);
      if (
        typeof Object.getOwnPropertyDescriptor(prototype, 'requestHandler')
          ?.get !== 'function' ||
        typeof Object.getOwnPropertyDescriptor(prototype, 'upgradeHandler')
          ?.get !== 'function'
      )
        throw new Error('Pinned NextCustomServer getters missing');
      requestHandler = app.requestHandler;
      upgradeHandler = app.upgradeHandler;
      if (
        typeof requestHandler !== 'function' ||
        typeof upgradeHandler !== 'function'
      )
        throw new Error('Uninitialized Next handlers');
    },
    async (signal) => {
      const response = await fetch('http://127.0.0.1:3151/', { signal });
      const body = await response.text();
      bootstrap = { response, body };
    },
    () => {
      const { response, body } = bootstrap;
      if (response.status !== 200 || !body.includes('HMR protocol fixture'))
        throw new Error('Bootstrap did not compile expected fixture');
      record('bootstrap', {
        status: response.status,
        bytes: Buffer.byteLength(body),
        getters: ['requestHandler', 'upgradeHandler'],
      });
      phase = 'bound';
      receipt('bound', { host: '127.0.0.1', port: 3151 });
    },
  ]);
  startup.settled.catch((error) => {
    // A shutdown-triggered fetch abort is cancellation, not successful startup.
    if (phase === 'stopping' && error.name === 'AbortError') return;
    failure(error);
  });
}

module.exports = {
  createUpgradeGate,
  createStartupSequence,
  startupUpgradeLimits,
};

if (require.main === module)
  main(process.argv.slice(2)).catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
