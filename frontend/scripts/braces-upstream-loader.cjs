'use strict';

// Only the Mocha test process uses these independently locked test tools.
// Application resolution is NOT patched; installed code is checked separately.
const path = require('node:path');
const Module = require('node:module');
const tools = Module.createRequire(
  path.resolve(__dirname, '../vendor/braces-test-tools/package.json'),
);
const testTools = {
  mocha: tools.resolve('mocha'),
  'bash-path': tools.resolve('bash-path'),
};
const resolve = Module._resolveFilename;
Module._resolveFilename = function (request, parent, ...args) {
  if (Object.hasOwn(testTools, request)) return testTools[request];
  return resolve.call(this, request, parent, ...args);
};
