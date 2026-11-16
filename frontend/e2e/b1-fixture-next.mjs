// Test-only production Next server. All application handlers are unmodified.
import http from 'node:http';
import readline from 'node:readline';
import next from 'next';

const port = Number(process.argv[2]);
const nonce = process.argv[3];
if (!Number.isInteger(port) || !/^[a-f0-9]{32}$/.test(nonce)) process.exit(2);
const emit = (value) => process.stdout.write(JSON.stringify(value) + '\n');
// Never forward Next's console/error objects: request diagnostics may hold auth.
console.log = console.warn = console.error = () => {};
const server = http.createServer(async (request, response) => {
  if (request.url === '/__b1_identity') {
    response.writeHead(200, {
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
    });
    return response.end(JSON.stringify({ nonce, port, role: 'next' }));
  }
  if (!ready) {
    response.writeHead(503);
    return response.end();
  }
  try {
    await app.getRequestHandler()(request, response);
  } catch {
    response.destroy();
  }
});
let ready = false;
const app = next({
  dev: false,
  hostname: '127.0.0.1',
  port,
  httpServer: server,
});
const bound = new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen({ fd: Number(process.argv[4]) }, resolve);
});
async function stop() {
  ready = false;
  server.close();
  server.closeAllConnections();
  await app.close();
  emit({ event: 'stopped', nonce });
  process.exit(0);
}
readline
  .createInterface({ input: process.stdin })
  .on('line', (line) => {
    try {
      if (JSON.parse(line).op === 'stop') void stop();
    } catch {
      process.exit(2);
    }
  })
  .on('close', () => void stop());
try {
  await bound;
  await app.prepare();
  ready = true;
  emit({ event: 'ready', nonce, port });
} catch {
  emit({ event: 'failed', nonce });
  await stop();
}
