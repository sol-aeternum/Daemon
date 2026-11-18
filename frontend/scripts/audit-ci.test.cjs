'use strict';

// Offline, deterministic tests for the #735 audit exception gate. No network:
// npm is replaced by a fake spawn and every report is a synthetic fixture.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { AUDIT_ARGS, RECORD_PATH, readRecord, run } = require('./audit-ci.cjs');

const URL = 'https://github.com/advisories/GHSA-fc5q-39xc-vjpf';
const PICO = {
  source: 1247710,
  name: 'picomatch',
  dependency: 'picomatch',
  title:
    'picomatch vulnerable to ReDoS through single-branch repeated extglobs',
  url: URL,
  severity: 'high',
  range: '<=4.0.7',
};
const advisory = (id, name, severity, source) => ({
  source,
  name,
  dependency: name,
  title: `${name} synthetic ${severity}`,
  url: `https://github.com/advisories/${id}`,
  severity,
  range: '<9.9.9',
});
const OTHER_HIGH = advisory('GHSA-2222-3333-4444', 'lodash', 'high', 900001);
const OTHER_CRITICAL = advisory(
  'GHSA-5555-6666-7777',
  'lodash',
  'critical',
  900002,
);
const OTHER_MODERATE = advisory(
  'GHSA-8888-9999-cccc',
  'postcss-selector-parser',
  'moderate',
  900003,
);

const RECORD = {
  schemaVersion: 1,
  exception: {
    advisory: 'GHSA-fc5q-39xc-vjpf',
    aliases: ['CVE-2026-112366'],
    url: URL,
    npmAdvisorySource: 1247710,
    package: 'picomatch',
    severity: 'high',
    range: '<=4.0.7',
    tracking: 'https://github.com/sol-aeternum/Daemon/issues/735',
    owner: 'fixture owner',
    createdAt: '2026-11-17T21:30:00Z',
    expiresAt: '2026-12-01T21:30:00Z',
    rationale: 'fixture',
    removalCondition: 'fixture',
    affectedPaths: {
      picomatch: [
        'node_modules/picomatch',
        'node_modules/vite/node_modules/picomatch',
      ],
      micromatch: ['node_modules/micromatch'],
      tailwindcss: ['node_modules/tailwindcss'],
      '@tailwindcss/typography': ['node_modules/@tailwindcss/typography'],
    },
  },
};
const NOW = Date.parse('2026-11-20T00:00:00Z');

const entry = (name, severity, via, nodes = [`node_modules/${name}`]) => ({
  name,
  severity,
  isDirect: false,
  via,
  effects: [],
  range: '*',
  nodes,
  fixAvailable: false,
});

function report(entries) {
  const vulnerabilities = Object.fromEntries(entries.map((e) => [e.name, e]));
  const counts = { info: 0, low: 0, moderate: 0, high: 0, critical: 0 };
  for (const e of entries) counts[e.severity] += 1;
  return {
    auditReportVersion: 2,
    vulnerabilities,
    metadata: {
      vulnerabilities: { ...counts, total: entries.length },
      dependencies: {},
    },
  };
}

// The exact advisory with transitive-only parents, including a parent whose own
// moderate advisory sits beside the inherited high.
const exactGraph = () => [
  entry(
    'picomatch',
    'high',
    [PICO],
    ['node_modules/picomatch', 'node_modules/vite/node_modules/picomatch'],
  ),
  entry('micromatch', 'high', ['picomatch']),
  entry('tailwindcss', 'high', ['micromatch']),
  entry('postcss-selector-parser', 'moderate', [OTHER_MODERATE]),
  entry('@tailwindcss/typography', 'high', [
    'postcss-selector-parser',
    'tailwindcss',
  ]),
];

