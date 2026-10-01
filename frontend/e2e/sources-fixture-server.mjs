// Local fictional API + frontend proxy. Unlike page.route, these responses also
// reach actual production service-worker fetches. Never points at the real API.
import http from 'node:http';
import { spawn } from 'node:child_process';
import { Readable } from 'node:stream';
import { fixtureURL, frontendURL } from './sources-fixture-url.mjs';

const app = 'http://127.0.0.1:3102';
const conversation = '11111111-1111-4111-8111-111111111111';
const ids = Array.from(
  { length: 21 },
  (_, index) =>
    `22222222-2222-4222-8222-${String(index + 1).padStart(12, '0')}`,
);
let removed = new Set();
let deletes = 0;
let failure = 0;
let failureKind = '';
let unknownDelete = false;
let heldExports = [];
const now = new Date();
const metadata = (id) => ({
  id,
  conversation_id: conversation,
  source_url: 'https://example.org/fictional-source',
  final_url: 'https://example.org/fictional-source',
  title: `Fictional retained source ${ids.indexOf(id) + 1}`,
  extract_mode: 'text',
  extraction_version: 'fixture-v1',
  content_chars: 20,
  content_bytes: 20,
  stored_bytes: 120,
  retrieved_at: now.toISOString(),
  expires_at: new Date(now.getTime() + 86_400_000).toISOString(),
});
function json(response, value, status = 200) {
  response.writeHead(status, {
    'Content-Type': 'application/json',
    'Cache-Control': 'no-store',
    'Access-Control-Allow-Origin': app,
    'Access-Control-Allow-Headers': 'Authorization, Content-Type',
    'Access-Control-Allow-Methods': 'GET, DELETE, OPTIONS',
  });
  response.end(JSON.stringify(value));
}
function fixture(request, response, url) {
  const path = url.pathname.replace(/^\/daemon(?=\/)/, '');
  if (path === '/__fixture/reset') {
    removed = new Set();
    deletes = 0;
    failure = Number(url.searchParams.get('failure') || 0);
    failureKind = '';
    unknownDelete = false;
    json(response, { status: 'reset' });
    return true;
  }
  if (path === '/__fixture/status') {
    json(response, { deletes, heldExports: heldExports.length });
    return true;
  }
  if (path === '/__fixture/expire-last') {
    removed.add(ids.at(-1));
    json(response, { status: 'fictional expiry' });
    return true;
  }
  if (path === '/__fixture/failure') {
    failure = Number(url.searchParams.get('status') || 0);
    failureKind = url.searchParams.get('kind') || '';
    unknownDelete = url.searchParams.get('unknownDelete') === 'true';
    json(response, { status: 'configured' });
    return true;
  }
  if (path === '/__fixture/release') {
    for (const complete of heldExports) complete();
    heldExports = [];
    json(response, { status: 'released' });
    return true;
  }
  if (path === '/legacy-snapshot-worker.js') {
    // Fictional prior worker reproduces the old Cache API write despite HTTP
    // no-store, keeping its fetch alive until the write finishes. Local only.
    response.writeHead(200, {
      'Content-Type': 'text/javascript',
      'Cache-Control': 'no-store',
    });
    response.end(`
      self.addEventListener('install', () => self.skipWaiting());
      self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
      self.addEventListener('fetch', event => {
        if (!new URL(event.request.url).pathname.includes('/web-snapshots')) return;
        const operation = (async () => {
          const response = await fetch(event.request);
          if (response.ok) await (await caches.open('others')).put(event.request, response.clone());
          return response;
        })();
        event.respondWith(operation);
        event.waitUntil(operation);
      });
    `);
    return true;
  }
  if (path.includes('/web-snapshots')) {
    if (request.method === 'OPTIONS') {
      json(response, {});
      return true;
    }
    if (request.headers.authorization !== 'Bearer sources-fixture-token') {
      json(response, { detail: 'Not authenticated' }, 401);
      return true;
    }
    const kind =
      request.method === 'DELETE'
        ? 'delete'
        : path.endsWith('/export')
          ? 'export'
          : 'list';
    if (failure && (!failureKind || failureKind === kind)) {
      json(response, { detail: 'Fictional failure' }, failure);
      return true;
    }
    const prefix = `/conversations/${conversation}/web-snapshots`;
    if (path === prefix) {
      const offset = Number(url.searchParams.get('offset') || 0);
      const limit = Number(url.searchParams.get('limit') || 20);
      const all = ids.filter((id) => !removed.has(id));
      json(response, {
        snapshots: all.slice(offset, offset + limit).map(metadata),
        total: all.length,
        offset,
        limit,
      });
      return true;
    }
    const id = path.slice(prefix.length + 1).split('/')[0];
    if (
      !ids.includes(id) ||
      removed.has(id) ||
      !path.startsWith(`${prefix}/`)
    ) {
      json(response, { detail: 'Snapshot not found' }, 404);
      return true;
    }
    if (request.method === 'DELETE' && path === `${prefix}/${id}`) {
      deletes += 1;
      removed.add(id);
      if (unknownDelete) {
        // A response failure after the fictional mutation leaves its outcome
        // unknown to the client. A bare socket reset may be transparently
        // retried by Chromium's HTTP layer and yield the route's generic 404;
        // do not confuse that transport behavior with an application replay.
        json(
          response,
          { detail: 'Fictional post-removal response failure' },
          503,
        );
        return true;
      }
      json(response, { status: 'deleted' });
      return true;
    }
    if (request.method === 'GET' && path === `${prefix}/${id}/export`) {
      if (url.searchParams.get('hold') === 'true') {
        heldExports.push(() =>
          json(response, { content: 'FICTIONAL IN-FLIGHT LEGACY EXPORT' }),
        );
        return true;
      }
      response.writeHead(200, {
        'Content-Type': 'application/json',
        'Content-Disposition': `attachment; filename="web-snapshot-${id}.json"`,
        'Cache-Control': 'no-store',
        'Access-Control-Allow-Origin': app,
      });
      response.end(
        JSON.stringify({
          schema: 'daemon.web_snapshot_export',
          version: 1,
          snapshot_id: id,
          ...metadata(id),
          content: 'Fictional page text.',
        }),
      );
      return true;
    }
    json(response, { detail: 'Not found' }, 404);
    return true;
  }
  if (path === '/api/v1/auth/refresh') {
    json(response, { access_token: 'sources-fixture-token', expires_in: 3600 });
    return true;
  }
  if (path === '/api/v1/auth/config') {
    json(response, {
      mode: 'self_hosted',
      email: { enabled: false },
      google: { enabled: false },
    });
    return true;
  }
  if (path === '/conversations') {
    json(response, {
      conversations: [
        {
          id: conversation,
          title: 'Fictional Sources review',
          created_at: now.toISOString(),
          updated_at: now.toISOString(),
          pinned: false,
          title_locked: false,
          status: 'active',
          metadata: {},
        },
      ],
    });
    return true;
  }
  if (path === `/conversations/${conversation}`) {
    json(response, {
      id: conversation,
      title: 'Fictional Sources review',
      messages: [],
      metadata: {},
    });
    return true;
  }
  if (path === '/api/entitlements') {
    json(response, { plan: 'free', capabilities: ['chat'], limits: {} });
    return true;
  }
  if (path === '/v1/catalog') {
    json(response, {
      auto: {
        id: 'auto',
        name: 'Auto',
        tagline: 'Automatic routing',
        icon: 'zap',
      },
      featured: [],
    });
    return true;
  }
  if (path === '/users/me/settings') {
    json(response, {});
    return true;
  }
  if (path === '/chat' || path === '/api/chat') {
    json(
      response,
      { detail: 'No chat/model execution in Sources fixtures' },
      409,
    );
    return true;
  }
  return false;
}

const next = spawn(
  process.execPath,
  ['node_modules/next/dist/bin/next', 'start', '-H', '127.0.0.1', '-p', '3101'],
  { stdio: 'inherit' },
);
const api = http.createServer((request, response) => {
  let url;
  try {
    url = fixtureURL(request.url, 'http://127.0.0.1:3103');
  } catch {
    json(response, { detail: 'Invalid fixture request target' }, 400);
    return;
  }
  if (!fixture(request, response, url))
    json(response, { detail: 'Fixture not found' }, 404);
});
const proxy = http.createServer(async (request, response) => {
  let url;
  try {
    url = fixtureURL(request.url, app);
  } catch {
    json(response, { detail: 'Invalid fixture request target' }, 400);
    return;
  }
  if (fixture(request, response, url)) return;
  try {
    const upstream = await fetch(frontendURL(url), {
      method: request.method,
      headers: { ...request.headers, host: '127.0.0.1:3101' },
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
    response.end('Fixture frontend starting');
  }
});
api.listen(3103, '127.0.0.1');
proxy.listen(3102, '127.0.0.1');
function stop() {
  proxy.close();
  api.close();
  next.kill('SIGTERM');
}
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
next.on('exit', () => {
  proxy.close();
  api.close();
});
