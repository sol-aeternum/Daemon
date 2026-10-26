'use strict';

// Temporary Next 16.4.1 Webpack SYNC recipient and fallback-compiler correction.
// Never run concurrently with a package writer or a live dev server.
const fs = require('node:fs');
const path = require('node:path');
const { createHash, randomBytes } = require('node:crypto');

const serverTargets = [
  {
    file: 'dist/server/dev/hot-middleware.js',
    before: 'c62af1c040758e369dbdefbf6df5782bfc77124c52ce7be7cd8885078731b2ca',
    after: 'a63c95269e086c50988f86bf17af91da106b688ffab72aa12640cd72510f3e1a',
    symbol: '_hotreloadertypes.HMR_MESSAGE_SENT_TO_BROWSER.SYNC',
  },
  {
    file: 'dist/esm/server/dev/hot-middleware.js',
    before: '1ddaa00c7df885f54fb9696dfb0bb3fc42dd4d2b1cefe37d84c930367a37644a',
    after: '8dfd6be25ba49debfec59a730622fb60056a2c67b11b3275f4bf65a336260ab4',
    symbol: 'HMR_MESSAGE_SENT_TO_BROWSER.SYNC',
  },
].map((target) => ({
  ...target,
  edits: [
    {
      before: `this.publish({\n                    type: ${target.symbol},`,
      after: `this.publishToClient(client, {\n                    type: ${target.symbol},`,
    },
  ],
}));
const clientTargets = [
  {
    file: 'dist/client/dev/hot-reloader/pages/hot-reloader-pages.js',
    before: '2ee7dacf7c6f0e9f6cbe02bc7d384147770c7b3e3025899b3565757c7287d1c5',
    after: 'b7ceb41b67f1f32569b5c744ed07029c3c446d378c085cdb85d1c4e4f1310b30',
    dispatcher: '_nextdevtools.dispatcher',
  },
  {
    file: 'dist/esm/client/dev/hot-reloader/pages/hot-reloader-pages.js',
    before: '66793d7a635c27a59e075303815cf27f67ca9755730aa3de9b2c0f2a1b4cb306',
    after: '7edb61c46cf3072ee39d81ab18b7f62a310a7a6fa31706034fae91190ed22676',
    dispatcher: 'dispatcher',
  },
].map((target) => {
  const gate = `    if (!isUpdateAvailable() || !canApplyUpdates()) {\n        ${target.dispatcher}.onBuildOk();\n        return;\n    }`;
  const next = '\n    function handleApplyUpdates(err, updatedModules) {';
  return {
    ...target,
    edits: [
      {
        before:
          'var hasCompileErrors = false;\nfunction clearOutdatedErrors() {',
        after:
          'var hasCompileErrors = false;\nvar isFallbackReloading = false;\nfunction clearOutdatedErrors() {',
      },
      {
        before: gate + next,
        after:
          gate.slice(0, -1) +
          `} else if (process.env.NODE_ENV === 'development' && window.__NEXT_DATA__.page === '/_error' && Array.from(document.scripts).some((script)=>script.src && new URL(script.src).pathname.endsWith('/_next/static/chunks/fallback/webpack.js'))) {\n        // The fallback runtime belongs to a separate compiler. Main-compiler\n        // notifications cannot supply updates for its hash; recover by reload.\n        if (!isFallbackReloading) {\n            isFallbackReloading = true;\n            performFullReload();\n        }\n        return;\n    }` +
          next,
      },
    ],
  };
});
const targets = Object.freeze([...serverTargets, ...clientTargets]);
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');

