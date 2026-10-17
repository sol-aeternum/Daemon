import { afterEach, expect, it, vi } from 'vitest';
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
afterEach(() => vi.resetAllMocks());

function project() {
  return { config: { root: '/synthetic/frontend' }, provide: vi.fn() };
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
    '/synthetic/.uv-venv/bin/python',
    ['-m', 'tests.progressive_research_support'],
    expect.objectContaining({ timeout: 10_000, maxBuffer: 4 * 1024 * 1024 }),
  );
});

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
