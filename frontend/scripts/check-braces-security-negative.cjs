'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const {
  verifyArtifacts,
  verifyInstallation,
  archiveFiles,
} = require('./check-braces-security.cjs');
const frontend = path.resolve(__dirname, '..');
const hash = (bytes, algorithm = 'sha256', encoding = 'hex') =>
  crypto.createHash(algorithm).update(bytes).digest(encoding);
const read = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));
const write = (file, value) => fs.writeFileSync(file, JSON.stringify(value));
// Derive the packed archive name from the vendored manifest so version bumps
// never leave a stale hardcoded path behind.
const vendoredManifest = read(
  path.join(frontend, 'vendor/braces/package.json'),
);
const archiveName = `${vendoredManifest.name
  .replace(/^@/, '')
  .replace('/', '-')}-${vendoredManifest.version}.tgz`;
function fixture(t) {
  const root = fs.mkdtempSync(
    path.join(os.tmpdir(), 'daemon-braces-negative-'),
  );
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.cpSync(path.join(frontend, 'vendor'), path.join(root, 'vendor'), {
    recursive: true,
    filter: (file) => !file.split(path.sep).includes('node_modules'),
  });
  for (const file of ['package.json', 'package-lock.json'])
    fs.copyFileSync(path.join(frontend, file), path.join(root, file));
  fs.cpSync(
    path.join(root, 'vendor/braces'),
    path.join(root, 'node_modules/braces'),
    { recursive: true },
  );
  for (const [name, dependencies] of [
    ['micromatch', { braces: '^3.0.3' }],
    ['chokidar', { braces: '~3.0.2' }],
    ['fast-glob', { micromatch: '^4.0.8' }],
  ]) {
    const dir = path.join(root, 'node_modules', name);
    fs.mkdirSync(dir);
    write(path.join(dir, 'package.json'), {
      name,
      version: '0.0.0-fixture',
      dependencies,
    });
    fs.writeFileSync(
      path.join(dir, 'index.js'),
      'throw Error("resolution-only fixture must not execute");',
    );
  }
  return root;
}

test('valid pinned source/lock and consumer-resolution fixture is accepted', (t) => {
  const root = fixture(t);
  const proof = verifyArtifacts(root);
  assert.equal(verifyInstallation(root, proof).consumers.length, 2);
});
test('tampered archive fails before any dependency execution', (t) => {
  const root = fixture(t);
  const file = path.join(root, 'vendor', archiveName);
  const bytes = fs.readFileSync(file);
  bytes[bytes.length - 1] ^= 1;
  fs.writeFileSync(file, bytes);
  assert.throws(() => verifyArtifacts(root), /Derivative archive changed/);
});
test('changed readable source and upstream release tests fail', (t) => {
  const root = fixture(t);
  fs.appendFileSync(
    path.join(root, 'vendor/braces/lib/parse.js'),
    '\n// drift\n',
  );
  assert.throws(() => verifyArtifacts(root), /Fixed bytes mismatch/);
});
test('release tests cannot silently be weakened', (t) => {
  const root = fixture(t);
  fs.appendFileSync(
    path.join(root, 'vendor/braces/test/braces.parse.js'),
    '\n// drift\n',
  );
  assert.throws(() => verifyArtifacts(root), /Fixed bytes mismatch/);
});
test('installed source tamper fails even if readable vendor and audit identity are intact', (t) => {
  const root = fixture(t);
  fs.appendFileSync(
    path.join(root, 'node_modules/braces/lib/expand.js'),
    '\n// drift\n',
  );
  assert.throws(
    () => verifyInstallation(root, verifyArtifacts(root)),
    /Fixed bytes mismatch/,
  );
});
test('nested unoverridden original fails actual resolution/graph inspection', (t) => {
  const root = fixture(t);
  const destination = path.join(
    root,
    'node_modules/micromatch/node_modules/braces',
  );
  fs.mkdirSync(destination, { recursive: true });
  for (const [relative, bytes] of archiveFiles(
    fs.readFileSync(path.join(root, 'vendor/braces-3.0.3.tgz')),
  )) {
    const file = path.join(destination, relative);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, bytes);
  }
  assert.throws(
    () => verifyInstallation(root, verifyArtifacts(root)),
    /Wrong consumer resolution|Wrong fast-glob resolution|Unpatched upstream copy remains/,
  );
});
test('missing override or lock integrity cannot pass', (t) => {
  const root = fixture(t);
  const file = path.join(root, 'package-lock.json');
  const lock = read(file);
  delete lock.packages['node_modules/braces'].integrity;
  write(file, lock);
  assert.throws(() => verifyArtifacts(root), /Lockfile must pin/);
});
/** Repack the fixture derivative and regenerate its provenance/lock metadata. */
function regenerateFixtureMetadata(root) {
  const packed = spawnSync(
    'npm',
    [
      'pack',
      '--ignore-scripts',
      '--pack-destination',
      path.join(root, 'vendor'),
      '--json',
    ],
    {
      cwd: path.join(root, 'vendor/braces'),
      timeout: 30000,
      encoding: 'utf8',
    },
  );
  assert.equal(packed.status, 0, packed.stderr);
  const packedOutput = JSON.parse(packed.stdout);
  const packedEntry = Array.isArray(packedOutput)
    ? packedOutput[0]
    : Object.values(packedOutput)[0];
  const packedName = packedEntry && packedEntry.filename;
  assert.equal(packedName, archiveName, 'Unexpected packed archive name');
  const archive = fs.readFileSync(path.join(root, 'vendor', packedName));
  const file = path.join(root, 'vendor/braces-provenance.json');
  const proof = read(file);
  proof.derivative.archive_sha256 = hash(archive);
  proof.derivative.integrity = `sha512-${hash(archive, 'sha512', 'base64')}`;
  proof.derivative.files = Object.fromEntries(
    [...archiveFiles(archive)].map(([name, bytes]) => [name, hash(bytes)]),
  );
  write(file, proof);
  const lockFile = path.join(root, 'package-lock.json');
  const lock = read(lockFile);
  lock.packages['node_modules/braces'].integrity = proof.derivative.integrity;
  write(lockFile, lock);
}