function ordinary(root, relative, kind, io = fs) {
  const resolved = path.resolve(root);
  if (io.realpathSync(resolved) !== resolved) throw new Error('Non-real root');
  let current = resolved;
  for (const component of relative.split('/').filter(Boolean)) {
    current = path.join(current, component);
    const stat = io.lstatSync(current);
    if (stat.isSymbolicLink()) throw new Error(`Symlink refused: ${current}`);
    if (current !== path.join(resolved, relative) && !stat.isDirectory())
      throw new Error(`Non-directory parent: ${current}`);
  }
  const stat = io.lstatSync(current);
  if (!(kind === 'directory' ? stat.isDirectory() : stat.isFile()))
    throw new Error(`Unexpected ${kind}: ${current}`);
  if (stat.uid !== process.getuid())
    throw new Error(`Foreign owner: ${current}`);
  return { file: current, stat };
}

function generatedDirectory(root, relative, io) {
  let current = root;
  for (const part of relative.split('/')) {
    current = path.join(current, part);
    try {
      io.mkdirSync(current, { mode: 0o700 });
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
    }
    ordinary(root, path.relative(root, current), 'directory', io);
  }
  return current;
}

function inspectInstallation(root, io = fs) {
  root = path.resolve(root);
  ordinary(root, 'node_modules/next', 'directory', io);
  const metadata = ordinary(root, 'node_modules/next/package.json', 'file', io);
  const pkg = JSON.parse(io.readFileSync(metadata.file, 'utf8'));
  if (pkg.name !== 'next' || pkg.version !== '16.4.1')
    throw new Error('Unsupported package name/version (requires next 16.4.1)');
  const artifacts = targets.map((target) => {
    const { file, stat } = ordinary(
      root,
      `node_modules/next/${target.file}`,
      'file',
      io,
    );
    const bytes = io.readFileSync(file);
    const digest = hash(bytes);
    const state =
      digest === target.before
        ? 'original'
        : digest === target.after
          ? 'patched'
          : 'unknown';
    if (state === 'unknown')
      throw new Error(`Unknown bytes: ${target.file} ${digest}`);
    const text = bytes.toString('utf8');
    let transformed = text;
    for (const edit of target.edits) {
      const expected = state === 'original' ? edit.before : edit.after;
      const forbidden = state === 'original' ? edit.after : edit.before;
      if (text.split(expected).length !== 2 || text.includes(forbidden))
        throw new Error(`Ambiguous seam: ${target.file}`);
      if (state === 'original')
        transformed = transformed.replace(edit.before, edit.after);
    }
    const output = Buffer.from(transformed);
    if (hash(output) !== target.after)
      throw new Error('Conflicting output hash');
    return {
      ...target,
      file,
      bytes,
      output,
      digest,
      state,
      mode: stat.mode & 0o777,
      device: stat.dev,
    };
  });
  const states = artifacts.map((artifact) => artifact.state);
  const state = states.every((value) => value === 'original')
    ? 'original'
    : states.every((value) => value === 'patched')
      ? 'patched'
      : states.join(',') === 'patched,patched,original,original'
        ? 'server-only'
        : null;
  if (!state) throw new Error('Mixed/partial installation');
  return { root, state, artifacts };
}

function assertNoLock(root, io) {
  const dir = path.join(root, '.hmr-runs');
  try {
    io.lstatSync(dir);
  } catch (error) {
    if (error.code === 'ENOENT') return;
    throw error;
  }
  ordinary(root, '.hmr-runs', 'directory', io);
  try {
    io.lstatSync(path.join(dir, 'patch.lock'));
  } catch (error) {
    if (error.code === 'ENOENT') return;
    throw error;
  }
  throw new Error(
    'Patch lock/in-progress state; retain evidence and use a fresh locked install',
  );
}

function checkPatched(root, io = fs) {
  assertNoLock(root, io);
  const result = inspectInstallation(root, io);
  if (result.state !== 'patched')
    throw new Error('Installation is not fully patched');
  // A cooperative preparer starting during inspection also makes check fail.
  assertNoLock(root, io);
  return result;
}

