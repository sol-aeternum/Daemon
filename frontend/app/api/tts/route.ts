import { NextResponse } from 'next/server';

const API_URLS = [
  process.env.DAEMON_INTERNAL_API_URL,
  process.env.NEXT_PUBLIC_API_URL,
  'http://backend:8000',
  'http://localhost:8000',
].filter((url): url is string => Boolean(url));

function buildProxyHeaders(req: Request): Headers {
  const headers = new Headers();
  headers.set('Content-Type', 'application/json');

  const authHeader = req.headers.get('authorization');
  if (authHeader) headers.set('Authorization', authHeader);

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

async function buildResponseWithCookies(res: Response): Promise<NextResponse> {
  const data = await res.json();
  const responseHeaders = new Headers();
  responseHeaders.set('Content-Type', 'application/json');
  responseHeaders.set('Cache-Control', 'no-store');
  const retryAfter = res.headers.get('retry-after');
  if (retryAfter) responseHeaders.set('Retry-After', retryAfter);

  res.headers.forEach((value, key) => {
    if (key.toLowerCase() === 'set-cookie') {
      responseHeaders.append('Set-Cookie', value);
    }
  });

  return NextResponse.json(data, {
    status: res.status,
    headers: responseHeaders,
  });
}

export async function POST(req: Request) {
  const body = await req.json();
  const { text, voice, model, speed, format, cache } = body || {};

  const proxyHeaders = buildProxyHeaders(req);

  let backendRes: Response | null = null;
  let lastError: Error | null = null;

  for (const apiUrl of API_URLS) {
    try {
      backendRes = await fetch(`${apiUrl}/tts`, {
        method: 'POST',
        headers: proxyHeaders,
        credentials: 'include',
        signal: req.signal,
        body: JSON.stringify({ text, voice, model, speed, format, cache }),
      });
      break;
    } catch (error) {
      if (req.signal.aborted) {
        return NextResponse.json(
          { detail: { code: 'speech_cancelled' } },
          { status: 499 },
        );
      }
      lastError = error instanceof Error ? error : new Error(String(error));
    }
  }

  if (!backendRes) {
    return NextResponse.json(
      {
        error: `Backend error (network): ${lastError?.message || 'unknown error'}`,
      },
      { status: 502 },
    );
  }

  return await buildResponseWithCookies(backendRes);
}
