// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  isCacheClearRequest,
  withoutServiceWorker,
} from '../e2e/online-ui-fixture';
import {
  isAcknowledgedPrefetchCancellation,
  isAcknowledgedRscPrefetch,
  isAdditionalRscPrefetch,
} from '../e2e/settings-rsc-fixture';
import {
  qualificationEnvironment,
  runQualification,
} from '../e2e/run-ui-qualification.mjs';

const origin = 'http://127.0.0.1:3181';
const headers = { rsc: '1', 'next-router-prefetch': '1' };
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('exact production page prefetch fixture', () => {
  for (const path of ['/settings/voice', '/settings/devices', '/auth']) {
    it(`admits observed RSC GET ${path}`, () => {
      expect(
        isAdditionalRscPrefetch(
          new URL(`${origin}${path}?_rsc=synthetic`),
          'GET',
          headers,
          origin,
        ),
      ).toBe(true);
    });
  }
  for (const [url, method, markers] of [
    [`https://foreign.test/settings/voice?_rsc=x`, 'GET', headers],
    [`${origin}/api/settings/voice?_rsc=x`, 'GET', headers],
    [`${origin}/settings/voice?_rsc=x`, 'POST', headers],
    [`${origin}/settings/voice`, 'GET', headers],
    [`${origin}/settings/voice?_rsc=x`, 'GET', {}],
    [`${origin}/settings/devices?_rsc=x`, 'GET', { rsc: '1' }],
  ] as const) {
    it(`refuses ${method} ${url} with ${JSON.stringify(markers)}`, () => {
      expect(
        isAdditionalRscPrefetch(new URL(url), method, markers, origin),
      ).toBe(false);
    });
  }
});

describe('cache acknowledgement fixture', () => {
  it('only acknowledges the exact same-origin POST', () => {
    const url = new URL('/api/v1/auth/cache/clear', origin);
    expect(isCacheClearRequest(url, 'POST', origin)).toBe(true);
    expect(isCacheClearRequest(url, 'GET', origin)).toBe(false);
    expect(isCacheClearRequest(url, 'POST', 'https://foreign.test')).toBe(
      false,
    );
    expect(isCacheClearRequest(new URL(`${url}?extra=1`), 'POST', origin)).toBe(
      false,
    );
    expect(
      isCacheClearRequest(
        new URL('/api/v1/auth/cache/clear/other', origin),
        'POST',
        origin,
      ),
    ).toBe(false);
  });
});

describe('narrow acknowledged prefetch cancellation', () => {
  const url = new URL(`${origin}/settings/voice?_rsc=x`);
  const paths = ['/settings/voice'];
  it('requires the matching marked 200 RSC response and exact cancellation', () => {
    const acknowledged = isAcknowledgedRscPrefetch(
      url,
      'GET',
      headers,
      origin,
      paths,
      200,
      'text/x-component',
    );
    expect(acknowledged).toBe(true);
    expect(
      isAcknowledgedPrefetchCancellation(acknowledged, 'net::ERR_ABORTED'),
    ).toBe(true);
    expect(isAcknowledgedPrefetchCancellation(false, 'net::ERR_ABORTED')).toBe(
      false,
    );
    expect(isAcknowledgedPrefetchCancellation(true, 'net::ERR_FAILED')).toBe(
      false,
    );
    expect(isAcknowledgedPrefetchCancellation(true, null)).toBe(false);
  });
  it('fails closed on every response/request boundary mismatch', () => {
    for (const [candidate, method, markers, allowed, status, contentType] of [
      [url, 'GET', headers, [], 200, 'text/x-component'],
      [url, 'GET', headers, paths, 404, 'text/x-component'],
      [url, 'GET', headers, paths, 200, 'text/html'],
      [url, 'POST', headers, paths, 200, 'text/x-component'],
      [url, 'GET', {}, paths, 200, 'text/x-component'],
      [
        new URL('/settings/voice', origin),
        'GET',
        headers,
        paths,
        200,
        'text/x-component',
      ],
      [
        new URL('https://foreign.test/settings/voice?_rsc=x'),
        'GET',
        headers,
        paths,
        200,
        'text/x-component',
      ],
      [
        new URL('/api/test?_rsc=x', origin),
        'GET',
        headers,
        ['/api/test'],
        200,
        'text/x-component',
      ],
    ] as const) {
      expect(
        isAcknowledgedRscPrefetch(
          candidate,
          method,
          markers,
          origin,
          [...allowed],
          status,
          contentType,
        ),
      ).toBe(false);
    }
  });
});