function applyPatch(root, io = fs) {
  root = path.resolve(root);
  const generated = generatedDirectory(root, '.hmr-runs', io);
  const lock = path.join(generated, 'patch.lock');
  const nonce = randomBytes(16).toString('hex');
  const receipt = JSON.stringify({ nonce, pid: process.pid, root });
  // Exclusive creation: never remove or repair somebody else's lock.
  const fd = io.openSync(lock, 'wx', 0o600);
  const identity = io.fstatSync(fd);
  let staging;
  try {
    io.writeFileSync(fd, receipt);
    io.fsyncSync(fd);
    io.closeSync(fd);
    const installation = inspectInstallation(root, io);
    if (installation.state !== 'patched') {
      const pending = installation.artifacts.filter(
        (artifact) => artifact.state === 'original',
      );
      generatedDirectory(root, '.hmr-runs/patch-staging', io);
      staging = path.join(generated, 'patch-staging', nonce);
      io.mkdirSync(staging, { mode: 0o700 });
      const stageStat = io.lstatSync(staging);
      for (const [index, artifact] of pending.entries()) {
        if (stageStat.dev !== artifact.device)
          throw new Error('Staging is not on target filesystem');
        const staged = path.join(staging, `${index}.js`);
        const stageFd = io.openSync(staged, 'wx', artifact.mode);
        try {
          io.writeFileSync(stageFd, artifact.output);
          io.fchmodSync(stageFd, artifact.mode);
          io.fsyncSync(stageFd);
        } finally {
          io.closeSync(stageFd);
        }
        if (
          hash(io.readFileSync(staged)) !== artifact.after ||
          (io.lstatSync(staged).mode & 0o777) !== artifact.mode
        )
          throw new Error('Stage hash/mode mismatch');
      }
      for (const [index, artifact] of pending.entries()) {
        ordinary(
          root,
          `node_modules/next/${artifact.file.split(`${path.sep}next${path.sep}`)[1]}`,
          'file',
          io,
        );
        if (hash(io.readFileSync(artifact.file)) !== artifact.before)
          throw new Error('Preimage changed before rename');
        // These renames are deliberately NOT claimed as a multi-file transaction.
        io.renameSync(path.join(staging, `${index}.js`), artifact.file);
      }
    }
    const final = inspectInstallation(root, io);
    if (final.state !== 'patched')
      throw new Error('Final readback not fully patched');
    const lockStat = io.lstatSync(lock);
    if (
      lockStat.isSymbolicLink() ||
      lockStat.dev !== identity.dev ||
      lockStat.ino !== identity.ino ||
      lockStat.uid !== process.getuid() ||
      io.readFileSync(lock, 'utf8') !== receipt
    )
      throw new Error('Lock identity changed; retaining interrupted state');
    io.unlinkSync(lock);
    return final;
  } catch (error) {
    // Do not roll back, repair, erase a stale lock, or discard interrupted output.
    try {
      io.writeFileSync(
        path.join(generated, `patch-failure-${nonce}.json`),
        JSON.stringify({ nonce, staging, error: String(error) }) + '\n',
        { flag: 'wx', mode: 0o600 },
      );
    } catch {
      /* The original failure remains authoritative even if receipt IO fails. */
    }
    throw error;
  }
}

module.exports = {
  inspectInstallation,
  applyPatch,
  checkPatched,
  targets,
  hash,
};
if (require.main === module) {
  try {
    if (
      process.argv.length !== 3 ||
      !['--apply', '--check'].includes(process.argv[2])
    )
      throw new Error(
        'Usage: node scripts/patch-next-hmr.cjs --apply|--check (no target override)',
      );
    const root = path.resolve(__dirname, '..');
    const result =
      process.argv[2] === '--apply' ? applyPatch(root) : checkPatched(root);
    console.log(
      JSON.stringify({
        state: result.state,
        artifacts: result.artifacts.map(({ file, digest }) => ({
          file,
          sha256: digest,
        })),
      }),
    );
  } catch (error) {
    console.error(`Next HMR preparation refused: ${error.message}`);
    process.exitCode = 1;
  }
}
