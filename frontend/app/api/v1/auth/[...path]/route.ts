import { copyResponseHeaders } from '../../../_lib/cookies';

const API_URLS = [
  process.env.DAEMON_INTERNAL_API_URL,
  process.env.NEXT_PUBLIC_API_URL,
  'http://backend:8000',
  'http://localhost:8000',
].filter((url): url is string => Boolean(url));

type RouteContext = { params: Promise<{ path: string[] }> };

function shouldTrustPlatformClientIpHeaders(): boolean {
  return process.env.DAEMON_TRUST_PLATFORM_CLIENT_IP_HEADERS === 'true';
}

function normalizeClientIp(value: string | null): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (
    !trimmed ||
    trimmed.toLowerCase() === 'unknown' ||
    trimmed.includes(',')
  ) {
    return null;
  }
  if (/^[0-9a-fA-F:.]+$/.test(trimmed)) {
    return trimmed;
  }
  return null;
}

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

  const daemonClientIp = shouldTrustPlatformClientIpHeaders()
    ? (normalizeClientIp(req.headers.get('x-vercel-forwarded-for')) ??
      normalizeClientIp(req.headers.get('cf-connecting-ip')))
    : null;
  if (daemonClientIp) headers.set('X-Daemon-Client-IP', daemonClientIp);

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
