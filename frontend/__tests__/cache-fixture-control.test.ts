// @vitest-environment node
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  identityFor,
  cleanupStartupFailure,
  serveAppGate,
  launchIdentity,
  ownChild,
  readFrames,
  verifyHTTP,
  waitFor,
} from '../e2e/cache-fixture-control.mjs';

const identity = { task: 'synthetic-task', nonce: 'a'.repeat(32) };
class Child extends EventEmitter {
  exitCode: number | null = null;
  signalCode: string | null = null;
  stdin = new PassThrough();
  stdio = [
    new PassThrough(),
    new PassThrough(),
    new PassThrough(),
    new PassThrough(),
  ];
  frame(event: string, extra = {}) {
    this.stdio[3].write(
      JSON.stringify({ ...identityFor(identity, 'next'), event, ...extra }) +
        '\n',
    );
  }
  exit(code = 0) {
    this.exitCode = code;
    this.emit('exit', code, null);
  }
}
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('private fixture ownership protocol', () => {
  it('preserves the startup cause when cleanup also fails, without hiding either', async () => {
    const startup = new Error('synthetic exclusive bind failure');
    const cleanup = new Error('synthetic dirty shutdown');
    const owner = { stop: vi.fn().mockRejectedValue(cleanup) };
    const result = cleanupStartupFailure(owner, startup);
    await expect(result).rejects.toMatchObject({
      cause: startup,
      errors: [startup, cleanup],
    });
    await expect(result).rejects.toThrow('synthetic exclusive bind failure');
    await expect(result).rejects.toThrow('synthetic dirty shutdown');
    expect(owner.stop).toHaveBeenCalledTimes(1);
  });
  it('rethrows the identical startup error when cleanup succeeds', async () => {
    const startup = new Error('synthetic startup failure');
    await expect(
      cleanupStartupFailure(
        { stop: vi.fn().mockResolvedValue(undefined) },
        startup,
      ),
    ).rejects.toBe(startup);
  });
  it.each([
    '/__fixture/seed',
    '/__fixture/reset',
    '/api/v1/auth/cache/clear',
    '/auth',
    '/__fixture/ready/' + identity.nonce,
  ])('keeps %s at503 throughout unarmed/warm-up/dead phases', (url) => {
    const response = { writeHead: vi.fn(), end: vi.fn() };
    expect(
      serveAppGate({ method: 'GET', url }, response, identity, false),
    ).toBe(true);
    expect(response.writeHead.mock.calls[0][0]).toBe(503);
  });
  it('exposes only exact nonce readiness and no normal routes before ready', () => {
    const response = { writeHead: vi.fn(), end: vi.fn() };
    serveAppGate(
      { method: 'GET', url: '/__fixture/ready/foreign' },
      response,
      identity,
      true,
    );
    expect(response.writeHead.mock.calls[0][0]).toBe(503);
    response.writeHead.mockClear();
    serveAppGate(
      { method: 'GET', url: '/__fixture/ready/' + identity.nonce },
      response,
      identity,
      true,
    );
    expect(response.writeHead.mock.calls[0][0]).toBe(200);
    expect(
      serveAppGate(
        { method: 'POST', url: '/__fixture/reset' },
        response,
        identity,
        true,
      ),
    ).toBe(false);
  });
  it('requires a valid task and fresh run identity, independent of application env', () => {
    expect(launchIdentity([identity.task, identity.nonce])).toEqual(identity);
    expect(() => launchIdentity(['', identity.nonce])).toThrow();
    expect(() => launchIdentity([identity.task, 'wrong'])).toThrow();
  });
  it.each([
    { nonce: 'b'.repeat(32) },
    { task: 'foreign' },
    { port: 3100 },
    { host: '0.0.0.0' },
    { role: 'api' },
  ])('rejects a mismatched bind receipt %j', async (extra) => {
    const child = new Child();
    const failures: unknown[] = [];
    const owner = ownChild(child, identity, 'next', (error: unknown) =>
      failures.push(error),
    );
    child.frame('bound', extra);
    await expect(owner.bound).rejects.toThrow('identity mismatch');
    expect(failures).toHaveLength(1);
    expect(child.stdin.read()).toBeNull(); // no arm/shutdown directed by a port lookup
    child.exit();
  });
  it('requires bound before arm and exact armed acknowledgement before warm-up', async () => {
    const child = new Child();
    const owner = ownChild(child, identity, 'next', vi.fn());
    await expect(owner.arm()).rejects.toThrow('bound');
    child.frame('bound');
    await owner.bound;
    const armed = owner.arm();
    expect(JSON.parse(child.stdin.read().toString())).toEqual({
      ...identity,
      event: 'arm',
    });
    child.frame('armed');
    await armed;
    owner.assertAlive();
    const stopped = owner.stop();
    expect(JSON.parse(child.stdin.read().toString())).toEqual({
      ...identity,
      event: 'shutdown',
    });
    child.exit();
    await stopped;
    await owner.stop();
    expect(() => owner.assertAlive()).toThrow('not live');
  });
  it('invalidates on receipt-channel EOF or child death during warm-up', async () => {
    const child = new Child();
    const failures = vi.fn();
    const owner = ownChild(child, identity, 'next', failures);
    child.frame('bound');
    await owner.bound;
    const arming = owner.arm();
    child.exit(1);
    await expect(arming).rejects.toThrow('exited');
    expect(failures).toHaveBeenCalled();
    expect(() => owner.assertAlive()).toThrow();
    child.stdio[3].end();
  });
  it('reports cleanup timeout without a signal/kill fallback', async () => {
    vi.useFakeTimers();
    const child = new Child();
    const owner = ownChild(child, identity, 'next', vi.fn());
    const result = expect(owner.stop()).rejects.toThrow('retained on timeout');
    await vi.advanceTimersByTimeAsync(10000);
    await result;
    expect(child.exitCode).toBeNull();
    child.exit();
  });
  it.each([
    [1, null],
    [null, 'SIGTERM'],
  ])(
    'rejects dirty shutdown exit %s/%s rather than reporting clean',
    async (code, signal) => {
      const child = new Child();
      const owner = ownChild(child, identity, 'next', vi.fn());
      const stopped = owner.stop();
      child.exitCode = code as number | null;
      child.signalCode = signal as string | null;
      child.emit('exit', code, signal);
      await expect(stopped).rejects.toThrow('shutdown failed');
      await expect(owner.stop()).rejects.toThrow('shutdown failed');
    },
  );
  it.each(['not-json\n', 'x'.repeat(4097)])(
    'refuses malformed or oversized frame',
    (frame) => {
      const pipe = new PassThrough();
      const accepted = vi.fn();
      const refused = vi.fn();
      readFrames(pipe, accepted, refused);
      pipe.write(frame);
      expect(refused).toHaveBeenCalled();
      expect(accepted).not.toHaveBeenCalled();
    },
  );
  it('uses exact HTTP identity and rejects redirects or spoofed successful status', async () => {
    const expected = identityFor(identity, 'next');
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ ...expected, nonce: 'foreign' })),
      );
    vi.stubGlobal('fetch', fetcher);
    await expect(verifyHTTP('http://127.0.0.1:3141', expected)).rejects.toThrow(
      'identity mismatch',
    );
    expect(fetcher.mock.calls[0][1].redirect).toBe('error');
    fetcher.mockResolvedValue(
      new Response(JSON.stringify(expected), { status: 403 }),
    );
    await expect(
      verifyHTTP('http://127.0.0.1:3141', expected),
    ).rejects.toThrow();
    fetcher.mockResolvedValue(new Response(JSON.stringify(expected)));
    await verifyHTTP('http://127.0.0.1:3141', expected);
  });
  it('keeps bounded startup failure rather than retrying the process', async () => {
    vi.useFakeTimers();
    const result = expect(
      waitFor(new Promise(() => {}), 90, 'synthetic'),
    ).rejects.toThrow('timeout');
    await vi.advanceTimersByTimeAsync(90);
    await result;
  });
});
