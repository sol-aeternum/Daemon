// Fixture-only custom server; production request handlers remain unchanged.
import http from 'node:http';
import next from 'next';
import {
  childControl,
  identityFor,
  launchIdentity,
} from './cache-fixture-control.mjs';

const identity = launchIdentity();
const expected = identityFor(identity, 'next');
let app;
const server = http.createServer(async (request, response) => {
  if (request.method === 'GET' && request.url === '/__fixture/identity') {
    response.writeHead(200, {
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
    });
    return response.end(JSON.stringify(expected));
  }
  if (!control.isArmed()) {
    response.writeHead(503, { 'Cache-Control': 'no-store' });
    return response.end('Fixture not armed');
  }
  return app.getRequestHandler()(request, response);
});
const control = childControl(identity, 'next', async () => {
  server.close();
  server.closeAllConnections();
  await app?.close();
  // Explicit fixture-owned process exit after graceful server/app closure. No PID signalling.
  process.exit(process.exitCode ?? 0);
});
server.on('error', (error) => {
  console.error(error);
  process.exitCode = 1;
  control.stop();
});
// Bind before expensive preparation; no default-port retry or increment.
server.listen(expected.port, expected.host, async () => {
  try {
    app = next({
      dev: true,
      hostname: expected.host,
      port: expected.port,
      webpack: true,
      httpServer: server,
    });
    await app.prepare();
    control.bound(server.address());
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
    control.stop();
  }
});
