'use strict';

// Public upstream metadata only. No token, application data or writes required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
async function get(url) {
  const response = await fetch(url, {
    headers: {
      accept: 'application/json',
      'user-agent': 'Daemon-vendored-braces-watch',
    },
    signal: AbortSignal.timeout(15000),
  });
  assert(response.ok, `Upstream watch unavailable: ${response.status} ${url}`);
  return response.json();
}
async function main() {
  const proof = JSON.parse(
    fs.readFileSync(
      path.resolve(__dirname, '../vendor/braces-provenance.json'),
      'utf8',
    ),
  );
  const [registry, pr, advisories] = await Promise.all([
    get('https://registry.npmjs.org/braces/latest'),
    get('https://api.github.com/repos/micromatch/braces/pulls/72'),
    get(
      'https://api.github.com/advisories?ecosystem=npm&affects=braces%403.0.3&per_page=100',
    ),
  ]);
  assert.equal(
    registry.version,
    proof.original.version,
    'Official release changed: reassess and retire derivative only after verified replacement',
  );
  assert.equal(
    pr.head.sha,
    proof.patch.upstream_pr_head,
    'Upstream patch changed: security reassessment required',
  );
  assert.equal(pr.state, 'open', 'Upstream PR status changed: reassess');
  assert.equal(
    pr.merged,
    false,
    'Upstream PR merged: review official release path',
  );
  assert(
    Array.isArray(advisories) && advisories.length < 100,
    'Incomplete advisory response',
  );
  const rows = advisories
    .map(({ ghsa_id, updated_at, withdrawn_at }) => ({
      ghsa_id,
      updated_at,
      withdrawn_at,
    }))
    .sort((a, b) => a.ghsa_id.localeCompare(b.ghsa_id));
  assert.deepEqual(
    rows,
    proof.watch.advisories,
    'Upstream advisories changed: reassess derivative exposure and coverage',
  );
  console.log(
    'Upstream braces release, PR and advisory mapping unchanged; not a security guarantee.',
  );
}
main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
