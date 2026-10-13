// Isolated loopback fixture. Actual Next auth proxy and production FastAPI
// cache-clear handler; synthetic artifacts demonstrate browser HTTP caching.
import http from 'node:http';
import { spawn } from 'node:child_process';
import { Readable } from 'node:stream';
import { build } from 'esbuild';
import path from 'node:path';
import { cacheFixtureURLs } from './cache-fixture-url.mjs';

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
const env = {
  PATH: process.env.PATH,
  HOME: process.env.HOME,
  TMPDIR: process.env.TMPDIR,
  NODE_OPTIONS: process.env.NODE_OPTIONS,
  PYTHONPATH: '..',
  // Explicit clean env: no inherited application credentials and no implicit
  // dotenv loading inside litellm or any other dependency.
  LITELLM_MODE: 'PRODUCTION',
  NEXT_TELEMETRY_DISABLED: '1',
  DAEMON_INTERNAL_API_URL: `${apiOrigin}/daemon`,
  NEXT_PUBLIC_API_URL: `${apiOrigin}/daemon`,
};
const api = spawn(
  path.resolve(
    '..',
    process.env.UV_PROJECT_ENVIRONMENT ?? '.uv-venv',
    'bin/python',
  ),
  [
    '-m',
    'uvicorn',
    'cache-fixture-api:app',
    '--app-dir',
    'e2e',
    '--host',
    '127.0.0.1',
    '--port',
    '3143',
  ],
  { env, stdio: 'inherit' },
);
const next = spawn(
  process.execPath,
  [
    'node_modules/next/dist/bin/next',
    'dev',
    '-H',
    '127.0.0.1',
    '-p',
    '3141',
    '--webpack',
  ],
  { env, stdio: 'inherit' },
);
const state = { upgraded: false, hits: 0 };
const server = http.createServer(async (request, response) => {
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
function stop() {
  server.close();
  api.kill('SIGTERM');
  next.kill('SIGTERM');
}
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
api.on('exit', () => {
  server.close();
  next.kill('SIGTERM');
});
next.on('exit', () => {
  server.close();
  api.kill('SIGTERM');
});

// The readiness URL must represent ALL participants, not just the seed server.
// Compile the actual proxy before Playwright starts; assertion timeouts stay
// unchanged and no test has to race Python/router import or Next compilation.
const deadline = Date.now() + 90_000;
let ready = false;
try {
  while (Date.now() < deadline) {
    try {
      const backend = await fetch(`${apiOrigin}/__fixture/status`, {
        signal: AbortSignal.timeout(2000),
      });
      const proxy = await fetch(`${nextOrigin}/api/v1/auth/cache/clear`, {
        method: 'POST',
        headers: { Origin: appOrigin, 'Sec-Fetch-Site': 'same-origin' },
        signal: AbortSignal.timeout(2000),
      });
      if (backend.ok && proxy.status === 204) {
        ready = true;
        break;
      }
    } catch {
      // Both child processes are still starting; no application request replay.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!ready)
    throw new Error('Cache fixture backend/proxy failed to become ready');
  server.listen(3142, '127.0.0.1');
} catch (error) {
  stop();
  throw error;
}
