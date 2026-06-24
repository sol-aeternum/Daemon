const API_URLS = [
  process.env.DAEMON_INTERNAL_API_URL,
  process.env.NEXT_PUBLIC_API_URL,
  'http://backend:8000',
  'http://localhost:8000',
].filter((url): url is string => Boolean(url));

type RouteContext = { params: Promise<{ path: string[] }> };
const SAFE_SEGMENT = /^[A-Za-z0-9._~-]+$/;
const UUID_SEGMENT =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ALLOWED_SINGLE_SEGMENTS = new Set([
  'models',
  'generate',
  'upload-reference',
]);

function isAllowedPath(path: string[]): boolean {
  if (path.length === 1) {
    return ALLOWED_SINGLE_SEGMENTS.has(path[0]) || UUID_SEGMENT.test(path[0]);
  }
  if (path.length === 2) {
    return UUID_SEGMENT.test(path[0]) && path[1] === 'metadata';
  }
  return false;
}

function normalizePathSegments(path: string[] | undefined): string {
  if (!Array.isArray(path) || path.length === 0) {
    return '';
  }

  if (!isAllowedPath(path)) {
    throw new Error('Unsupported images API path');
  }

  const safeSegments = path.map((segment) => {
    const trimmed = segment.trim();
    if (!trimmed || trimmed === '.' || trimmed === '..') {
      throw new Error('Invalid path segment');
    }
    if (!SAFE_SEGMENT.test(trimmed)) {
      throw new Error('Path segment contains unsupported characters');
    }
    return encodeURIComponent(trimmed);
  });

  return safeSegments.join('/');
}

function buildProxyHeaders(req: Request): Headers {
  const requestHeaders = new Headers(req.headers);

  if (!req.headers.get('authorization')) {
    requestHeaders.delete('Authorization');
  }
  requestHeaders.delete('host');
  requestHeaders.delete('content-length');

  const xForwardedHost = req.headers.get('x-forwarded-host');
  if (xForwardedHost && !requestHeaders.has('x-forwarded-host')) {
    requestHeaders.set('X-Forwarded-Host', xForwardedHost);
  }

  const xForwardedProto = req.headers.get('x-forwarded-proto');
  if (xForwardedProto && !requestHeaders.has('x-forwarded-proto')) {
    requestHeaders.set('X-Forwarded-Proto', xForwardedProto);
  }

  return requestHeaders;
}

async function proxyRequest(
  req: Request,
  context: RouteContext,
): Promise<Response> {
  const { path } = await context.params;
  const method = req.method.toUpperCase();
  let normalizedPath = '';
  try {
    normalizedPath = normalizePathSegments(path);
  } catch (error) {
    return new Response(
      JSON.stringify({
        error: error instanceof Error ? error.message : 'Invalid path',
      }),
      {
        status: 400,
        headers: { 'content-type': 'application/json' },
      },
    );
  }

  const requestUrl = new URL(req.url);
  const search = requestUrl.search;

  if (method === 'POST') {
    const origin = req.headers.get('origin');
    if (origin && origin !== requestUrl.origin) {
      return new Response(
        JSON.stringify({ error: 'Cross-origin POST is not allowed' }),
        {
          status: 403,
          headers: { 'content-type': 'application/json' },
        },
      );
    }
  }

  const requestHeaders = buildProxyHeaders(req);

  const body =
    method === 'GET' || method === 'HEAD' ? undefined : await req.arrayBuffer();

  let backendRes: Response | null = null;
  let lastError: Error | null = null;

  for (const apiUrl of API_URLS) {
    try {
      backendRes = await fetch(
        `${apiUrl}/api/images/${normalizedPath}${search}`,
        {
          method,
          headers: requestHeaders,
          credentials: 'include',
          body,
        },
      );
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

export async function GET(req: Request, context: RouteContext) {
  return proxyRequest(req, context);
}

export async function POST(req: Request, context: RouteContext) {
  return proxyRequest(req, context);
}
