// Pure parsing only: request targets can select a path, never an upstream host.
export function fixtureURL(target, origin) {
  if (typeof target !== 'string') throw new TypeError('Missing request target');
  const url = new URL(target, origin);
  if (url.protocol !== 'http:' && url.protocol !== 'https:')
    throw new TypeError('Unsupported request target');
  return url;
}

export function frontendURL(url) {
  const upstream = new URL('http://127.0.0.1:3101');
  upstream.pathname = url.pathname;
  upstream.search = url.search;
  return upstream;
}
