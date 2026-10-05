'use strict';

// Offline fixture tests for the upstream watch. Each accepted-baseline change
// and each lookup failure must fail closed, separately. The recorded baseline
// is a reviewed change-detection record, not a security guarantee.
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  ENDPOINTS,
  get,
  readBaseline,
  validate,
  main,
} = require('./watch-braces-upstream.cjs');

const baseline = readBaseline();
const acceptedRegistry = () => ({
  version: baseline.release.version,
  dist: {
    shasum: baseline.release.shasum,
    integrity: baseline.release.integrity,
  },
});
const acceptedPullRequest = () => ({
  head: { sha: baseline.pull_request.head },
  state: baseline.pull_request.state,
  merged: baseline.pull_request.merged,
});
const acceptedAdvisories = () => baseline.advisories.map((row) => ({ ...row }));
const acceptedObserved = () => ({
  registry: acceptedRegistry(),
  pullRequest: acceptedPullRequest(),
  advisories: acceptedAdvisories(),
});

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => JSON.parse(JSON.stringify(body)),
  };
}

test('accepted reviewed baseline passes validation', () => {
  validate(acceptedObserved(), baseline);
});

test('official release version change fails', () => {
  const observed = acceptedObserved();
  observed.registry.version = '3.0.4';
  assert.throws(() => validate(observed, baseline), /Official release changed/);
});

test('official release shasum change fails', () => {
  const observed = acceptedObserved();
  observed.registry.dist.shasum = '0'.repeat(40);
  assert.throws(
    () => validate(observed, baseline),
    /shasum missing or changed/,
  );
});

test('official release integrity change fails', () => {
  const observed = acceptedObserved();
  observed.registry.dist.integrity = 'sha512-bogus';
  assert.throws(
    () => validate(observed, baseline),
    /integrity missing or changed/,
  );
});

test('official release metadata missing fails', () => {
  const observed = acceptedObserved();
  delete observed.registry.dist;
  assert.throws(
    () => validate(observed, baseline),
    /shasum missing or changed/,
  );
  const missingVersion = acceptedObserved();
  delete missingVersion.registry.version;
  assert.throws(
    () => validate(missingVersion, baseline),
    /Official release changed/,
  );
});

for (const field of ['shasum', 'integrity']) {
  test(`official release ${field} missing fails`, () => {
    const observed = acceptedObserved();
    delete observed.registry.dist[field];
    assert.throws(() => validate(observed, baseline), /missing or changed/);
  });
}

test('upstream PR head change fails', () => {
  const observed = acceptedObserved();
  observed.pullRequest.head.sha = 'f'.repeat(40);
  assert.throws(() => validate(observed, baseline), /head missing or changed/);
});

test('upstream PR head missing fails', () => {
  const observed = acceptedObserved();
  delete observed.pullRequest.head;
  assert.throws(() => validate(observed, baseline), /head missing or changed/);
});

test('upstream PR state change fails', () => {
  const observed = acceptedObserved();
  observed.pullRequest.state = 'open';
  assert.throws(
    () => validate(observed, baseline),
    /PR status missing or changed/,
  );
});

test('upstream PR merge change fails', () => {
  const observed = acceptedObserved();
  observed.pullRequest.merged = true;
  assert.throws(
    () => validate(observed, baseline),
    /merge state missing or changed/,
  );
});

for (const field of ['state', 'merged']) {
  test(`upstream PR ${field} missing fails`, () => {
    const observed = acceptedObserved();
    delete observed.pullRequest[field];
    assert.throws(() => validate(observed, baseline), /missing or changed/);
  });
}

test('upstream PR head SHA missing fails', () => {
  const observed = acceptedObserved();
  delete observed.pullRequest.head.sha;
  assert.throws(() => validate(observed, baseline), /head missing or changed/);
});

test('additional advisory fails', () => {
  const observed = acceptedObserved();
  observed.advisories.push({
    ghsa_id: 'GHSA-zzzz-zzzz-zzzz',
    updated_at: '2026-10-05T00:00:00Z',
    withdrawn_at: null,
  });
  assert.throws(() => validate(observed, baseline), /advisories changed/);
});

test('advisory update timestamp change fails', () => {
  const observed = acceptedObserved();
  observed.advisories[0].updated_at = '2026-10-05T00:00:00Z';
  assert.throws(() => validate(observed, baseline), /advisories changed/);
});

