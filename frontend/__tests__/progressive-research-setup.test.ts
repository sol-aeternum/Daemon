import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import path from 'node:path';
import type { TestProject } from 'vitest/node';
import { execFileSync } from 'node:child_process';
import { performance } from 'node:perf_hooks';
import setup from './progressive-research-setup';

vi.mock('node:child_process', () => {
  const exports = { execFileSync: vi.fn() };
  return { ...exports, default: exports };
});
vi.mock('node:perf_hooks', () => {
  const exports = { performance: { now: vi.fn() } };
  return { ...exports, default: exports };
});
beforeEach(() => {
  // Only this mocked fixture: real setup still uses the managed absolute env.
  vi.stubEnv('UV_PROJECT_ENVIRONMENT', undefined);
});
afterEach(() => {
  vi.resetAllMocks();
  vi.unstubAllEnvs();
});

const fixtureRoot = '/synthetic';
function project() {
  return {
    config: { root: path.join(fixtureRoot, 'frontend') },
    provide: vi.fn(),
  };
}

it('fails setup on subprocess timeout rather than providing partial or cached evidence', () => {
  const p = project();
  vi.mocked(performance.now).mockReturnValue(0);
  vi.mocked(execFileSync).mockImplementation(() => {
    throw new Error('spawnSync ETIMEDOUT');
  });
  expect(() => setup(p as unknown as TestProject)).toThrow('ETIMEDOUT');
  expect(p.provide).not.toHaveBeenCalled();
  expect(execFileSync).toHaveBeenCalledWith(
    path.join(fixtureRoot, '.uv-venv', 'bin/python'),
    ['-m', 'tests.progressive_research_support'],
    expect.objectContaining({ timeout: 10_000, maxBuffer: 4 * 1024 * 1024 }),
  );
});

it.each([
  ['.fixture-env', '/synthetic/.fixture-env/bin/python'],
  ['/explicit-synthetic/env', '/explicit-synthetic/env/bin/python'],
])(
  'honors the explicit mocked environment %s',
  (environment, expectedPython) => {
    const p = project();
    vi.stubEnv('UV_PROJECT_ENVIRONMENT', environment);
    vi.mocked(performance.now).mockReturnValue(0);
    vi.mocked(execFileSync).mockReturnValue('{}');
    setup(p as unknown as TestProject);
    expect(execFileSync).toHaveBeenCalledWith(
      expectedPython,
      ['-m', 'tests.progressive_research_support'],
      expect.objectContaining({ timeout: 10_000, maxBuffer: 4 * 1024 * 1024 }),
    );
    expect(p.provide).toHaveBeenCalledOnce();
  },
);

it('also fails when total setup exceeds the old hook budget', () => {
  const p = project();
  vi.mocked(performance.now).mockReturnValueOnce(0).mockReturnValueOnce(10_001);
  vi.mocked(execFileSync).mockReturnValue('{}');
  expect(() => setup(p as unknown as TestProject)).toThrow('10000ms budget');
  expect(p.provide).not.toHaveBeenCalled();
});

it('rejects invalid backend output instead of substituting a transcript', () => {
  const p = project();
  vi.mocked(performance.now).mockReturnValue(0);
  vi.mocked(execFileSync).mockReturnValue('not json');
  expect(() => setup(p as unknown as TestProject)).toThrow();
  expect(p.provide).not.toHaveBeenCalled();
});

it('forwards only the existing literal process controls, retaining truthy omission and no credentials', () => {
  const p = project();
  vi.mocked(performance.now).mockReturnValue(0);
  vi.mocked(execFileSync).mockReturnValue('{}');
  vi.stubEnv('PATH', '/synthetic/bin');
  vi.stubEnv('HOME', '/synthetic/home');
  vi.stubEnv('TMPDIR', '/synthetic/tmp');
  vi.stubEnv('TMP', '');
  vi.stubEnv('TEMP', undefined);
  vi.stubEnv('PYTHONPYCACHEPREFIX', '/synthetic/bytecode');
  vi.stubEnv('OPENROUTER_API_KEY', 'synthetic-not-forwarded');
  setup(p as unknown as TestProject);
  expect(execFileSync).toHaveBeenCalledWith(
    expect.any(String),
    ['-m', 'tests.progressive_research_support'],
    expect.objectContaining({
      env: {
        NODE_ENV: 'test',
        LITELLM_MODE: 'PRODUCTION',
        HF_HUB_OFFLINE: '1',
        PYTHONPATH: '.',
        PATH: '/synthetic/bin',
        HOME: '/synthetic/home',
        TMPDIR: '/synthetic/tmp',
        PYTHONPYCACHEPREFIX: '/synthetic/bytecode',
      },
    }),
  );
});
