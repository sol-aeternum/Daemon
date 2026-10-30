import { describe, expect, it } from 'vitest';
import inventory from '../playwright.config';
import production from '../playwright.production.config';
import development from '../playwright.development.config';

describe('production browser qualification configuration', () => {
  it('retains every default inventory project across the two isolated phases', () => {
    const combined = [
      ...(development.projects ?? []),
      ...(production.projects ?? []),
    ];
    for (const project of inventory.projects ?? []) {
      expect(
        combined.find((candidate) => candidate.name === project.name),
      ).toEqual({
        ...project,
        use: {
          ...project.use,
          baseURL:
            project.name === 'csp-previews'
              ? 'http://127.0.0.1:3180'
              : 'http://127.0.0.1:3181',
        },
      });
      expect(
        combined.filter((candidate) => candidate.name === project.name),
      ).toHaveLength(1);
    }
    expect(production.projects?.map((project) => project.name)).toContain(
      'midnight',
    );
    expect(production.projects?.map((project) => project.name)).toContain(
      'contextual-home',
    );
  });

  it('executes the intact development-only spec separately instead of on production', () => {
    expect(
      development.projects?.map((project) => [project.name, project.testMatch]),
    ).toEqual([['development-runtime', 'dev-runtime-observability.spec.ts']]);
    expect(
      production.projects?.some(
        (project) => project.name === 'development-runtime',
      ),
    ).toBe(false);
    expect(development.webServer).toEqual({
      command: 'node_modules/.bin/next dev -H 127.0.0.1 -p 3181 --webpack',
      url: 'http://127.0.0.1:3181',
      env: { NEXT_PUBLIC_API_URL: 'http://127.0.0.1:3181' },
      reuseExistingServer: false,
      timeout: 120_000,
    });
    expect(development.retries).toBe(inventory.retries);
    expect(development.workers).toBe(inventory.workers);
    expect(development.timeout).toBe(inventory.timeout);
    expect(development.expect).toEqual(inventory.expect);
    expect(development.fullyParallel).toBe(inventory.fullyParallel);
  });

  it('preserves inventory timing, concurrency, retries and trace settings', () => {
    expect(production.fullyParallel).toBe(inventory.fullyParallel);
    expect(production.workers).toBe(inventory.workers);
    expect(production.retries).toBe(inventory.retries);
    expect(production.timeout).toBe(inventory.timeout);
    expect(production.expect).toEqual(inventory.expect);
    expect(production.use?.trace).toBe(inventory.use?.trace);
    // Existing production policy: API fixtures cannot be bypassed by SW fetches.
    expect(production.use?.serviceWorkers).toBe('block');
  });

  it('owns both servers and serves the main app in production at the fixture origin', () => {
    const servers = production.webServer;
    if (!Array.isArray(servers)) throw new Error('Expected two owned servers');
    expect(servers).toHaveLength(2);
    expect(servers[0]).toEqual({
      command:
        'node_modules/.bin/next dev e2e/csp-preview-app -H 127.0.0.1 -p 3180 --webpack',
      url: 'http://127.0.0.1:3180',
      reuseExistingServer: false,
      timeout: 120_000,
    });
    expect(servers[1]).toEqual({
      command: 'npm run start -- -H 127.0.0.1 -p 3181',
      url: 'http://127.0.0.1:3181',
      env: { NEXT_PUBLIC_API_URL: 'http://127.0.0.1:3181' },
      reuseExistingServer: false,
      timeout: 120_000,
    });
  });
});