function audit(
  body,
  {
    status,
    stdout,
    stderr = '',
    error,
    signal,
    now = NOW,
    record = RECORD,
    cwd = '/fake/frontend',
  } = {},
) {
  const calls = [];
  const out =
    stdout ?? (typeof body === 'string' ? body : JSON.stringify(body));
  const spawn = (command, args, options) => {
    calls.push({ command, args, options });
    return { status, stdout: out, stderr, error, signal: signal ?? null };
  };
  const result = run({
    spawn,
    now,
    recordText: typeof record === 'string' ? record : JSON.stringify(record),
    ...(cwd === null ? {} : { cwd }),
  });
  return { ...result, text: result.lines.join('\n'), calls };
}

const withException = (change) => {
  const copy = structuredClone(RECORD);
  change(copy.exception);
  return copy;
};

test('runs the unchanged high-threshold npm audit check, as JSON', () => {
  const result = audit(report([]), { status: 0 });
  assert.deepEqual(AUDIT_ARGS, ['audit', '--json', '--audit-level=high']);
  assert.equal(result.calls[0].command, 'npm');
  assert.deepEqual(result.calls[0].args, AUDIT_ARGS);
  assert.equal('env' in result.calls[0].options, false);
  assert.equal(result.calls[0].options.cwd, '/fake/frontend');
  // By default the audit runs in frontend/, wherever the script is invoked from.
  const invokedFrom = process.cwd();
  process.chdir(require('node:os').tmpdir());
  try {
    const fromAnywhere = audit(report([]), { status: 0, cwd: null });
    assert.equal(fromAnywhere.calls[0].options.cwd, path.join(__dirname, '..'));
  } finally {
    process.chdir(invokedFrom);
  }
});

test('exact advisory with transitive-only parents passes and stays visible', () => {
  const result = audit(report(exactGraph()), { status: 1 });
  assert.equal(result.ok, true, result.text);
  assert.match(result.text, /complete, unfiltered/);
  for (const name of [
    'picomatch',
    'micromatch',
    'tailwindcss',
    '@tailwindcss/typography',
  ]) {
    assert.match(
      result.text,
      new RegExp(`high +${name.replace('/', '\\/')}: .*\\[EXEMPT\\]`),
    );
  }
  assert.match(
    result.text,
    /moderate postcss-selector-parser: .*\[below threshold\]/,
  );
  assert.match(
    result.text,
    /PASSED with exception GHSA-fc5q-39xc-vjpf .* expires 2026-12-01T21:30:00Z: 4 /,
  );
  assert.ok(
    result.text.includes(JSON.stringify(report(exactGraph()))),
    'raw report printed',
  );
});

test('a dependency cycle is attributed without looping', () => {
  const graph = exactGraph();
  graph.push(entry('a-cycle', 'high', ['b-cycle', 'picomatch']));
  graph.push(entry('b-cycle', 'high', ['a-cycle']));
  const record = withException((e) => {
    e.affectedPaths['a-cycle'] = ['node_modules/a-cycle'];
    e.affectedPaths['b-cycle'] = ['node_modules/b-cycle'];
  });
  assert.equal(audit(report(graph), { status: 1, record }).ok, true);
});

test('mixed findings on the excepted package itself fail', () => {
  const graph = exactGraph();
  graph[0] = entry(
    'picomatch',
    'high',
    [PICO, { ...OTHER_HIGH, name: 'picomatch' }],
    graph[0].nodes,
  );
  const result = audit(report(graph), { status: 1 });
  assert.equal(result.ok, false);
  assert.match(result.text, /picomatch: GHSA-2222-3333-4444 high/);
});

test('a parent with its own unrelated high advisory fails', () => {
  const graph = exactGraph();
  graph.push(entry('lodash', 'high', [OTHER_HIGH]));
  graph[2] = entry('tailwindcss', 'high', ['micromatch', 'lodash']);
  const result = audit(report(graph), { status: 1 });
  assert.equal(result.ok, false);
  assert.match(result.text, /tailwindcss: GHSA-2222-3333-4444 high/);
  assert.match(result.text, /lodash: GHSA-2222-3333-4444 high/);
});

