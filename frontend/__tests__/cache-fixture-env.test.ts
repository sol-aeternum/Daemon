import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { describe, expect, it } from 'vitest';

// Execute the actual environment construction/guard, without starting servers.
const source = readFileSync('e2e/cache-fixture-server.mjs', 'utf8');
const environment = source.slice(
  source.indexOf('const env ='),
  source.indexOf('const api = spawn('),
);

function fixtureEnv(options: string | undefined) {
  return runInNewContext(`${environment}\nenv;`, {
    process: {
      env: {
        PATH: '/synthetic/bin',
        HOME: '/synthetic/home',
        TMPDIR: '/synthetic/task/tmp',
        NODE_OPTIONS: options,
        OPENROUTER_API_KEY: 'synthetic-must-not-be-forwarded',
      },
    },
    apiOrigin: 'http://127.0.0.1:3143',
  }) as Record<string, string | undefined>;
}

describe('clean cache fixture child environment', () => {
  it.each([
    undefined,
    '',
    '--max-old-space-size=1',
    '--max-old-space-size=2048',
  ])(
    'preserves isolated temp storage and the safe heap control %s',
    (options) => {
      const env = fixtureEnv(options);
      expect(env.TMPDIR).toBe('/synthetic/task/tmp');
      expect(env.NODE_OPTIONS).toBe(options);
      expect(env.LITELLM_MODE).toBe('PRODUCTION');
      expect(env.OPENROUTER_API_KEY).toBeUndefined();
      expect(Object.keys(env).sort()).toEqual(
        [
          'PATH',
          'HOME',
          'TMPDIR',
          'NODE_OPTIONS',
          'PYTHONPATH',
          'LITELLM_MODE',
          'NEXT_TELEMETRY_DISABLED',
          'DAEMON_INTERNAL_API_URL',
          'NEXT_PUBLIC_API_URL',
        ].sort(),
      );
    },
  );

  it.each([
    '--require=/synthetic/preload.cjs',
    '--import=/synthetic/loader.mjs',
    '--inspect=0.0.0.0',
    '--max-old-space-size=2048 --require=/synthetic/preload.cjs',
    '--max-old-space-size=2049',
    '--max-old-space-size=0',
    '--max-old-space-size=-1',
    '--max-old-space-size=Infinity',
    '--max-old-space-size=2048\n',
  ])(
    'rejects unsafe/unbounded options before spawning children: %s',
    (options) => {
      expect(() => fixtureEnv(options)).toThrow('heap limit of 1–2048 MiB');
    },
  );
});
