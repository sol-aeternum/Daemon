// @vitest-environment node
import { describe, expect, it } from 'vitest';
import {
  allowedPage,
  assertLoopbackListeners,
  assertPrivateStat,
  browserEnvironment,
  main,
  parseListeners,
  safeMessage,
  SafetyError,
  terminateOwnedProcess,
  validateEndpoint,
} from '../scripts/authenticated-browser.mjs';

describe('authenticated browser safety boundary', () => {
  it('does not give application credentials or debug injection variables to Chromium', () => {
    expect(
      browserEnvironment({
        HOME: '/home/test',
        PATH: '/usr/bin',
        DISPLAY: ':0',
        OPENROUTER_API_KEY: 'secret',
        NODE_OPTIONS: '--require private',
        DEBUG: 'pw:*',
      }),
    ).toEqual({ HOME: '/home/test', PATH: '/usr/bin', DISPLAY: ':0' });
  });

  it('does not signal a stale owner and escalates only the live owned process', async () => {
    const signals: string[] = [];
    const state = { pid: 123 };
    const signal = (pid: number, value: string) => {
      expect(pid).toBe(123);
      signals.push(value);
    };
    await terminateOwnedProcess(state, {
      isLive: async () => false,
      signal,
      wait: async () => {},
    });
    expect(signals).toEqual([]);
    let running = true;
    await terminateOwnedProcess(state, {
      isLive: async () => running,
      signal: (pid: number, value: string) => {
        signal(pid, value);
        if (value === 'SIGKILL') running = false;
      },
      wait: async () => {},
    });
    expect(signals).toEqual(['SIGTERM', 'SIGKILL']);
  });

  it('reports cleanup failure rather than claiming a hung endpoint is closed', async () => {
    await expect(
      terminateOwnedProcess(
        { pid: 123 },
        { isLive: async () => true, signal: () => {}, wait: async () => {} },
      ),
    ).rejects.toThrow(SafetyError);
  });

  it('accepts only the exact local application, not login or unrelated tabs', () => {
    expect(allowedPage('about:blank')).toBe(true);
    expect(allowedPage('http://localhost:3000/?id=private')).toBe(true);
    expect(allowedPage('http://localhost:3000/settings/profile')).toBe(true);
    for (const url of [
      'http://localhost:3000/auth',
      'http://localhost:3000/setup',
      'http://localhost:3000/landing',
      'http://localhost:3000/auth/google',
      'http://localhost:3000.evil.test/',
      'http://user:secret@localhost:3000/',
      'http://localhost:8000/',
      'https://accounts.google.com/',
      'file:///etc/passwd',
      'chrome://newtab/',
      'invalid',
    ]) {
      expect(allowedPage(url)).toBe(false);
    }
  });

  it('rejects alternate endpoints, credentials, redirects and endpoint suffixes', () => {
    const good = 'ws://127.0.0.1:9227/devtools/browser/a1-b2';
    expect(validateEndpoint(good)).toBe(good);
    for (const value of [
      'wss://127.0.0.1:9227/devtools/browser/a1',
      'ws://localhost:9227/devtools/browser/a1',
      'ws://127.0.0.1:9228/devtools/browser/a1',
      'ws://evil.test:9227/devtools/browser/a1',
      `${good}?token=secret`,
      `${good}#secret`,
      'ws://user:secret@127.0.0.1:9227/devtools/browser/a1',
      'ws://127.0.0.1:9227/other',
      'invalid',
    ]) {
      expect(() => validateEndpoint(value)).toThrow(SafetyError);
    }
  });

  it('requires an exclusive exact loopback listener across both IP families', () => {
    const header = 'sl local_address rem_address st other';
    const row = '0: 0100007F:240B 00000000:0000 0A 0 0 0 1000 0 12345';
    const listeners = parseListeners(`${header}\n${row}`, 'tcp');
    expect(listeners).toEqual([
      { family: 'tcp', address: '0100007F', inode: '12345' },
    ]);
    expect(() => assertLoopbackListeners(listeners)).not.toThrow();
    expect(
      parseListeners(`${header}\n${row.replace('0A', '01')}`, 'tcp'),
    ).toEqual([]);
    for (const rows of [
      [],
      [...listeners, ...listeners],
      [{ ...listeners[0], address: '00000000' }],
      [{ ...listeners[0], family: 'tcp6' }],
    ]) {
      expect(() => assertLoopbackListeners(rows)).toThrow(SafetyError);
    }
  });

  it('refuses unsafe directory/file ownership, permissions and links', () => {
    const stat = {
      uid: 1000,
      mode: 0o700,
      nlink: 1,
      isSymbolicLink: () => false,
      isDirectory: () => true,
      isFile: () => false,
    };
    expect(() => assertPrivateStat(stat, true, 1000)).not.toThrow();
    for (const patch of [
      { uid: 0 },
      { mode: 0o755 },
      { isSymbolicLink: () => true },
      { isDirectory: () => false },
    ]) {
      expect(() =>
        assertPrivateStat({ ...stat, ...patch }, true, 1000),
      ).toThrow(SafetyError);
    }
    const file = {
      ...stat,
      mode: 0o600,
      isDirectory: () => false,
      isFile: () => true,
    };
    expect(() => assertPrivateStat(file, false, 1000)).not.toThrow();
    expect(() => assertPrivateStat({ ...file, nlink: 2 }, false, 1000)).toThrow(
      SafetyError,
    );
  });

  it('suppresses raw errors and requires an explicit owner-ready command', async () => {
    expect(safeMessage(new Error('Bearer secret; private URL'))).not.toContain(
      'secret',
    );
    expect(safeMessage(new SafetyError('Known safe message.'))).toBe(
      'Known safe message.',
    );
    await expect(main(['smoke'])).rejects.toThrow(SafetyError);
    await expect(main(['probe', '--owner-ready', '--trace'])).rejects.toThrow(
      SafetyError,
    );
  });
});
