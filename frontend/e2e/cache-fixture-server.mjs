// Isolated loopback fixture. Actual Next auth proxy and production FastAPI
// cache-clear handler; synthetic artifacts demonstrate browser HTTP caching.
import http from 'node:http';
import { spawn } from 'node:child_process';
import { Readable } from 'node:stream';
import { build } from 'esbuild';
import path from 'node:path';
import { appendFileSync, openSync } from 'node:fs';
import { cacheFixtureURLs } from './cache-fixture-url.mjs';
import { fixtureEnvironment } from './cache-fixture-environment.mjs';
import {
  identityFor,
  serveAppGate,
  childControl,
  launchIdentity,
  ownChild,
  verifyHTTP,
  waitFor,
} from './cache-fixture-control.mjs';

const appOrigin = 'http://127.0.0.1:3142';
const nextOrigin = 'http://127.0.0.1:3141';
const apiOrigin = 'http://127.0.0.1:3143';
const clientBuildOptions = {
  entryPoints: ['lib/browserCache.ts'],
  bundle: true,
  write: false,
  format: 'esm',
  platform: 'browser',
  define: {
    'process.env.NEXT_PUBLIC_API_URL': JSON.stringify(`${apiOrigin}/daemon/`),
    'process.env.NODE_ENV': JSON.stringify('development'),
  },
};
const env = fixtureEnvironment();
// Start owned participants only after the app's exclusive bind succeeds.
const identity = launchIdentity();
const receipt = openSync(
  path.join(env.TMPDIR, `cache-${identity.nonce}.jsonl`),
  'ax',
  0o600,
);
function record(event, values = {}) {
  appendFileSync(
    receipt,
    JSON.stringify({ event, ...identity, ...values }) + '\n',
  );
}
record('intent');
let ready = false;
let stopping;
let rejectFailed;
const failed = new Promise((_, reject) => {
  rejectFailed = reject;
});
failed.catch(() => {});
const children = [];
function fail(error) {
  ready = false;
  record('failure', { message: String(error) });
  rejectFailed(error);
  if (!stopping) void stop(1);
}
const state = { upgraded: false, hits: 0 };
const server = http.createServer(async (request, response) => {
  if (serveAppGate(request, response, identity, ready)) return;
  let url;
  let upstreamUrl;
  try {
    ({ url, upstream: upstreamUrl } = cacheFixtureURLs(request.url));
  } catch {
    response.writeHead(400, { 'Cache-Control': 'no-store' });
    response.end('Invalid fixture request target');
    return;
  }
  if (url.pathname === '/__fixture/status') {
    response.writeHead(200, {
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
    });
    response.end(JSON.stringify(state));
  } else if (url.pathname === '/__fixture/upgrade') {
    state.upgraded = true;
    response.writeHead(204);
    response.end();
  } else if (url.pathname === '/__fixture/reset') {
    state.upgraded = false;
    state.hits = 0;
    response.writeHead(204);
    response.end();
  } else if (url.pathname.startsWith('/generated-files/')) {
    state.hits += 1;
    response.writeHead(200, {
      'Cache-Control': state.upgraded
        ? 'private, no-store'
        : 'private, max-age=3600',
    });
    response.end(
      state.upgraded ? 'FICTIONAL_NEW_OWNER_B' : 'FICTIONAL_OLD_OWNER_A',
    );
  } else if (url.pathname === '/__fixture/cache-client.js') {
    const variant = url.searchParams.get('variant');
    const apiBase =
      variant === 'insecure'
        ? 'http://192.0.2.10:3143/daemon/'
        : variant === 'same-origin'
          ? `${appOrigin}/daemon/`
          : `${apiOrigin}/daemon/`;
    const output = await build({
      ...clientBuildOptions,
      define: {
        ...clientBuildOptions.define,
        'process.env.NEXT_PUBLIC_API_URL': JSON.stringify(apiBase),
      },
    });
    response.writeHead(200, {
      'Content-Type': 'text/javascript',
      'Cache-Control': 'no-store',
    });
    response.end(output.outputFiles[0].text);
  } else if (url.pathname === '/__fixture/passive-worker.js') {
    response.writeHead(200, {
      'Content-Type': 'text/javascript',
      'Cache-Control': 'no-store',
    });
    // Registered only under an unused scope; never controls cache-test reads.
    response.end("self.addEventListener('install', () => self.skipWaiting());");
  } else if (url.pathname === '/__fixture/provider.js') {
    const output = await build({
      ...clientBuildOptions,
      entryPoints: ['e2e/cache-provider-harness.tsx'],
      alias: {
        'next/navigation': path.resolve('e2e/cache-fixture-navigation.ts'),
      },
      tsconfig: 'tsconfig.json',
      jsx: 'automatic',
    });
    response.writeHead(200, {
      'Content-Type': 'text/javascript',
      'Cache-Control': 'no-store',
    });
    response.end(output.outputFiles[0].text);
  } else if (
    (url.pathname === '/auth' && url.searchParams.has('cache-fixture')) ||
    url.pathname === '/setup' ||
    url.pathname.startsWith('/settings/p02-')
  ) {
    response.writeHead(200, {
      'Content-Type': 'text/html',
      'Cache-Control': 'no-store',
    });
    response.end(
      '<!doctype html><title>Fictional provider cache gate</title><div id="fixture-root"></div><script type="module" src="/__fixture/provider.js"></script>',
    );
  } else if (url.pathname === '/__fixture/seed') {
    response.writeHead(200, {
      'Content-Type': 'text/html',
      'Cache-Control': 'no-store',
    });
    response.end(
      '<!doctype html><title>Fictional HTTP-cache seed</title><p>Isolated cache fixture</p>',
    );
  } else {
    try {
      const upstream = await fetch(upstreamUrl, {
        method: request.method,
        headers: { ...request.headers, host: '127.0.0.1:3141' },
        body: ['GET', 'HEAD'].includes(request.method)
          ? undefined
          : Readable.toWeb(request),
        ...(['GET', 'HEAD'].includes(request.method) ? {} : { duplex: 'half' }),
        redirect: 'manual',
      });
      const headers = Object.fromEntries(upstream.headers);
      for (const key of [
        'content-encoding',
        'content-length',
        'transfer-encoding',
        'connection',
      ])
        delete headers[key];
      response.writeHead(upstream.status, headers);
      if (upstream.body) Readable.fromWeb(upstream.body).pipe(response);
      else response.end();
    } catch {
      response.writeHead(503);
      response.end('Task fixture starting');
    }
  }
});
const control = childControl(identity, 'app', () => stop());
function stop(code = process.exitCode ?? 0) {
  if (stopping) return stopping;
  ready = false;
  stopping = (async () => {
    server.close();
    server.closeAllConnections();
    const outcomes = await Promise.allSettled(
      children.map((child) => child.stop()),
    );
    const incomplete = outcomes.some(
      (outcome) => outcome.status === 'rejected',
    );
    record(incomplete ? 'cleanup-incomplete-retained' : 'children-stopped', {
      outcomes: outcomes.map((outcome) =>
        outcome.status === 'rejected' ? String(outcome.reason) : 'exited',
      ),
    });
    process.exitCode = incomplete ? 1 : code;
    // Startup refusal must close the launch pipe too: otherwise the parent waits
    // for EOF while this coordinator waits for a command on the same live pipe.
    process.stdin.destroy();
  })();
  return stopping;
}
process.on('SIGTERM', () => void stop());
process.on('SIGINT', () => void stop());

