// Private launch pipes are the authority for bind/arm/shutdown, never a PID/port.
import { createWriteStream } from 'node:fs';
import { once } from 'node:events';

export const ports = { app: 3142, next: 3141, api: 3143 };
export function launchIdentity(args = process.argv.slice(2)) {
  const [task, nonce] = args;
  if (
    args.length !== 2 ||
    !/^[a-f0-9]{32}$/.test(nonce ?? '') ||
    !/^[\w.-]{1,100}$/.test(task ?? '')
  )
    throw new Error('Missing valid cache fixture task/run identity');
  return { task, nonce };
}
export function identityFor(identity, role) {
  if (!Object.hasOwn(ports, role)) throw new Error('Unknown fixture role');
  return { ...identity, role, host: '127.0.0.1', port: ports[role] };
}
export function matches(actual, expected) {
  return (
    actual &&
    Object.keys(actual).sort().join() === Object.keys(expected).sort().join() &&
    Object.keys(expected).every((key) => actual[key] === expected[key])
  );
}
export function serveAppGate(request, response, identity, ready) {
  let status,
    body = '';
  const headers = { 'Cache-Control': 'no-store' };
  if (request.method === 'GET' && request.url === '/__fixture/identity') {
    status = 200;
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(identityFor(identity, 'app'));
  } else if (request.url?.startsWith('/__fixture/ready/')) {
    status =
      request.method === 'GET' &&
      ready &&
      request.url === `/__fixture/ready/${identity.nonce}`
        ? 200
        : 503;
  } else if (!ready) {
    status = 503;
    body = 'Fixture not ready';
  } else return false;
  response.writeHead(status, headers);
  response.end(body);
  return true;
}
export function readFrames(stream, onFrame, onFailure) {
  let buffer = '';
  stream.setEncoding('utf8');
  stream.on('data', (chunk) => {
    buffer += chunk;
    if (buffer.length > 4096)
      return onFailure(new Error('Oversize fixture frame'));
    while (buffer.includes('\n')) {
      const index = buffer.indexOf('\n');
      const line = buffer.slice(0, index);
      buffer = buffer.slice(index + 1);
      try {
        onFrame(JSON.parse(line));
      } catch (error) {
        onFailure(error);
      }
    }
  });
  stream.on('error', onFailure);
  stream.on('end', () => onFailure(new Error('Fixture control pipe closed')));
}
export function waitFor(promise, milliseconds, label) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(
        () => reject(new Error(`Fixture timeout: ${label}`)),
        milliseconds,
      );
    }),
  ]).finally(() => clearTimeout(timer));
}

// Preserve BOTH failures: neither a dirty shutdown nor the startup cause may be
// overwritten by the other. This helper does not change resource authority.
export async function cleanupStartupFailure(owner, startupError) {
  try {
    await owner.stop();
  } catch (cleanupError) {
    throw new AggregateError(
      [startupError, cleanupError],
      `Fixture startup failed: ${startupError}; cleanup failed: ${cleanupError}`,
      { cause: startupError },
    );
  }
  throw startupError;
}

export function ownChild(child, identity, role, onFailure, record = () => {}) {
  const expected = identityFor(identity, role);
  let phase = 'launching';
  let failed = false;
  let resolveBound, resolveArmed, rejectBound, rejectArmed;
  const bound = new Promise((resolve, reject) => {
    resolveBound = resolve;
    rejectBound = reject;
  });
  const armed = new Promise((resolve, reject) => {
    resolveArmed = resolve;
    rejectArmed = reject;
  });
  // Failure can precede awaiting either promise; don't create unhandled rejections.
  bound.catch(() => {});
  armed.catch(() => {});
  const exited = once(child, 'exit');
  exited.catch(() => {});
  function fail(error) {
    failed = true;
    rejectBound(error);
    rejectArmed(error);
    if (phase !== 'stopping') onFailure(error);
  }
  child.on('error', fail);
  child.on('exit', (code, signal) => {
    record('child-exit', { role, code, signal });
    fail(new Error(`Fixture ${role} exited (${code}/${signal})`));
  });
  child.stdin.on('error', fail);
  readFrames(
    child.stdio[3],
    (frame) => {
      const { event, ...actual } = frame;
      if (!matches(actual, expected))
        throw new Error('Fixture bind identity mismatch');
      if (event === 'bound' && phase === 'launching') {
        phase = 'bound';
        record('bound', actual);
        resolveBound();
      } else if (event === 'armed' && phase === 'arming') {
        phase = 'armed';
        resolveArmed();
      } else throw new Error('Unexpected fixture control phase');
    },
    fail,
  );
  return {
    expected,
    bound,
    assertAlive() {
      if (
        failed ||
        child.exitCode !== null ||
        child.signalCode !== null ||
        phase === 'stopping'
      )
        throw new Error(`Fixture ${role} is not live`);
    },
    async arm() {
      if (phase !== 'bound')
        throw new Error('Fixture must be bound before arming');
      phase = 'arming';
      child.stdin.write(JSON.stringify({ ...identity, event: 'arm' }) + '\n');
      await armed;
    },
    async stop() {
      if (phase !== 'stopping') {
        phase = 'stopping';
        if (child.exitCode === null && child.signalCode === null)
          child.stdin.end(
            JSON.stringify({ ...identity, event: 'shutdown' }) + '\n',
          );
      }
      const [code, signal] = await waitFor(
        exited,
        10000,
        `${role} shutdown (retained on timeout)`,
      );
      if (code !== 0 || signal !== null)
        throw new Error(`Fixture ${role} shutdown failed (${code}/${signal})`);
    },
  };
}

export async function verifyHTTP(origin, expected) {
  const response = await fetch(`${origin}/__fixture/identity`, {
    redirect: 'error',
    signal: AbortSignal.timeout(2000),
  });
  if (response.status !== 200 || !matches(await response.json(), expected))
    throw new Error('Fixture HTTP identity mismatch');
}

// Used only by the fixture-only Next child; Python uses the same wire contract.
export function childControl(identity, role, onStop) {
  let armed = false;
  let stopping = false;
  const receipts = createWriteStream('', { fd: 3, autoClose: false });
  const expected = identityFor(identity, role);
  const stop = () => {
    if (!stopping) {
      stopping = true;
      armed = false;
      Promise.resolve(onStop()).catch((error) => {
        console.error(error);
        process.exitCode = 1;
      });
    }
  };
  readFrames(
    process.stdin,
    (frame) => {
      const { event, ...actual } = frame;
      if (!matches(actual, identity))
        throw new Error('Invalid fixture command identity');
      if (event === 'shutdown') return stop();
      if (event !== 'arm' || stopping || armed)
        throw new Error('Invalid fixture command phase');
      armed = true;
      receipts.write(JSON.stringify({ ...expected, event: 'armed' }) + '\n');
    },
    (error) => {
      if (error.message !== 'Fixture control pipe closed') process.exitCode = 1;
      stop();
    },
  );
  process.on('SIGTERM', stop);
  process.on('SIGINT', stop);
  return {
    isArmed: () => armed && !stopping,
    bound(address) {
      if (address?.address !== expected.host || address?.port !== expected.port)
        throw new Error('Unexpected fixture listening address');
      receipts.write(JSON.stringify({ ...expected, event: 'bound' }) + '\n');
    },
    stop,
  };
}