test('reverted code cannot hide behind updated archive/provenance and private identity', (t) => {
  const root = fixture(t);
  const original = archiveFiles(
    fs.readFileSync(path.join(root, 'vendor/braces-3.0.3.tgz')),
  );
  // Keep the PRIVATE derivative identity but revert the security implementation.
  for (const [relative, bytes] of original)
    if (relative.startsWith('lib/')) {
      fs.writeFileSync(path.join(root, 'vendor/braces', relative), bytes);
    }
  regenerateFixtureMetadata(root);
  assert.throws(() => verifyArtifacts(root), /Reviewed code reverted\/changed/);
});
test('reverted fractional parser guard fails even with regenerated metadata', (t) => {
  const root = fixture(t);
  const parseFile = path.join(root, 'vendor/braces/lib/parse.js');
  const source = fs.readFileSync(parseFile, 'utf8');
  const reverted = source.replaceAll(
    'if (nesting + 1 > maxDepth) {',
    'if (nesting >= maxDepth) {',
  );
  assert.notEqual(
    reverted,
    source,
    'Expected the fractional parser guard to be present for selective revert',
  );
  fs.writeFileSync(parseFile, reverted);
  regenerateFixtureMetadata(root);
  assert.throws(
    () => verifyArtifacts(root),
    /Reviewed code reverted\/changed: lib\/parse\.js/,
  );
});
test('reverted parent-cycle guard fails even with regenerated metadata', (t) => {
  const root = fixture(t);
  const expandFile = path.join(root, 'vendor/braces/lib/expand.js');
  const source = fs.readFileSync(expandFile, 'utf8');
  const reverted = source
    .replace(
      'const q = queueOwner(parent).queue;',
      [
        'let p = parent;',
        'let q = parent.queue;',
        '',
        "while (p.type !== 'brace' && p.type !== 'root' && p.parent) {",
        '  p = p.parent;',
        '  q = p.queue;',
        '}',
      ].join('\n'),
    )
    .replace(
      'const queue = queueOwner(node).queue;',
      [
        'let queue = node.queue;',
        'let block = node;',
        '',
        "while (block.type !== 'brace' && block.type !== 'root' && block.parent) {",
        '  block = block.parent;',
        '  queue = block.queue;',
        '}',
      ].join('\n'),
    );
  assert.notEqual(
    reverted,
    source,
    'Expected the parent-cycle guard to be present for selective revert',
  );
  fs.writeFileSync(expandFile, reverted);
  regenerateFixtureMetadata(root);
  assert.throws(
    () => verifyArtifacts(root),
    /Reviewed code reverted\/changed: lib\/expand\.js/,
  );
});
