import { copyResponseHeaders } from '../_lib/cookies';
import { selectBackendBase } from '../_lib/bootstrapBackend';

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

/** Opaque: no exception text, internal host or port reaches the browser. */
function unreachable(): Response {
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
      headers: {
        'content-type': 'application/json',
        'cache-control': 'no-store',
      },
    },
  );
}

export async function GET(req: Request): Promise<Response> {
  const requestHeaders = buildProxyHeaders(req);
  const search = new URL(req.url).search;

  // The auth proxy and this route must agree on one authority. An invalid
  // selection refuses instead of falling through, and a redirect is never
  // followed because that would replay the request against another host.
  const selection = selectBackendBase();
  if (!selection.ok) {
    return unreachable();
  }

  let backendRes: Response;
  try {
    backendRes = await fetch(`${selection.base}${BACKEND_PATH}${search}`, {
      method: 'GET',
      headers: requestHeaders,
      credentials: 'include',
      cache: 'no-store',
      redirect: 'error',
    });
  } catch {
    // A transport error can name internal hosts and ports, so nothing from it
    // is relayed to the browser.
    return unreachable();
  }

  const responseHeaders = new Headers();
  copyResponseHeaders(backendRes.headers, responseHeaders);

  return new Response(backendRes.body, {
    status: backendRes.status,
    statusText: backendRes.statusText,
    headers: responseHeaders,
  });
}