test('advisory withdrawal fails', () => {
  const observed = acceptedObserved();
  observed.advisories[0].withdrawn_at = '2026-10-05T00:00:00Z';
  assert.throws(() => validate(observed, baseline), /advisories changed/);
});

test('advisory row missing a field fails', () => {
  const observed = acceptedObserved();
  delete observed.advisories[0].withdrawn_at;
  assert.throws(() => validate(observed, baseline), /advisories changed/);
});

for (const field of ['ghsa_id', 'updated_at']) {
  test(`advisory ${field} missing fails`, () => {
    const observed = acceptedObserved();
    delete observed.advisories[0][field];
    assert.throws(() => validate(observed, baseline));
  });
}

test('advisory removed from response fails', () => {
  const observed = acceptedObserved();
  observed.advisories = [];
  assert.throws(() => validate(observed, baseline), /advisories changed/);
});

test('null advisory row fails', () => {
  const observed = acceptedObserved();
  observed.advisories = [null];
  assert.throws(() => validate(observed, baseline));
});

test('full advisory page fails as incomplete', () => {
  const observed = acceptedObserved();
  observed.advisories = Array.from({ length: 100 }, (_, index) => ({
    ghsa_id: `GHSA-a${index}`,
    updated_at: null,
    withdrawn_at: null,
  }));
  assert.throws(
    () => validate(observed, baseline),
    /Incomplete advisory response/,
  );
});

test('malformed advisory payload fails', () => {
  const observed = acceptedObserved();
  observed.advisories = 'nope';
  assert.throws(
    () => validate(observed, baseline),
    /Advisory response missing or malformed/,
  );
});

test('missing observed data fails', () => {
  assert.throws(() => validate(null, baseline), /watch data missing/);
  assert.throws(
    () =>
      validate(
        {
          registry: null,
          pullRequest: acceptedPullRequest(),
          advisories: acceptedAdvisories(),
        },
        baseline,
      ),
    /Registry response missing/,
  );
  assert.throws(
    () =>
      validate(
        {
          registry: acceptedRegistry(),
          pullRequest: null,
          advisories: acceptedAdvisories(),
        },
        baseline,
      ),
    /PR response missing/,
  );
});

test('HTTP error responses fail closed', async () => {
  for (const status of [403, 404, 500, 503]) {
    await assert.rejects(
      main({
        fetchImpl: async (url) => jsonResponse({}, status),
        baseline,
      }),
      /Upstream watch unavailable/,
    );
  }
});

test('network failure fails closed', async () => {
  await assert.rejects(
    main({
      fetchImpl: async () => {
        throw new Error('getaddrinfo EAI_AGAIN (simulated)');
      },
      baseline,
    }),
    /EAI_AGAIN/,
  );
});

test('request timeout fails closed', async () => {
  await assert.rejects(
    main({
      fetchImpl: (url, init) =>
        new Promise((resolve, reject) => {
          init.signal.addEventListener('abort', () =>
            reject(init.signal.reason),
          );
        }),
      timeoutMs: 50,
      baseline,
    }),
    (error) =>
      error.name === 'TimeoutError' || /aborted|timeout/i.test(String(error)),
  );
});

test('invalid JSON response fails closed', async () => {
  await assert.rejects(
    main({
      fetchImpl: async () => ({
        ok: true,
        status: 200,
        json: async () => JSON.parse('{"version": '),
      }),
      baseline,
    }),
    SyntaxError,
  );
});

test('main resolves on the accepted baseline with injected fetch', async () => {
  await main({
    fetchImpl: async (url) => {
      assert(
        Object.values(ENDPOINTS).includes(url),
        `unexpected endpoint ${url}`,
      );
      if (url === ENDPOINTS.registry) return jsonResponse(acceptedRegistry());
      if (url === ENDPOINTS.pullRequest)
        return jsonResponse(acceptedPullRequest());
      return jsonResponse(acceptedAdvisories());
    },
    baseline,
  });
});

test('get() surfaces HTTP failure status', async () => {
  await assert.rejects(
    get('https://example.invalid', {
      fetchImpl: async () => jsonResponse({}, 502),
    }),
    /502/,
  );
});
