const API_URLS = [
  process.env.DAEMON_INTERNAL_API_URL,
  process.env.NEXT_PUBLIC_API_URL,
  'http://backend:8000',
  'http://localhost:8000',
].filter((url): url is string => Boolean(url));

const BACKEND_PATH = '/users/me/entitlements';

/**
 * Builds an explicit allowlist of forwarded headers. The plan is decided by the
 * authenticated session, so client-supplied plan, tier, or capability hints are
 * never relayed to the backend.
 */
function buildProxyHeaders(req: Request): Headers {
  const headers = new Headers();

  const authorization = req.headers.get('authorization');
  if (authorization) {
    headers.set('Authorization', authorization);
  }

  const cookie = req.headers.get('cookie');
  if (cookie) headers.set('Cookie', cookie);

  const origin = req.headers.get('origin');
  if (origin) headers.set('Origin', origin);

  const referer = req.headers.get('referer');
  if (referer) headers.set('Referer', referer);

  const secFetchSite = req.headers.get('sec-fetch-site');
  if (secFetchSite) headers.set('Sec-Fetch-Site', secFetchSite);

  const host = req.headers.get('host');
  if (host) headers.set('Host', host);

  const xForwardedHost = req.headers.get('x-forwarded-host');
  if (xForwardedHost) headers.set('X-Forwarded-Host', xForwardedHost);

  const xForwardedProto = req.headers.get('x-forwarded-proto');
  if (xForwardedProto) headers.set('X-Forwarded-Proto', xForwardedProto);

  return headers;
}

export async function GET(req: Request): Promise<Response> {
  const requestHeaders = buildProxyHeaders(req);
  const search = new URL(req.url).search;

  let backendRes: Response | null = null;

  for (const apiUrl of API_URLS) {
    try {
      backendRes = await fetch(`${apiUrl}${BACKEND_PATH}${search}`, {
        method: 'GET',
        headers: requestHeaders,
        credentials: 'include',
        cache: 'no-store',
      });
      break;
    } catch {
      // Try the next configured backend. A transport error can name internal
      // hosts and ports, so nothing from it is relayed to the browser.
    }
  }

  if (!backendRes) {
    return new Response(
      JSON.stringify({
        error: 'Could not reach the plan service.',
        detail: {
          code: 'entitlements_unreachable',
          message: 'Could not reach the plan service.',
        },
      }),
      {
        status: 502,
        headers: { 'content-type': 'application/json' },
      },
    );
  }

  const responseHeaders = new Headers();
  backendRes.headers.forEach((value, key) => {
    if (key.toLowerCase() === 'set-cookie') {
      responseHeaders.append(key, value);
    } else if (key.toLowerCase() !== 'content-encoding') {
      responseHeaders.set(key, value);
    }
  });

  return new Response(backendRes.body, {
    status: backendRes.status,
    statusText: backendRes.statusText,
    headers: responseHeaders,
  });
}
