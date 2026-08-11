import { copyResponseHeaders } from '../../../_lib/cookies';
import { daemonClientIp } from '../../../_lib/clientIp';

const API_URLS = [
  process.env.DAEMON_INTERNAL_API_URL,
  process.env.NEXT_PUBLIC_API_URL,
  'http://backend:8000',
  'http://localhost:8000',
].filter((url): url is string => Boolean(url));

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

  const clientIp = daemonClientIp(req);
  if (clientIp) headers.set('X-Daemon-Client-IP', clientIp);

  const authorization = req.headers.get('authorization');
  if (authorization) headers.set('Authorization', authorization);

  return headers;
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

  let backendRes: Response | null = null;
  let lastError: Error | null = null;

  const requestBody =
    method !== 'GET' && method !== 'HEAD' ? await req.arrayBuffer() : undefined;

  for (const apiUrl of API_URLS) {
    try {
      backendRes = await fetch(`${apiUrl}/v1/auth/${normalizedPath}${search}`, {
        method,
        headers: requestHeaders,
        credentials: 'include',
        body: requestBody,
      });
      break;
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error));
    }
  }

  if (!backendRes) {
    return new Response(
      JSON.stringify({
        error: `Backend error (network): ${lastError?.message || 'unknown error'}`,
      }),
      {
        status: 502,
        headers: { 'content-type': 'application/json' },
      },
    );
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