// The readiness URL must represent ALL participants, not just the seed server.
// Compile the actual proxy before Playwright starts; assertion timeouts stay
// unchanged and no test has to race Python/router import or Next compilation.
try {
  await waitFor(
    Promise.race([
      failed,
      (async () => {
        await new Promise((resolve, reject) => {
          server.once('error', reject);
          server.listen(3142, '127.0.0.1', resolve);
        });
        record('bound', identityFor(identity, 'app'));
        const api = spawn(
          path.resolve('..', env.UV_PROJECT_ENVIRONMENT, 'bin/python'),
          ['e2e/cache-fixture-api-runner.py', identity.task, identity.nonce],
          { env, stdio: ['pipe', 'inherit', 'inherit', 'pipe'] },
        );
        children.push(ownChild(api, identity, 'api', fail, record));
        const next = spawn(
          process.execPath,
          ['e2e/cache-fixture-next.mjs', identity.task, identity.nonce],
          { env, stdio: ['pipe', 'inherit', 'inherit', 'pipe'] },
        );
        children.push(ownChild(next, identity, 'next', fail, record));
        await Promise.all(children.map((child) => child.bound));
        for (const child of children) child.assertAlive();
        await Promise.all([
          verifyHTTP(appOrigin, identityFor(identity, 'app')),
          verifyHTTP(apiOrigin, identityFor(identity, 'api')),
          verifyHTTP(nextOrigin, identityFor(identity, 'next')),
        ]);
        // Arm only proven-owned listeners. The public app/seed remains gated.
        await Promise.all(children.map((child) => child.arm()));
        for (const child of children) child.assertAlive();
        const proxy = await fetch(`${nextOrigin}/api/v1/auth/cache/clear`, {
          method: 'POST',
          redirect: 'error',
          headers: { Origin: appOrigin, 'Sec-Fetch-Site': 'same-origin' },
          signal: AbortSignal.timeout(60000),
        });
        if (proxy.status !== 204) throw new Error('Owned proxy warm-up failed');
        for (const child of children) child.assertAlive();
        if (stopping) throw new Error('Fixture stopped during startup');
        ready = true;
        record('ready');
        control.bound(server.address());
      })(),
    ]),
    90000,
    'aggregate startup',
  );
} catch (error) {
  console.error(error);
  await stop(1);
}
