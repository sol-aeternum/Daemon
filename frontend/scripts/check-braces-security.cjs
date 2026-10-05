'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const { spawnSync } = require('node:child_process');
const zlib = require('node:zlib');

const FRONTEND = path.resolve(__dirname, '..');
const PATCH_SHA256 =
  'd472faf192fdc57bf4e2108df4587a3eb42d33d7f1f8e8b409c7defa3cf2d01c';
const ORIGINAL_SHA256 =
  '1cd18e862c8640b4568b1425a7df4ee030ff201d45b2da8f9f222d2987494ffc';
const NAME = '@daemon-internal/braces';
const VERSION = '3.0.3-daemon.2';
const ARCHIVE = 'vendor/daemon-internal-braces-3.0.3-daemon.2.tgz';
// Independently reviewed code bytes, separate from editable packaging metadata.
const FIXED_CODE = {
  'lib/constants.js':
    'f9fb688959232eee3e6ad7906a5b0e3234815db49ee857ef86983d65b917dc7c',
  'lib/parse.js':
    '72aabaadaa555cdfbd07fbd7c7f743373e4dc8eec04a97550cc57bbeec30eb6c',
  'lib/compile.js':
    'b651f7715e6db8942ce61d3394357b4d81c8ece88240aa31a458ea1165edd195',
  'lib/expand.js':
    '7ea3e14c2b2b256ef244fd3d83b8fcaa20aa2232b4e6d768c3bb6ab567f66cf5',
  'lib/stringify.js':
    '49dc2d8bafa74f34715a18a845bcb82ce66caaf3bab4cf117998e06b1f9a50a9',
};
const hash = (bytes, algorithm = 'sha256', encoding = 'hex') =>
  crypto.createHash(algorithm).update(bytes).digest(encoding);
const json = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));

/** Only small regular-file npm archives are accepted, never links or traversal. */
function archiveFiles(bytes) {
  const tar = zlib.gunzipSync(bytes, { maxOutputLength: 256 * 1024 });
  const files = new Map();
  for (let offset = 0; offset + 512 <= tar.length; ) {
    const header = tar.subarray(offset, offset + 512);
    if (header.every((byte) => byte === 0)) break;
    const name = header.subarray(0, 100).toString().split('\0')[0];
    const sizeText = header
      .subarray(124, 136)
      .toString()
      .replace(/\0/g, '')
      .trim();
    assert(/^[0-7]+$/.test(sizeText), 'Invalid archive size');
    const size = parseInt(sizeText, 8);
    assert(
      size <= 128 * 1024 && offset + 512 + size <= tar.length,
      'Truncated/oversized archive',
    );
    assert(
      name.startsWith('package/') &&
        !name.includes('..') &&
        !name.includes('\\'),
      'Invalid archive path',
    );
    assert(
      header[156] === 0 || header[156] === 48,
      'Archive must contain regular files only',
    );
    const relative = name.slice('package/'.length);
    assert(!files.has(relative), 'Duplicate archive member');
    files.set(relative, tar.subarray(offset + 512, offset + 512 + size));
    offset += 512 + Math.ceil(size / 512) * 512;
  }
  return files;
}

function checkFileSet(directory, expected) {
  for (const [relative, digest] of Object.entries(expected)) {
    assert(
      !path.isAbsolute(relative) && !relative.split('/').includes('..'),
      'Invalid provenance path',
    );
    const file = path.join(directory, relative);
    assert(fs.lstatSync(file).isFile(), `Expected regular file: ${relative}`);
    assert.equal(
      hash(fs.readFileSync(file)),
      digest,
      `Fixed bytes mismatch: ${relative}`,
    );
  }
}

function verifyArtifacts(frontend = FRONTEND) {
  const provenance = json(path.join(frontend, 'vendor/braces-provenance.json'));
  assert.equal(
    provenance.original.sha256,
    ORIGINAL_SHA256,
    'Original lineage changed',
  );
  assert.equal(provenance.patch.sha256, PATCH_SHA256, 'Reviewed patch changed');
  const original = fs.readFileSync(
    path.join(frontend, 'vendor/braces-3.0.3.tgz'),
  );
  assert.equal(hash(original), ORIGINAL_SHA256, 'Original archive changed');
  assert.equal(
    `sha512-${hash(original, 'sha512', 'base64')}`,
    provenance.original.integrity,
  );
  assert.equal(
    hash(
      fs.readFileSync(path.join(frontend, 'vendor/minimal-depth-guard.patch')),
    ),
    PATCH_SHA256,
  );
  const bytes = fs.readFileSync(path.join(frontend, ARCHIVE));
  assert.equal(
    hash(bytes),
    provenance.derivative.archive_sha256,
    'Derivative archive changed',
  );
  const integrity = `sha512-${hash(bytes, 'sha512', 'base64')}`;
  assert.equal(
    integrity,
    provenance.derivative.integrity,
    'Derivative integrity changed',
  );
  const members = archiveFiles(bytes);
  for (const [relative, digest] of Object.entries(FIXED_CODE)) {
    assert.equal(
      hash(members.get(relative)),
      digest,
      `Reviewed code reverted/changed: ${relative}`,
    );
  }
  assert.deepEqual(
    [...members.keys()].sort(),
    Object.keys(provenance.derivative.files).sort(),
    'Unexpected archive member',
  );
  for (const [relative, digest] of Object.entries(
    provenance.derivative.files,
  )) {
    assert.equal(
      hash(members.get(relative)),
      digest,
      `Archive source mismatch: ${relative}`,
    );
  }
  checkFileSet(
    path.join(frontend, 'vendor/braces'),
    provenance.derivative.files,
  );
  checkFileSet(
    path.join(frontend, 'vendor/braces/test'),
    provenance.release_tests.files,
  );
  assert.equal(
    hash(
      fs.readFileSync(
        path.join(frontend, 'vendor/braces-baseline-golden.json'),
      ),
    ),
    provenance.coverage.baseline_golden_sha256,
    'Baseline compatibility fixture changed',
  );
  const upstream = archiveFiles(original);
  assert.equal(
    hash(upstream.get('LICENSE')),
    provenance.original.license_sha256,
  );
  assert.deepEqual(
    members.get('LICENSE'),
    upstream.get('LICENSE'),
    'MIT attribution changed',
  );
  for (const relative of ['index.js', 'lib/utils.js']) {
    assert.deepEqual(
      members.get(relative),
      upstream.get(relative),
      `Unrelated code changed: ${relative}`,
    );
  }
  const manifest = JSON.parse(members.get('package.json').toString());
  assert.equal(manifest.name, NAME);
  assert.equal(manifest.version, VERSION);
  assert.equal(manifest.private, true);
  assert.equal(manifest.license, 'MIT');
  assert.equal(manifest.main, 'index.js');
  assert.equal(
    manifest.scripts,
    undefined,
    'No install/build hooks in derivative',
  );
  assert(
    members
      .get('README.md')
      .toString()
      .includes('NOT an official upstream release'),
  );
  const app = json(path.join(frontend, 'package.json'));
  assert.equal(app.dependencies.braces, `file:${ARCHIVE}`);
  assert.equal(app.overrides.braces, '$braces');
  const lock = json(path.join(frontend, 'package-lock.json'));
  const entry = lock.packages['node_modules/braces'];
  assert.equal(entry.name, NAME);
  assert.equal(entry.version, VERSION);
  assert.equal(
    entry.resolved,
    `file:${ARCHIVE}`,
    'Archive must resolve by committed relative path',
  );
  assert.equal(
    entry.integrity,
    integrity,
    'Lockfile must pin the exact archive',
  );
  return provenance;
}

