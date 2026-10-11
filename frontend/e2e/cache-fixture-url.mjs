import { fixtureURL } from './sources-fixture-url.mjs';

// Request targets may select paths, never the test-owned upstream authority.
export function cacheFixtureURLs(target) {
  const url = fixtureURL(target, 'http://127.0.0.1:3142');
  const upstream = new URL('http://127.0.0.1:3141');
  upstream.pathname = url.pathname;
  upstream.search = url.search;
  return { url, upstream };
}
