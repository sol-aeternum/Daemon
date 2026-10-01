import { describe, expect, it } from 'vitest';
import { fixtureURL, frontendURL } from '../e2e/sources-fixture-url.mjs';

describe('fictional Sources proxy destination', () => {
  it.each([
    '/_next/static/file.js?version=1',
    '//foreign.test/path',
    'https://user:password@foreign.test/path?x=1',
    '/@foreign.test',
    '/\\foreign.test/path',
    'https://foreign.test//other.test/path',
    '/%2f%2fforeign.test/path',
    '/path?next=https://foreign.test',
  ])('never lets target %s select the upstream authority', (target) => {
    const url = fixtureURL(target, 'http://127.0.0.1:3102');
    const upstream = frontendURL(url);
    expect(upstream.origin).toBe('http://127.0.0.1:3101');
    expect(upstream.username).toBe('');
    expect(upstream.password).toBe('');
    expect(upstream.pathname).toBe(url.pathname);
    expect(upstream.search).toBe(url.search);
  });

  it.each([
    'x:@example.invalid',
    'javascript:alert(1)',
    'file:///tmp/a',
    'http://[',
    undefined,
  ])(
    'rejects unsupported or malformed target %s without network access',
    (target) => {
      expect(() => fixtureURL(target, 'http://127.0.0.1:3102')).toThrow();
    },
  );
});
