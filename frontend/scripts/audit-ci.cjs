'use strict';

// Frontend dependency audit gate (#735). Runs the same
// `npm audit --audit-level=high` check, as JSON, prints the complete report, and
// fails on every high/critical finding unless that finding is attributable ONLY
// to the single recorded, unexpired exception in ../audit-exceptions.json.
// Everything unexpected fails closed: audit or network errors, unknown report
// shapes, inconsistent counts, ambiguous attribution, an advisory whose range or
// severity changed, a new affected package or path, and an expired record.
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const RECORD_PATH = path.join(__dirname, '..', 'audit-exceptions.json');
const AUDIT_ARGS = ['audit', '--json', '--audit-level=high'];
const SEVERITIES = ['info', 'low', 'moderate', 'high', 'critical'];
const BLOCKING = new Set(['high', 'critical']);
const MAX_WINDOW_MS = 14 * 24 * 60 * 60 * 1000;
const INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
const GHSA = /^GHSA(-[23456789cfghjmpqrvwx]{4}){3}$/;
const RECORD_KEYS = [
  'advisory',
  'aliases',
  'url',
  'npmAdvisorySource',
  'package',
  'severity',
  'range',
  'tracking',
  'owner',
  'createdAt',
  'expiresAt',
  'rationale',
  'removalCondition',
  'affectedPaths',
];

class AuditRefused extends Error {}
const refuse = (message) => {
  throw new AuditRefused(message);
};
const isObject = (value) =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
const nonEmptyString = (value) =>
  typeof value === 'string' && value.trim() !== '';
const sameKeys = (value, keys) =>
  JSON.stringify(Object.keys(value).sort()) ===
  JSON.stringify([...keys].sort());

function instant(value, field) {
  if (typeof value !== 'string' || !INSTANT.test(value)) {
    refuse(`exception record: ${field} must be an ISO-8601 UTC instant`);
  }
  const time = Date.parse(value);
  if (
    !Number.isFinite(time) ||
    new Date(time).toISOString() !== value.replace('Z', '.000Z')
  ) {
    refuse(`exception record: ${field} is not a real instant`);
  }
  return time;
}

function readRecord(text) {
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    refuse('exception record: not valid JSON');
  }
  if (!isObject(parsed) || !sameKeys(parsed, ['schemaVersion', 'exception'])) {
    refuse('exception record: unexpected top-level shape');
  }
  if (parsed.schemaVersion !== 1)
    refuse('exception record: unsupported schemaVersion');
  const record = parsed.exception;
  if (!isObject(record) || !sameKeys(record, RECORD_KEYS)) {
    refuse('exception record: unexpected exception fields');
  }
  if (!GHSA.test(record.advisory))
    refuse('exception record: advisory is not a GHSA id');
  if (record.url !== `https://github.com/advisories/${record.advisory}`) {
    refuse('exception record: url does not match the advisory');
  }
  if (!Array.isArray(record.aliases) || !record.aliases.every(nonEmptyString)) {
    refuse('exception record: aliases must be strings');
  }
  if (
    !Number.isSafeInteger(record.npmAdvisorySource) ||
    record.npmAdvisorySource <= 0
  ) {
    refuse('exception record: npmAdvisorySource must be a positive integer');
  }
  if (!BLOCKING.has(record.severity)) {
    refuse(
      'exception record: only a high/critical advisory needs an exception',
    );
  }
  for (const field of [
    'package',
    'range',
    'tracking',
    'owner',
    'rationale',
    'removalCondition',
  ]) {
    if (!nonEmptyString(record[field]))
      refuse(`exception record: ${field} is required`);
  }
  const created = instant(record.createdAt, 'createdAt');
  const expires = instant(record.expiresAt, 'expiresAt');
  if (!(expires > created) || expires - created > MAX_WINDOW_MS) {
    refuse(
      'exception record: the exception window must be positive and at most 14 days',
    );
  }
  const paths = record.affectedPaths;
  if (!isObject(paths) || !Object.hasOwn(paths, record.package)) {
    refuse('exception record: affectedPaths must include the advisory package');
  }
  for (const [name, nodes] of Object.entries(paths)) {
    if (!nonEmptyString(name) || !Array.isArray(nodes) || nodes.length === 0) {
      refuse(`exception record: affectedPaths.${name} must list node paths`);
    }
    if (
      !nodes.every(
        (node) => nonEmptyString(node) && node.startsWith('node_modules/'),
      )
    ) {
      refuse(
        `exception record: affectedPaths.${name} has an invalid node path`,
      );
    }
  }
  return { ...record, created, expires };
}