test('a parent inheriting both the exception and an unrelated high fails', () => {
  const graph = exactGraph();
  graph.push(entry('lodash', 'high', [OTHER_HIGH]));
  graph.push(entry('mixed-parent', 'high', ['picomatch', 'lodash']));
  const record = withException((e) => {
    e.affectedPaths['mixed-parent'] = ['node_modules/mixed-parent'];
  });
  const result = audit(report(graph), { status: 1, record });
  assert.equal(result.ok, false);
  assert.match(result.text, /mixed-parent: GHSA-2222-3333-4444/);
});

for (const [label, other] of [
  ['high', OTHER_HIGH],
  ['critical', OTHER_CRITICAL],
]) {
  test(`an unrelated ${label} finding elsewhere fails`, () => {
    const graph = exactGraph();
    graph.push(entry('lodash', label, [other]));
    const result = audit(report(graph), { status: 1 });
    assert.equal(result.ok, false);
    assert.match(
      result.text,
      new RegExp(`lodash: ${other.url.split('/').pop()} ${label}`),
    );
  });
}

test('an unrelated moderate finding stays below the unchanged threshold', () => {
  const graph = exactGraph();
  graph.push(entry('glob', 'moderate', [{ ...OTHER_MODERATE, name: 'glob' }]));
  const result = audit(report(graph), { status: 1 });
  assert.equal(result.ok, true, result.text);
  assert.match(result.text, /moderate glob: .*\[below threshold\]/);
});

test('an empty, offline or exception-free audit fails closed', () => {
  for (const body of [
    report([]),
    report([entry('postcss-selector-parser', 'moderate', [OTHER_MODERATE])]),
  ]) {
    const result = audit(body, { status: 0 });
    assert.equal(result.ok, false);
    assert.match(
      result.text,
      /matched no finding: the audit may be empty or offline/,
    );
  }
});

test('package names that shadow object built-ins fail without crashing', () => {
  for (const name of ['constructor', 'toString', 'hasOwnProperty']) {
    const graph = exactGraph();
    graph.push(entry(name, 'high', ['picomatch']));
    const result = audit(report(graph), { status: 1 });
    assert.equal(result.ok, false, name);
    assert.match(result.text, new RegExp(`${name}: unreviewed path`));
    assert.match(result.text, /complete, unfiltered/);
  }
});

test('the same GHSA with a changed range, severity or npm source fails closed', () => {
  for (const change of [
    { range: '<=4.0.8' },
    { severity: 'critical' },
    { source: 1 },
    { name: 'picomatch-fork' },
  ]) {
    const graph = exactGraph();
    graph[0] = entry(
      'picomatch',
      change.severity ?? 'high',
      [{ ...PICO, ...change }],
      graph[0].nodes,
    );
    for (const parent of graph.slice(1))
      if (parent.severity === 'high')
        parent.severity = change.severity ?? 'high';
    const result = audit(report(graph), { status: 1 });
    assert.equal(result.ok, false, JSON.stringify(change));
    assert.match(result.text, /no longer matches the record/);
  }
});

test('a new path or package for the excepted advisory fails as unreviewed', () => {
  const extraNode = exactGraph();
  extraNode[0].nodes.push('node_modules/new-tool/node_modules/picomatch');
  let result = audit(report(extraNode), { status: 1 });
  assert.equal(result.ok, false);
  assert.match(result.text, /picomatch: unreviewed path .*new-tool/);

  const newParent = exactGraph();
  newParent.push(entry('runtime-router', 'high', ['picomatch']));
  result = audit(report(newParent), { status: 1 });
  assert.equal(result.ok, false);
  assert.match(result.text, /runtime-router: unreviewed path/);
});

