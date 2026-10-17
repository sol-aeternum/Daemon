import { afterEach, describe, expect, it, vi } from 'vitest';
import { fixtureEnvironment } from '../e2e/cache-fixture-environment.mjs';

afterEach(() => vi.unstubAllEnvs());

function fixtureEnv(options: string | undefined) {
  vi.stubEnv('PATH', '/synthetic/bin');
  vi.stubEnv('HOME', '/synthetic/home');
  vi.stubEnv('TMPDIR', '/synthetic/task/tmp');
  vi.stubEnv('NODE_OPTIONS', options);
  vi.stubEnv('UV_PROJECT_ENVIRONMENT', '.uv-venv');
  vi.stubEnv('OPENROUTER_API_KEY', 'synthetic-must-not-be-forwarded');
  return fixtureEnvironment();
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
      expect(env).not.toHaveProperty('OPENROUTER_API_KEY');
      expect(Object.keys(env).sort()).toEqual(
        [
          'PATH',
          'HOME',
          'TMPDIR',
          'NODE_OPTIONS',
          'UV_PROJECT_ENVIRONMENT',
          'PYTHONPATH',
          'LITELLM_MODE',
          'NEXT_TELEMETRY_DISABLED',
          'DAEMON_INTERNAL_API_URL',
          'NEXT_PUBLIC_API_URL',
        ].sort(),
      );
    },
  );

  it('uses the standard temporary root when TMPDIR is absent, without a new setting', () => {
    fixtureEnv(undefined);
    vi.stubEnv('TMPDIR', undefined);
    expect(fixtureEnvironment().TMPDIR).toBeTruthy();
    expect(fixtureEnvironment()).not.toHaveProperty('OPENROUTER_API_KEY');
  });

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