function validateAdvisory(via, owner) {
  const keys = [
    'source',
    'name',
    'dependency',
    'title',
    'url',
    'severity',
    'range',
  ];
  if (!keys.every((key) => Object.hasOwn(via, key))) {
    refuse(`audit report: advisory under ${owner} is missing fields`);
  }
  if (!Number.isSafeInteger(via.source))
    refuse(`audit report: advisory source under ${owner}`);
  for (const key of ['name', 'dependency', 'title', 'url', 'range']) {
    if (typeof via[key] !== 'string')
      refuse(`audit report: advisory ${key} under ${owner}`);
  }
  if (!/^https:\/\/github\.com\/advisories\/GHSA-/.test(via.url)) {
    refuse(`audit report: unrecognised advisory url under ${owner}`);
  }
  if (!SEVERITIES.includes(via.severity))
    refuse(`audit report: advisory severity under ${owner}`);
}

function validateReport(report) {
  if (!isObject(report)) refuse('audit report: not a JSON object');
  if (Object.hasOwn(report, 'error')) {
    const error = isObject(report.error) ? report.error : {};
    const why = [error.code, error.summary, report.message].filter(
      nonEmptyString,
    );
    refuse(`npm audit error: ${why.join(' ') || 'unknown'}`);
  }
  if (report.auditReportVersion !== 2)
    refuse('audit report: unsupported auditReportVersion');
  const { vulnerabilities, metadata } = report;
  if (
    !isObject(vulnerabilities) ||
    !isObject(metadata) ||
    !isObject(metadata.vulnerabilities)
  ) {
    refuse('audit report: missing vulnerabilities or metadata');
  }
  const counts = Object.fromEntries(
    SEVERITIES.map((severity) => [severity, 0]),
  );
  for (const [name, entry] of Object.entries(vulnerabilities)) {
    if (!isObject(entry) || entry.name !== name)
      refuse(`audit report: malformed entry ${name}`);
    if (!SEVERITIES.includes(entry.severity))
      refuse(`audit report: unknown severity on ${name}`);
    if (
      typeof entry.isDirect !== 'boolean' ||
      typeof entry.range !== 'string'
    ) {
      refuse(`audit report: malformed entry ${name}`);
    }
    if (!Array.isArray(entry.via) || entry.via.length === 0) {
      refuse(`audit report: ${name} has no cause`);
    }
    if (
      !Array.isArray(entry.effects) ||
      !entry.effects.every((e) => typeof e === 'string')
    ) {
      refuse(`audit report: malformed effects on ${name}`);
    }
    if (
      !Array.isArray(entry.nodes) ||
      !entry.nodes.every(nonEmptyString) ||
      !entry.nodes.length
    ) {
      refuse(`audit report: malformed nodes on ${name}`);
    }
    for (const via of entry.via) {
      if (typeof via === 'string') {
        if (!Object.hasOwn(vulnerabilities, via)) {
          refuse(`audit report: ${name} refers to unreported package ${via}`);
        }
      } else if (isObject(via)) {
        validateAdvisory(via, name);
      } else {
        refuse(`audit report: malformed cause on ${name}`);
      }
    }
    counts[entry.severity] += 1;
  }
  const declared = metadata.vulnerabilities;
  const total = Object.keys(vulnerabilities).length;
  for (const severity of SEVERITIES) {
    if (declared[severity] !== counts[severity]) {
      refuse(
        `audit report: metadata ${severity} count disagrees with the findings`,
      );
    }
  }
  if (declared.total !== total)
    refuse('audit report: metadata total disagrees with the findings');
  return report;
}

