import { describe, expect, it } from 'vitest';
import { cacheFixtureURLs } from '../e2e/cache-fixture-url.mjs';

describe('P0.2 fixture request destinations', () => {
  it.each([
    '/api/v1/auth/cache/clear',
    '/_next/static/chunks/app.js?v=1',
    '/auth?cache-fixture=1',
    '//foreign.test/path',
    'https://user:password@foreign.test/path?x=1',
    '/@foreign.test',
    '/\\foreign.test/path',
    'https://foreign.test//other.test/path',
    '/%2f%2fforeign.test/path',
    '/%5c%5cforeign.test/path',
    '/%252f%252fforeign.test/path',
    '/path?next=https://foreign.test',
    '/%2e%2e/api/v1/auth/cache/clear',
  ])('keeps the fixed upstream for %s', (target) => {
    const { url, upstream } = cacheFixtureURLs(target);
    expect(upstream.origin).toBe('http://127.0.0.1:3141');
    expect(upstream.username).toBe('');
    expect(upstream.password).toBe('');
    expect(upstream.pathname).toBe(url.pathname);
    expect(upstream.search).toBe(url.search);
    expect(upstream.hash).toBe('');
  });

  it.each([
    'x:@example.invalid',
    'x:@127.0.0.1:9999',
    'javascript:alert(1)',
    'data:text/plain,@example.invalid',
    'file:///tmp/a',
    'http://[',
    undefined,
  ])('rejects %s before returning a forwarding destination', (target) => {
    expect(() => cacheFixtureURLs(target)).toThrow();
  });
});