test('an expired, not-yet-valid or over-long exception fails closed', () => {
  const clean = report(exactGraph());
  const expiry = Date.parse(RECORD.exception.expiresAt);
  for (const now of [expiry, expiry + 1, expiry + 86_400_000]) {
    const result = audit(clean, { status: 1, now });
    assert.equal(result.ok, false);
    assert.match(result.text, /expired at 2026-12-01T21:30:00Z/);
  }
  assert.equal(audit(clean, { status: 1, now: expiry - 1 }).ok, true);
  assert.match(
    audit(clean, { status: 1, now: Date.parse(RECORD.exception.createdAt) - 1 })
      .text,
    /createdAt is in the future/,
  );
  const tooLong = withException((e) => {
    e.expiresAt = '2026-12-01T21:30:01Z';
  });
  assert.match(
    audit(clean, { status: 1, record: tooLong }).text,
    /at most 14 days/,
  );
});

test('a malformed exception record fails closed', () => {
  const cases = [
    ['{', /not valid JSON/],
    [withException((e) => delete e.owner), /unexpected exception fields/],
    [withException((e) => (e.extra = true)), /unexpected exception fields/],
    [withException((e) => (e.expiresAt = '2026-12-01')), /ISO-8601/],
    [
      withException((e) => (e.expiresAt = '2026-02-30T00:00:00Z')),
      /not a real instant/,
    ],
    [
      withException((e) => (e.url = 'https://example.com')),
      /url does not match/,
    ],
    [withException((e) => (e.severity = 'moderate')), /only a high\/critical/],
    [
      withException((e) => delete e.affectedPaths.picomatch),
      /must include the advisory package/,
    ],
    [{ ...RECORD, schemaVersion: 2 }, /unsupported schemaVersion/],
    [{ ...RECORD, extra: 1 }, /unexpected top-level shape/],
    [
      withException((e) => (e.advisory = 'GHSA-not-a-real-id')),
      /not a GHSA id/,
    ],
    [withException((e) => (e.aliases = [''])), /aliases must be strings/],
    [
      withException((e) => (e.npmAdvisorySource = '1247710')),
      /positive integer/,
    ],
    [withException((e) => (e.npmAdvisorySource = 0)), /positive integer/],
    [
      withException((e) => (e.expiresAt = e.createdAt)),
      /window must be positive/,
    ],
    [
      withException((e) => (e.affectedPaths.picomatch = ['/abs/picomatch'])),
      /invalid node path/,
    ],
  ];
  for (const [record, pattern] of cases) {
    const result = audit(report(exactGraph()), { status: 1, record });
    assert.equal(result.ok, false);
    assert.match(result.text, pattern);
    assert.match(result.text, /complete, unfiltered/); // the audit still ran and printed
  }
});

test('malformed or unrecognised audit output fails closed', () => {
  const graph = report(exactGraph());
  const mutate = (change) => {
    const copy = structuredClone(graph);
    change(copy);
    return copy;
  };
  const cases = [
    ['', /not JSON/],
    ['npm warn something\n{', /not JSON/],
    [[], /not a JSON object/],
    [
      mutate((r) => (r.auditReportVersion = 3)),
      /unsupported auditReportVersion/,
    ],
    [mutate((r) => delete r.metadata), /missing vulnerabilities or metadata/],
    [
      mutate((r) => (r.metadata.vulnerabilities.high = 3)),
      /high count disagrees/,
    ],
    [mutate((r) => (r.metadata.vulnerabilities.total = 1)), /total disagrees/],
    [
      mutate((r) => (r.vulnerabilities.micromatch.severity = 'severe')),
      /unknown severity/,
    ],
    [
      mutate((r) => (r.vulnerabilities.micromatch.via = ['ghost'])),
      /unreported package ghost/,
    ],
    [mutate((r) => (r.vulnerabilities.micromatch.via = [])), /has no cause/],
    [
      mutate((r) => (r.vulnerabilities.micromatch.via = [42])),
      /malformed cause/,
    ],
    [
      mutate((r) => delete r.vulnerabilities.picomatch.via[0].url),
      /missing fields/,
    ],
    [
      mutate(
        (r) =>
          (r.vulnerabilities.picomatch.via[0].url =
            'https://evil.example/GHSA-fc5q-39xc-vjpf'),
      ),
      /unrecognised advisory url/,
    ],
    [
      mutate((r) => (r.vulnerabilities.micromatch.name = 'other')),
      /malformed entry micromatch/,
    ],
    [
      mutate((r) => delete r.vulnerabilities.micromatch.nodes),
      /malformed nodes/,
    ],
    [
      mutate((r) => (r.vulnerabilities.micromatch.isDirect = 'no')),
      /malformed entry micromatch/,
    ],
    [
      mutate((r) => (r.vulnerabilities.micromatch.range = null)),
      /malformed entry micromatch/,
    ],
    [
      mutate((r) => (r.vulnerabilities.micromatch.effects = [1])),
      /malformed effects/,
    ],
    [
      mutate((r) => (r.vulnerabilities.picomatch.via[0].source = '1247710')),
      /advisory source under picomatch/,
    ],
    [
      mutate((r) => (r.vulnerabilities.picomatch.via[0].severity = 'urgent')),
      /advisory severity under picomatch/,
    ],
  ];
  for (const [body, pattern] of cases) {
    const result = audit(body, { status: 1 });
    assert.equal(result.ok, false, String(pattern));
    assert.match(result.text, pattern);
  }
});