// All advisories a finding inherits, through its own causes and every
// vulnerable dependency it names (cycles visited once).
function rootAdvisories(vulnerabilities, name) {
  const roots = new Map();
  const seen = new Set();
  const queue = [name];
  while (queue.length) {
    const current = queue.shift();
    if (seen.has(current)) continue;
    seen.add(current);
    for (const via of vulnerabilities[current].via) {
      if (typeof via === 'string') queue.push(via);
      else
        roots.set(
          `${via.source}|${via.url}|${via.name}|${via.range}|${via.severity}`,
          via,
        );
    }
  }
  return [...roots.values()];
}

const rank = (severity) => SEVERITIES.indexOf(severity);
const advisoryId = (via) =>
  via.url.slice('https://github.com/advisories/'.length);

function exempt(via, record) {
  if (via.url !== record.url) return false;
  const exact =
    via.name === record.package &&
    via.severity === record.severity &&
    via.range === record.range &&
    via.source === record.npmAdvisorySource;
  if (!exact) {
    refuse(
      `${record.advisory} no longer matches the record (now ${via.name} ${via.range} ` +
        `${via.severity} source ${via.source}); a fresh reachability review is required`,
    );
  }
  return true;
}

// Pure decision. `now` is a millisecond timestamp.
function evaluate(report, record, now) {
  validateReport(report);
  if (now < record.created)
    refuse('exception record: createdAt is in the future');
  if (now >= record.expires) {
    refuse(
      `exception for ${record.advisory} expired at ${record.expiresAt}; remove or re-review it`,
    );
  }
  const findings = [];
  const violations = [];
  for (const [name, entry] of Object.entries(report.vulnerabilities).sort(
    ([a], [b]) => a.localeCompare(b),
  )) {
    // npm decides per edge whether a dependent inherits a dependency's advisory,
    // and `via` names only the dependency. A finding is therefore attributed to
    // EVERY advisory in its closure: it may be less severe than the closure's
    // worst (npm judged that edge unaffected), never more severe; and a
    // high/critical finding is exempt only when the excepted advisory is the
    // sole high/critical advisory anywhere in that closure (else fail closed).
    const roots = rootAdvisories(report.vulnerabilities, name);
    const highest = roots.reduce(
      (max, via) => Math.max(max, rank(via.severity)),
      -1,
    );
    if (highest < rank(entry.severity)) {
      refuse(
        `audit report: ${name} severity is not explained by its advisories`,
      );
    }
    const blockingRoots = roots.filter((via) => BLOCKING.has(via.severity));
    const finding = {
      name,
      severity: entry.severity,
      advisories: roots
        .map((via) => `${advisoryId(via)} (${via.name} ${via.severity})`)
        .sort(),
      exempt: false,
    };
    findings.push(finding);
    if (!BLOCKING.has(entry.severity)) continue;
    const other = blockingRoots.filter((via) => !exempt(via, record));
    if (other.length) {
      violations.push(
        `${name}: ${other.map((via) => `${advisoryId(via)} ${via.severity} ${via.url}`).join(', ')}`,
      );
      continue;
    }
    const reviewed = Object.hasOwn(record.affectedPaths, name)
      ? record.affectedPaths[name]
      : undefined;
    const unreviewed = reviewed
      ? entry.nodes.filter((node) => !reviewed.includes(node))
      : entry.nodes;
    if (unreviewed.length) {
      violations.push(
        `${name}: unreviewed path for ${record.advisory}: ${unreviewed.join(', ')}`,
      );
      continue;
    }
    finding.exempt = true;
  }
  const blocking = findings.filter((finding) => BLOCKING.has(finding.severity));
  return {
    ok: violations.length === 0,
    findings,
    violations,
    exempted: blocking
      .filter((finding) => finding.exempt)
      .map((finding) => finding.name),
    blocking: blocking.length,
  };
}

