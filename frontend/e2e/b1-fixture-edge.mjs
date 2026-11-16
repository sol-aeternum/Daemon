// Owned HTTPS edge. Only /__b1/* and two named upstream transport probes are
// synthetic; protected/auth responses otherwise originate in real main.app.
import http from 'node:http';
import https from 'node:https';
import { readFileSync } from 'node:fs';
import readline from 'node:readline';

const packet = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const { nonce, port, backendPort, nextPort } = packet;
const emit = (value) => process.stdout.write(JSON.stringify(value) + '\n');
const state = {
  requests: [],
  dropped: 0,
  redirectTargets: 0,
  headerProbes: 0,
  heldRefresh: null,
};
let armed = false;
let dropRefresh = false;
let nextHold = null;
let heldResponse = null;
let stopping = false;
// Reproduction of the previous rule boundary, NOT a historical built worker:
// configured /daemon remains network-only; legacy root reads can use Cache API.
const priorWorker = String.raw`
self.addEventListener('install', e => e.waitUntil(self.skipWaiting()));
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (u.pathname.startsWith('/daemon/') || u.pathname.startsWith('/api/')) {
    e.respondWith(fetch(e.request, {cache: 'no-store'})); return;
  }
  if (!/^\/(?:conversations|users\/me\/(?:settings|entitlements))(?:\/|$)/.test(u.pathname)) return;
  const work = (async () => {
    try {
      const response = await fetch(e.request);
      if (response.ok) await (await caches.open('others')).put(e.request, response.clone());
      return response;
    } catch (error) {
      const cached = await (await caches.open('others')).match(e.request);
      if (cached) return cached;
      throw error;
    }
  })();
  e.respondWith(work); e.waitUntil(work);
});`;
const server = https.createServer(
  {
    key: readFileSync(packet.key),
    cert: readFileSync(packet.cert),
  },
  (request, response) => {
    const target = new URL(request.url, `https://127.0.0.1:${port}`);
    if (target.pathname === '/__b1/identity') {
      response.writeHead(200, {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-store',
      });
      return response.end(JSON.stringify({ nonce, port, role: 'edge' }));
    }
    if (!armed) {
      response.writeHead(503);
      return response.end();
    }
    if (target.pathname === '/__b1/prior-worker.js') {
      response.writeHead(200, {
        'Content-Type': 'application/javascript',
        'Cache-Control': 'no-store',
        'Service-Worker-Allowed': '/',
      });
      return response.end(priorWorker);
    }
    if (target.pathname === '/__b1/passive') {
      response.writeHead(200, {
        'Content-Type': 'text/html',
        'Cache-Control': 'no-store',
      });
      return response.end(
        '<!doctype html><title>B1 synthetic cache fixture</title>',
      );
    }
    const backend = target.pathname.startsWith('/daemon/');
    const path = backend ? request.url.slice('/daemon'.length) : request.url;
    // Credentials/bodies/query strings are never recorded.
    if (backend || target.pathname.startsWith('/api/'))
      state.requests.push({ method: request.method, path: target.pathname });
    if (backend && /^\/v1\/auth\/__b1_redirect_(307|308)$/.test(path)) {
      const code = Number(path.slice(-3));
      response.writeHead(code, {
        Location: `https://127.0.0.1:${port}/daemon/v1/auth/__b1_redirect_target`,
        'Cache-Control': 'no-store',
      });
      return response.end();
    }
    if (backend && path === '/v1/auth/__b1_redirect_target') {
      state.redirectTargets += 1;
      response.writeHead(418, { 'Cache-Control': 'no-store' });
      return response.end();
    }
    if (backend && path === '/v1/auth/__b1_headers') {
      state.headerProbes += 1;
      response.writeHead(204, {
        'Cache-Control': 'no-store',
        'Set-Cookie': [
          'b1_transport_one=fictional; Path=/; Secure; HttpOnly; SameSite=Strict',
          'b1_transport_two=fictional; Path=/; Secure; HttpOnly; SameSite=Strict',
        ],
      });
      return response.end();
    }
    const lose =
      backend &&
      path === '/v1/auth/refresh' &&
      request.method === 'POST' &&
      dropRefresh;
    if (lose) dropRefresh = false;
    const hold =
      backend && path === '/v1/auth/refresh' && request.method === 'POST'
        ? nextHold
        : null;
    if (hold) nextHold = null;
    const headers = {
      ...request.headers,
      host: `127.0.0.1:${port}`,
      'x-forwarded-host': `127.0.0.1:${port}`,
      'x-forwarded-proto': 'https',
    };
    delete headers.connection;
    const upstream = http.request(
      {
        hostname: '127.0.0.1',
        port: backend ? backendPort : nextPort,
        path,
        method: request.method,
        headers,
      },
      (result) => {
        if (hold) {
          // Buffer ONLY the genuine completed handler response. No response body
          // or cookie material is exposed in IPC, logs or the public fixture API.
          const chunks = [];
          result.on('data', (chunk) => chunks.push(chunk));
          result.on('end', () => {
            const body = Buffer.concat(chunks);
            const outgoing = { ...result.headers };
            delete outgoing.connection;
            delete outgoing['transfer-encoding'];
            state.heldRefresh = {
              phase: hold,
              backendComplete: true,
              status: result.statusCode,
              headersSent: false,
              released: false,
            };
            if (result.statusCode !== 200 || body.length < 2) {
              response.destroy();
              return;
            }
            heldResponse = {
              release() {
                if (response.destroyed) return;
                if (hold === 'headers')
                  response.writeHead(result.statusCode, outgoing);
                response.end(hold === 'body' ? body.subarray(1) : body);
                state.heldRefresh.headersSent = true;
                state.heldRefresh.released = true;
              },
              destroy() {
                response.destroy();
              },
            };
            if (hold === 'body') {
              response.writeHead(result.statusCode, outgoing);
              response.flushHeaders();
              // Forward an actual JSON prefix so Next streams its genuine
              // headers, while the coordinator still awaits body termination.
              response.write(body.subarray(0, 1));
              state.heldRefresh.headersSent = true;
            }
          });
          result.on('error', () => response.destroy());
          return;
        }
        if (lose) {
          // Receiving the real handler's complete 200 means its transaction context
          // exited. Parent additionally checks persisted predecessor/successor SQL.
          result.resume();
          result.on('end', () => {
            if (result.statusCode === 200) state.dropped += 1;
            response.destroy();
          });
          return;
        }
        // Node's raw IncomingMessage preserves Set-Cookie arrays; do not fold them.
        const outgoing = { ...result.headers };
        delete outgoing.connection;
        delete outgoing['transfer-encoding'];
        response.writeHead(result.statusCode, outgoing);
        result.pipe(response);
      },
    );
    upstream.on('error', () => {
      if (!response.headersSent)
        response.writeHead(502, { 'Cache-Control': 'no-store' });
      response.end();
    });
    request.pipe(upstream);
  },
);
async function stop() {
  if (stopping) return;
  stopping = true;
  heldResponse?.destroy();
  heldResponse = null;
  server.close();
  server.closeAllConnections();
  emit({ event: 'stopped', nonce });
  process.exit(0);
}
readline
  .createInterface({ input: process.stdin })
  .on('line', (line) => {
    try {
      const command = JSON.parse(line);
      if (command.op === 'stop') return void stop();
      if (command.op === 'arm') armed = true;
      else if (command.op === 'drop-refresh') dropRefresh = true;
      else if (command.op === 'hold-refresh') {
        if (
          !['headers', 'body'].includes(command.phase) ||
          nextHold ||
          heldResponse
        )
          throw new Error();
        nextHold = command.phase;
        state.heldRefresh = {
          phase: command.phase,
          backendComplete: false,
          headersSent: false,
          released: false,
        };
      } else if (command.op === 'release-refresh') {
        if (!heldResponse) throw new Error();
        heldResponse.release();
        heldResponse = null;
      } else if (command.op !== 'state') throw new Error();
      emit({
        id: command.id,
        ok: true,
        state: { ...state, requests: [...state.requests] },
      });
    } catch {
      emit({ ok: false });
    }
  })
  .on('close', () => void stop());
server.on('error', () => {
  emit({ event: 'failed', nonce });
  process.exit(2);
});
server.listen({ fd: packet.fd }, () => emit({ event: 'bound', nonce, port }));