describe('explicit unsupported service-worker capability', () => {
  function browser(controller: object | null, configurable = true) {
    class Navigator {}
    Object.defineProperty(Navigator.prototype, 'serviceWorker', {
      value: { controller },
      configurable,
    });
    vi.stubGlobal('Navigator', Navigator);
    vi.stubGlobal('navigator', new Navigator());
  }
  it('removes unsupported capability before application scripts', () => {
    browser(null);
    withoutServiceWorker();
    expect('serviceWorker' in navigator).toBe(false);
  });
  it('refuses an active controller rather than hiding it', () => {
    browser({});
    expect(() => withoutServiceWorker()).toThrow('controller');
    expect('serviceWorker' in navigator).toBe(true);
  });
  it('refuses a capability that cannot be removed', () => {
    browser(null, false);
    expect(() => withoutServiceWorker()).toThrow('capability');
  });
});

describe('complete sequential browser qualification', () => {
  it('forwards only existing tooling controls and pins the isolated API origin', () => {
    const inherited = {
      PATH: '/synthetic/bin',
      HOME: '/synthetic/home',
      TMPDIR: '/synthetic/task/tmp',
      TMP: '/synthetic/task/tmp',
      TEMP: '/synthetic/task/tmp',
      PYTHONPYCACHEPREFIX: '/synthetic/task/pycache',
      UV_PROJECT_ENVIRONMENT: '/synthetic/task/venv',
      CI: '1',
    };
    for (const [key, value] of Object.entries(inherited))
      vi.stubEnv(key, value);
    vi.stubEnv('SYNTHETIC_SECRET', 'fictional-do-not-forward');
    vi.stubEnv('NODE_OPTIONS', '--import=/synthetic/injection.mjs');
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'https://foreign.test');
    expect(qualificationEnvironment()).toEqual({
      ...inherited,
      NEXT_PUBLIC_API_URL: origin,
    });
    const run = vi.fn<NonNullable<Parameters<typeof runQualification>[1]>>(
      () => ({ status: 0 }),
    );
    expect(runQualification('/synthetic/output', run)).toBe(0);
    for (const call of run.mock.calls) {
      expect(call[2].env).toEqual(qualificationEnvironment());
      expect(call[2].env).not.toHaveProperty('SYNTHETIC_SECRET');
      expect(call[2].env).not.toHaveProperty('NODE_OPTIONS');
    }
  });
  it.each([undefined, '', '1'])(
    'preserves the existing CI retry control %s',
    (value) => {
      vi.stubEnv('CI', value);
      expect(qualificationEnvironment().CI).toBe(value);
    },
  );
  it('runs development, fresh matching build, then unfiltered production', () => {
    const run = vi.fn<NonNullable<Parameters<typeof runQualification>[1]>>(
      () => ({ status: 0 }),
    );
    expect(runQualification('/synthetic/output', run)).toBe(0);
    expect(run.mock.calls.map((call) => call[1])).toEqual([
      [
        'run',
        'test:browser',
        '--',
        '--config=playwright.development.config.ts',
        '--output=/synthetic/output/development',
      ],
      ['run', 'build'],
      [
        'run',
        'test:browser',
        '--',
        '--config=playwright.production.config.ts',
        '--output=/synthetic/output/production',
      ],
    ]);
    for (const call of run.mock.calls)
      expect(call[2].env.NEXT_PUBLIC_API_URL).toBe(origin);
  });
  for (const failedStage of [0, 1, 2]) {
    it(`stops and preserves failure at stage ${failedStage}`, () => {
      let calls = 0;
      const run = vi.fn(() => ({ status: calls++ === failedStage ? 7 : 0 }));
      expect(runQualification('/synthetic/output', run)).toBe(7);
      expect(run).toHaveBeenCalledTimes(failedStage + 1);
    });
  }
  it('does not advance after a signalled child', () => {
    const run = vi.fn(() => ({ status: null, signal: 'SIGTERM' }));
    expect(runQualification('/synthetic/output', run)).toBe(1);
    expect(run).toHaveBeenCalledTimes(1);
  });
});