/** Enumerate the real installed graph, not only the declared lockfile. */
function installedPackages(nodeModules, result = []) {
  for (const entry of fs.readdirSync(nodeModules, { withFileTypes: true })) {
    if (entry.name.startsWith('.')) continue;
    const directory = path.join(nodeModules, entry.name);
    assert(!entry.isSymbolicLink(), `Unexpected linked package: ${directory}`);
    if (entry.name.startsWith('@')) {
      installedPackages(directory, result);
      continue;
    }
    if (!entry.isDirectory()) continue;
    const manifest = path.join(directory, 'package.json');
    if (fs.existsSync(manifest))
      result.push({ directory, manifest: json(manifest) });
    const nested = path.join(directory, 'node_modules');
    if (fs.existsSync(nested)) installedPackages(nested, result);
  }
  return result;
}

function verifyInstallation(frontend, provenance) {
  const modules = path.join(frontend, 'node_modules');
  const installed = path.join(modules, 'braces');
  checkFileSet(installed, provenance.derivative.files);
  const expected = fs.realpathSync(path.join(installed, 'index.js'));
  const graph = installedPackages(modules);
  const consumers = [];
  let micromatch = 0;
  let chokidar = 0;
  let fastGlob = 0;
  for (const pkg of graph) {
    assert.notEqual(
      pkg.manifest.name,
      'braces',
      `Unpatched upstream copy remains: ${pkg.directory}`,
    );
    if (path.basename(pkg.directory) === 'braces') {
      assert.equal(
        fs.realpathSync(pkg.directory),
        fs.realpathSync(installed),
        'Unexpected nested braces copy',
      );
    }
    const requireFrom = createRequire(path.join(pkg.directory, 'package.json'));
    if (pkg.manifest.dependencies?.braces) {
      assert.equal(
        fs.realpathSync(requireFrom.resolve('braces')),
        expected,
        `Wrong consumer resolution: ${pkg.directory}`,
      );
      consumers.push(`${pkg.manifest.name}@${pkg.manifest.version}`);
      if (pkg.manifest.name === 'micromatch') micromatch++;
      if (pkg.manifest.name === 'chokidar') chokidar++;
    }
    if (pkg.manifest.name === 'fast-glob') {
      const matcher = requireFrom.resolve('micromatch');
      const matcherRequire = createRequire(matcher);
      assert.equal(
        fs.realpathSync(matcherRequire.resolve('braces')),
        expected,
        `Wrong fast-glob resolution: ${pkg.directory}`,
      );
      fastGlob++;
    }
  }
  assert(
    micromatch > 0 && chokidar > 0 && fastGlob > 0,
    'Expected glob/watch consumers missing',
  );
  return { installed, consumers, fastGlob };
}

function verify(frontend = FRONTEND) {
  const provenance = verifyArtifacts(frontend);
  const resolved = verifyInstallation(frontend, provenance);
  // The child has a deadline/heap bound, and loads ONLY hash-checked code.
  const checked = spawnSync(
    process.execPath,
    [
      '--max-old-space-size=64',
      path.join(FRONTEND, 'scripts/braces-depth-regressions.cjs'),
      resolved.installed,
      path.join(frontend, 'node_modules'),
    ],
    { timeout: 10000, maxBuffer: 1024 * 1024, encoding: 'utf8' },
  );
  assert.equal(
    checked.status,
    0,
    `Depth/consumer regressions failed: ${checked.error?.message || checked.stderr}`,
  );
  return { ...resolved, regressions: checked.stdout.trim() };
}

module.exports = { verify, verifyArtifacts, verifyInstallation, archiveFiles };
if (require.main === module) {
  try {
    console.log(
      JSON.stringify({ status: 'verified fixed derivative', ...verify() }),
    );
  } catch (error) {
    console.error(`Vendored braces security gate failed: ${error.message}`);
    process.exitCode = 1;
  }
}