test('a finding more severe than its whole closure is ambiguous and fails', () => {
  const graph = report([
    entry('postcss-selector-parser', 'moderate', [OTHER_MODERATE]),
    entry('wrapper', 'high', ['postcss-selector-parser']),
  ]);
  const result = audit(graph, { status: 1 });
  assert.equal(result.ok, false);
  assert.match(result.text, /wrapper severity is not explained/);
});

test('audit and network errors fail even when the advisory is in the output', () => {
  const network = {
    error: {
      code: 'ENOTFOUND',
      summary: `request to registry failed; see ${URL}`,
      detail: '',
    },
  };
  let result = audit(network, {
    status: 1,
    stderr: 'npm error code ENOTFOUND',
  });
  assert.equal(result.ok, false);
  assert.match(result.text, /npm audit error: ENOTFOUND/);
  assert.match(result.text, /npm error code ENOTFOUND/); // stderr stays visible

  // The shape npm 11 actually emits for an unreachable registry.
  result = audit(
    {
      message: `request to http://127.0.0.1:9/-/npm/v1/security/advisories/bulk failed, ${URL}`,
      error: { summary: '', detail: '' },
    },
    { status: 1 },
  );
  assert.equal(result.ok, false);
  assert.match(
    result.text,
    /npm audit error: request to http:\/\/127\.0\.0\.1:9/,
  );

  result = audit('', {
    error: Object.assign(new Error('spawn npm ENOENT'), { code: 'ENOENT' }),
  });
  assert.match(result.text, /could not run npm audit: ENOENT/);

  result = audit(report(exactGraph()), { status: null, signal: 'SIGTERM' });
  assert.match(result.text, /terminated by SIGTERM/);

  for (const status of [2, 127, null]) {
    result = audit(report(exactGraph()), { status });
    assert.equal(result.ok, false);
    assert.match(result.text, /npm audit exited/);
  }
});

test('an exit code that disagrees with the findings fails closed', () => {
  let result = audit(report(exactGraph()), { status: 0 });
  assert.match(result.text, /exit 0 disagrees with 4 high\/critical findings/);
  result = audit(report([]), { status: 1 });
  assert.match(result.text, /exit 1 disagrees with 0 high\/critical findings/);
});

test('the committed record is valid, exactly 14 days, and names the advisory', () => {
  const record = readRecord(fs.readFileSync(RECORD_PATH, 'utf8'));
  assert.equal(record.advisory, 'GHSA-fc5q-39xc-vjpf');
  assert.deepEqual(record.aliases, ['CVE-2026-112366']);
  assert.equal(record.package, 'picomatch');
  assert.equal(record.range, '<=4.0.7');
  assert.equal(record.expires - record.created, 14 * 24 * 60 * 60 * 1000);
  assert.ok(record.affectedPaths.picomatch.includes('node_modules/picomatch'));
});
