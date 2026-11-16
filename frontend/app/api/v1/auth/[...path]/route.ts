import { copyResponseHeaders } from '../../../_lib/cookies';
import { appendDaemonClientIpHeaders } from '../../../_lib/clientIp';
import { selectBackendBase } from '../../../_lib/bootstrapBackend';

type RouteContext = { params: Promise<{ path: string[] }> };

function buildProxyHeaders(req: Request): Headers {
  const headers = new Headers();

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

  appendDaemonClientIpHeaders(headers, req);

  const authorization = req.headers.get('authorization');
  if (authorization) headers.set('Authorization', authorization);

  return headers;
}

/** Opaque: no exception text, internal host or port reaches the browser. */
function unreachable(): Response {
  return new Response(
    JSON.stringify({ error: 'Could not reach the authentication service.' }),
    {
      status: 502,
      headers: {
        'content-type': 'application/json',
        'cache-control': 'no-store',
      },
    },
  );
}

async function proxyRequest(
  req: Request,
  context: RouteContext,
): Promise<Response> {
  const method = req.method.toUpperCase();
  const { path } = await context.params;
  if (path.some((segment) => segment === '.' || segment === '..')) {
    return new Response(JSON.stringify({ error: 'Invalid auth path' }), {
      status: 400,
      headers: { 'content-type': 'application/json' },
    });
  }
  const normalizedPath = path
    .map((segment) => encodeURIComponent(segment))
    .join('/');
  const search = new URL(req.url).search;

  const requestHeaders = buildProxyHeaders(req);

  const contentType = req.headers.get('content-type');
  if (contentType) {
    requestHeaders.set('Content-Type', contentType);
  }

  // One selected authority for this request: authentication must not be
  // replayed against another candidate host. An invalid selection refuses
  // instead of falling through, and a redirect is never followed because that
  // would replay the credential-bearing request elsewhere.
  const selection = selectBackendBase();
  if (!selection.ok) {
    return unreachable();
  }

  const requestBody =
    method !== 'GET' && method !== 'HEAD' ? await req.arrayBuffer() : undefined;

  let backendRes: Response;
  try {
    backendRes = await fetch(
      `${selection.base}/v1/auth/${normalizedPath}${search}`,
      {
        method,
        headers: requestHeaders,
        credentials: 'include',
        body: requestBody,
        cache: 'no-store',
        redirect: 'error',
      },
    );
  } catch {
    // The transport error can name internal hosts and ports; nothing from it
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

export async function GET(req: Request, context: RouteContext) {
  return proxyRequest(req, context);
}

export async function POST(req: Request, context: RouteContext) {
  return proxyRequest(req, context);
}

export async function PUT(req: Request, context: RouteContext) {
  return proxyRequest(req, context);
}

export async function PATCH(req: Request, context: RouteContext) {
  return proxyRequest(req, context);
}

export async function DELETE(req: Request, context: RouteContext) {
  return proxyRequest(req, context);
}
