/** Read-only single-origin proxy; never fan out bearer credentials. */
const PRIVATE = {
  'Cache-Control': 'private, no-store',
  'X-Content-Type-Options': 'nosniff',
};
const ID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
const PATH = new RegExp(
  `^(?:/resources(?:/${ID}/versions/${ID}(?:/(?:content|preview))?)?|/tasks/${ID}/(?:inputs|presentations))$`,
);
export function resourceMethodDenied() {
  return new Response(null, {
    status: 405,
    headers: { ...PRIVATE, Allow: 'GET, HEAD' },
  });
}
export async function resourceProxy(request: Request, path: string) {
  if (!PATH.test(path) || !['GET', 'HEAD'].includes(request.method))
    return new Response(null, { status: 404, headers: PRIVATE });
  const query = new URL(request.url).search;
  if (query.length > 16384)
    return new Response(null, { status: 400, headers: PRIVATE });
  const headers = new Headers();
  for (const key of ['authorization', 'range', 'if-range', 'if-none-match']) {
    const value = request.headers.get(key);
    if (value && value.length <= 8192) headers.set(key, value);
  }
  try {
    const base = new URL(
      process.env.DAEMON_INTERNAL_API_URL ||
        process.env.NEXT_PUBLIC_API_URL ||
        'http://backend:8000',
    );
    if (
      !['http:', 'https:'].includes(base.protocol) ||
      base.username ||
      base.password ||
      base.search ||
      base.hash
    )
      throw new Error('invalid backend');
    const upstream = await fetch(
      `${base.toString().replace(/\/$/, '')}${path}${query}`,
      {
        method: request.method,
        headers,
        cache: 'no-store',
        redirect: 'error',
        signal: request.signal,
      },
    );
    const returned = new Headers(PRIVATE);
    for (const key of [
      'content-type',
      'content-length',
      'content-disposition',
      'content-range',
      'accept-ranges',
      'etag',
      'content-security-policy',
    ]) {
      const value = upstream.headers.get(key);
      if (value) returned.set(key, value);
    }
    return new Response(request.method === 'HEAD' ? null : upstream.body, {
      status: upstream.status,
      headers: returned,
    });
  } catch {
    return new Response(
      request.method === 'HEAD' ? null : '{"code":"unavailable"}',
      {
        status: 503,
        headers: { ...PRIVATE, 'Content-Type': 'application/json' },
      },
    );
  }
}
