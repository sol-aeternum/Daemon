// Supported Playwright setup/returned-teardown keeps private pipes alive through
// the suite. There is deliberately no existing-server reuse path.
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  cleanupStartupFailure,
  ownChild,
  verifyHTTP,
  waitFor,
} from './cache-fixture-control.mjs';
import { fixtureEnvironment } from './cache-fixture-environment.mjs';

export default async function setup() {
  const identity = {
    task: path.basename(path.resolve('..')),
    nonce: randomBytes(16).toString('hex'),
  };
  let rejectFailed;
  const failed = new Promise((_, reject) => {
    rejectFailed = reject;
  });
  failed.catch(() => {});
  const child = spawn(
    process.execPath,
    ['e2e/cache-fixture-server.mjs', identity.task, identity.nonce],
    {
      env: fixtureEnvironment(),
      stdio: ['pipe', 'inherit', 'inherit', 'pipe'],
    },
  );
  const owner = ownChild(child, identity, 'app', rejectFailed);
  try {
    await waitFor(
      Promise.race([owner.bound, failed]),
      90000,
      'cache fixture startup',
    );
    owner.assertAlive();
    await verifyHTTP('http://127.0.0.1:3142', owner.expected);
    const response = await fetch(
      `http://127.0.0.1:3142/__fixture/ready/${identity.nonce}`,
      {
        redirect: 'error',
        signal: AbortSignal.timeout(2000),
      },
    );
    if (response.status !== 200)
      throw new Error('Owned cache fixture not ready');
    owner.assertAlive();
  } catch (error) {
    await cleanupStartupFailure(owner, error);
  }
  return async () => {
    await owner.stop();
    if (child.exitCode !== 0)
      throw new Error('Cache fixture exited with incomplete cleanup');
  };
}

// Deterministic collision regressions use exactly the production fixture launcher.
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const teardown = await setup();
  await teardown();
}
