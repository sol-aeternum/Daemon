// Fresh actual Next application. No replacement page, proxy or auth provider.
import next from "../frontend/node_modules/next/dist/server/next.js";
import { createServer } from "node:http";
import { resolve } from "node:path";

const backend = process.env.DAEMON_INTERNAL_API_URL;
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(backend ?? ""))
  throw Error("Loopback backend required");
const realFetch = globalThis.fetch;
globalThis.fetch = (input, init) => {
  const url = new URL(
    typeof input === "string" || input instanceof URL ? input : input.url,
  );
  if (url.origin !== backend)
    throw Error("Unexpected fixture fetch destination");
  return realFetch(input, init);
};
const app = next({
  dev: true,
  dir: resolve("frontend"),
  hostname: "127.0.0.1",
});
await app.prepare();
const handler = app.getRequestHandler();
const server = createServer((req, res) => handler(req, res));
await new Promise((done, reject) => {
  server.once("error", reject);
  server.listen(0, "127.0.0.1", done);
});
console.log(
  JSON.stringify({ ui: `http://127.0.0.1:${server.address().port}` }),
);
process.once("SIGTERM", async () => {
  server.closeAllConnections();
  server.close();
  await app.close();
  process.exit(0);
});
