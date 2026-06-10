import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';

const API_URLS = [
  process.env.DAEMON_INTERNAL_API_URL,
  process.env.NEXT_PUBLIC_API_URL,
  'http://backend:8000',
  'http://localhost:8000',
].filter((url): url is string => Boolean(url));

type RouteContext = {
  params: Promise<{
    path: string[];
  }>;
};

export async function GET(_request: NextRequest, { params }: RouteContext) {
  const resolvedParams = await params;
  const filePath = resolvedParams.path.join('/');

  const authHeader = _request.headers.get('authorization');

  let lastStatus = 502;
  let lastError = 'Failed to fetch generated file';

  for (const apiUrl of API_URLS) {
    const upstreamUrl = `${apiUrl.replace(/\/$/, '')}/generated-files/${filePath}`;
    try {
      const upstream = await fetch(upstreamUrl, {
        cache: 'no-store',
        headers: authHeader ? { authorization: authHeader } : {},
      });

      if (!upstream.ok) {
        lastStatus = upstream.status;
        lastError = await upstream
          .text()
          .catch(() => 'Upstream returned non-OK status');
        continue;
      }

      const buffer = await upstream.arrayBuffer();
      const contentType =
        upstream.headers.get('content-type') || 'application/octet-stream';
      const contentDisposition =
        upstream.headers.get('content-disposition') ||
        `attachment; filename="${resolvedParams.path[resolvedParams.path.length - 1] || 'download'}"`;

      return new NextResponse(buffer, {
        status: 200,
        headers: {
          'content-type': contentType,
          'content-disposition': contentDisposition,
        },
      });
    } catch (error) {
      lastStatus = 502;
      lastError =
        error instanceof Error
          ? error.message
          : 'Network failure fetching generated file';
    }
  }

  return NextResponse.json({ error: lastError }, { status: lastStatus });
}
