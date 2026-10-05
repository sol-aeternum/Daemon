'use strict';

// Public upstream metadata only. No token, application data or writes required.
// The recorded baseline in vendor/braces-provenance.json is a reviewed
// change-detection record, NOT a security guarantee; it is never silently
// refreshed here, and any change, missing field or lookup failure fails closed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const DEFAULT_TIMEOUT_MS = 15000;
const ENDPOINTS = {
  registry: 'https://registry.npmjs.org/braces/latest',
  pullRequest: 'https://api.github.com/repos/micromatch/braces/pulls/72',
  advisories:
    'https://api.github.com/advisories?ecosystem=npm&affects=braces%403.0.3&per_page=100',
};

async function get(
  url,
  { fetchImpl = fetch, timeoutMs = DEFAULT_TIMEOUT_MS } = {},
) {
  const response = await fetchImpl(url, {
    headers: {
      accept: 'application/json',
      'user-agent': 'Daemon-vendored-braces-watch',
    },
    signal: AbortSignal.timeout(timeoutMs),
  });
  assert(response.ok, `Upstream watch unavailable: ${response.status} ${url}`);
  return response.json();
}

function readBaseline(
  provenancePath = path.resolve(__dirname, '../vendor/braces-provenance.json'),
) {
  const proof = JSON.parse(fs.readFileSync(provenancePath, 'utf8'));
  const baseline = proof && proof.watch;
  assert(
    baseline && typeof baseline === 'object',
    'Watch baseline missing from provenance',
  );
  assert(
    baseline.release &&
      typeof baseline.release === 'object' &&
      baseline.release.version &&
      baseline.release.shasum &&
      baseline.release.integrity,
    'Watch release baseline missing or incomplete',
  );
  assert(
    baseline.pull_request &&
      typeof baseline.pull_request === 'object' &&
      baseline.pull_request.head &&
      typeof baseline.pull_request.state === 'string' &&
      typeof baseline.pull_request.merged === 'boolean',
    'Watch PR baseline missing or incomplete',
  );
  assert(
    Array.isArray(baseline.advisories),
    'Watch advisory baseline missing or incomplete',
  );
  return baseline;
}

function validate(observed, baseline) {
  assert(
    observed && typeof observed === 'object',
    'Upstream watch data missing',
  );
  const { registry, pullRequest, advisories } = observed;

  // Official release identity: any change or missing field requires
  // reassessment before the derivative keeps being used.
  assert(registry && typeof registry === 'object', 'Registry response missing');
  assert.equal(
    registry.version,
    baseline.release.version,
    'Official release changed: reassess and retire derivative only after verified replacement',
  );
  assert.equal(
    registry.dist && registry.dist.shasum,
    baseline.release.shasum,
    'Official release shasum missing or changed: reassess',
  );
  assert.equal(
    registry.dist && registry.dist.integrity,
    baseline.release.integrity,
    'Official release integrity missing or changed: reassess',
  );

  // Reviewed PR state: head, state and merge flag must all be present.
  assert(
    pullRequest && typeof pullRequest === 'object',
    'Upstream PR response missing',
  );
  assert.equal(
    pullRequest.head && pullRequest.head.sha,
    baseline.pull_request.head,
    'Upstream patch head missing or changed: security reassessment required',
  );
  assert.equal(
    pullRequest.state,
    baseline.pull_request.state,
    'Upstream PR status missing or changed: reassess',
  );
  assert.equal(
    pullRequest.merged,
    baseline.pull_request.merged,
    'Upstream PR merge state missing or changed: review official release path',
  );

  // Advisories: complete page, fully formed rows, exact reviewed mapping.
  assert(Array.isArray(advisories), 'Advisory response missing or malformed');
  assert(advisories.length < 100, 'Incomplete advisory response');
  const rows = advisories
    .map(({ ghsa_id, updated_at, withdrawn_at }) => ({
      ghsa_id,
      updated_at,
      withdrawn_at,
    }))
    .sort((a, b) => a.ghsa_id.localeCompare(b.ghsa_id));
  assert.deepEqual(
    rows,
    baseline.advisories,
    'Upstream advisories changed: reassess derivative exposure and coverage',
  );
}

async function main({ fetchImpl, timeoutMs, baseline } = {}) {
  const reviewed = baseline || readBaseline();
  const [registry, pullRequest, advisories] = await Promise.all([
    get(ENDPOINTS.registry, { fetchImpl, timeoutMs }),
    get(ENDPOINTS.pullRequest, { fetchImpl, timeoutMs }),
    get(ENDPOINTS.advisories, { fetchImpl, timeoutMs }),
  ]);
  validate({ registry, pullRequest, advisories }, reviewed);
  console.log(
    'Upstream braces release, PR and advisory mapping unchanged; not a security guarantee.',
  );
}

module.exports = {
  DEFAULT_TIMEOUT_MS,
  ENDPOINTS,
  get,
  readBaseline,
  validate,
  main,
};
if (require.main === module) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
