// Fixture-only preload: Next must not read this checkout's operator dotenv files.
// Loaded before Next (including its build subprocesses), never by normal launchers.
const fs = require('node:fs');
const path = require('node:path');
const { syncBuiltinESMExports } = require('node:module');
const blocked = (file) => {
  try {
    return /^\.env(?:\.|$)/.test(path.basename(String(file)));
  } catch {
    return false;
  }
};
const absent = () =>
  Object.assign(new Error('Fixture dotenv disabled'), { code: 'ENOENT' });
const readSync = fs.readFileSync;
fs.readFileSync = function (file, ...args) {
  if (blocked(file)) throw absent();
  return readSync.call(this, file, ...args);
};
const read = fs.readFile;
fs.readFile = function (file, ...args) {
  if (blocked(file)) return queueMicrotask(() => args.at(-1)(absent()));
  return read.call(this, file, ...args);
};
const readPromise = fs.promises.readFile;
fs.promises.readFile = async function (file, ...args) {
  if (blocked(file)) throw absent();
  return readPromise.call(this, file, ...args);
};
syncBuiltinESMExports();

// Prevent Node build/server helpers from opening non-loopback network clients.
// Browser requests are independently origin-restricted by the Playwright driver.
const net = require('node:net');
const connect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function (...args) {
  const options = typeof args[0] === 'object' ? args[0] : null;
  const host =
    options?.host ?? (typeof args[1] === 'string' ? args[1] : '127.0.0.1');
  const unix =
    options?.path || (typeof args[0] === 'string' && args[0].startsWith('/'));
  if (!unix && !['127.0.0.1', '::1', 'localhost'].includes(host))
    throw new Error('B1 fixture denied external Node connection');
  return connect.apply(this, args);
};