// `npm` is resolved from PATH with the inherited environment, as the plain
// `npm audit` script was. Reading no environment variables keeps this script
// outside the env-surface parity gate's configuration inventory.
const NPM = 'npm';

// One audit run. Returns { ok, lines } and never throws.
function run({
  spawn = spawnSync,
  now = Date.now(),
  recordText = undefined,
  cwd = path.join(__dirname, '..'),
} = {}) {
  const lines = [];
  const say = (line) => lines.push(line);
  let record;
  let recordError;
  try {
    record = readRecord(recordText ?? fs.readFileSync(RECORD_PATH, 'utf8'));
  } catch (error) {
    recordError =
      error instanceof AuditRefused
        ? error.message
        : 'exception record unreadable';
  }
  say(`$ npm ${AUDIT_ARGS.join(' ')}`);
  const result = spawn(NPM, AUDIT_ARGS, {
    cwd,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    timeout: 300_000,
  });
  const stdout = typeof result.stdout === 'string' ? result.stdout : '';
  const stderr = typeof result.stderr === 'string' ? result.stderr : '';
  say('--- npm audit report (complete, unfiltered) ---');
  say(stdout.trimEnd() || '(no output)');
  if (stderr.trim()) say(`--- npm audit stderr ---\n${stderr.trimEnd()}`);
  const fail = (reason) => {
    say(`AUDIT FAILED: ${reason}`);
    return { ok: false, lines };
  };
  if (result.error)
    return fail(
      `could not run npm audit: ${result.error.code ?? result.error.message}`,
    );
  if (result.signal)
    return fail(`npm audit was terminated by ${result.signal}`);
  if (result.status !== 0 && result.status !== 1)
    return fail(`npm audit exited ${result.status}`);
  if (recordError) return fail(recordError);
  let report;
  try {
    report = JSON.parse(stdout);
  } catch {
    return fail('npm audit output is not JSON');
  }
  let decision;
  try {
    decision = evaluate(report, record, now);
  } catch (error) {
    return fail(
      error instanceof AuditRefused
        ? error.message
        : `unexpected ${error?.name ?? 'error'}`,
    );
  }
  if ((result.status === 0) !== (decision.blocking === 0)) {
    return fail(
      `npm audit exit ${result.status} disagrees with ${decision.blocking} high/critical findings`,
    );
  }
  say('--- attribution of every finding ---');
  for (const finding of decision.findings) {
    const mark = BLOCKING.has(finding.severity)
      ? finding.exempt
        ? 'EXEMPT'
        : 'BLOCKING'
      : 'below threshold';
    say(
      `${finding.severity.padEnd(8)} ${finding.name}: ${finding.advisories.join(', ')} [${mark}]`,
    );
  }
  if (!decision.ok) {
    return fail(
      `high/critical findings outside the exception:\n  ${decision.violations.join('\n  ')}`,
    );
  }
  if (decision.exempted.length) {
    say(
      `PASSED with exception ${record.advisory} (${record.tracking}), expires ${record.expiresAt}: ` +
        `${decision.exempted.length} high/critical findings attributable only to it.`,
    );
    return { ok: true, lines };
  }
  // Fail closed: an empty audit (offline, omitted dev dependencies, a mirror
  // answering {}) must not pass silently, and an unused record must go.
  return fail(
    `exception ${record.advisory} matched no finding: the audit may be empty or ` +
      'offline, or the advisory no longer applies. Verify, then remove the record ' +
      'and restore the plain npm audit.',
  );
}

module.exports = {
  AUDIT_ARGS,
  AuditRefused,
  RECORD_PATH,
  evaluate,
  readRecord,
  rootAdvisories,
  run,
  validateReport,
};

if (require.main === module) {
  const { ok, lines } = run();
  for (const line of lines) (ok ? console.log : console.error)(line);
  process.exitCode = ok ? 0 : 1;
}
