const API_URLS = [
  process.env.DAEMON_INTERNAL_API_URL,
  process.env.NEXT_PUBLIC_API_URL,
  "http://backend:8000",
  "http://localhost:8000",
].filter((url): url is string => Boolean(url));

type RouteContext = { params: Promise<{ path: string[] }> };

const ALLOWED_SEGMENTS = new Set(["balance", "transactions", "estimate", "grant"]);

function normalizePathSegments(path: string[] | undefined): string {
  if (!Array.isArray(path) || path.length !== 1) {
    throw new Error("Unsupported video credits API path");
  }

  const segment = path[0]?.trim();
  if (!segment || !ALLOWED_SEGMENTS.has(segment)) {
    throw new Error("Unsupported video credits API path");
  }

  return encodeURIComponent(segment);
}

async function proxyRequest(req: Request, context: RouteContext): Promise<Response> {
  const method = req.method.toUpperCase();
  const requestUrl = new URL(req.url);
  const search = requestUrl.search;

  let normalizedPath = "";
  try {
    const { path } = await context.params;
    normalizedPath = normalizePathSegments(path);
  } catch (error) {
    return new Response(
      JSON.stringify({ error: error instanceof Error ? error.message : "Invalid path" }),
      {
        status: 400,
        headers: { "content-type": "application/json" },
      },
    );
  }

  if (method === "POST") {
    const origin = req.headers.get("origin");
    if (origin && origin !== requestUrl.origin) {
      return new Response(JSON.stringify({ error: "Cross-origin POST is not allowed" }), {
        status: 403,
        headers: { "content-type": "application/json" },
      });
    }
  }

  const authHeader = req.headers.get("authorization");
  const authToken = authHeader?.replace(/^Bearer\s+/i, "").trim();
  const daemonApiKey = process.env.DAEMON_API_KEY?.trim();
  const authorization = authToken
    ? `Bearer ${authToken}`
    : daemonApiKey
      ? `Bearer ${daemonApiKey}`
      : null;

  const requestHeaders = new Headers(req.headers);
  if (authorization) {
    requestHeaders.set("Authorization", authorization);
  } else {
    requestHeaders.delete("Authorization");
  }
  requestHeaders.delete("host");
  requestHeaders.delete("content-length");

  const body =
    method === "GET" || method === "HEAD"
      ? undefined
      : await req.arrayBuffer();

  let backendRes: Response | null = null;
  let lastError: Error | null = null;

  for (const apiUrl of API_URLS) {
    try {
      backendRes = await fetch(`${apiUrl}/video-credits/${normalizedPath}${search}`, {
        method,
        headers: requestHeaders,
        body,
      });
      break;
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error));
    }
  }

  if (!backendRes) {
    return new Response(
      JSON.stringify({ error: `Backend error (network): ${lastError?.message || "unknown error"}` }),
      {
        status: 502,
        headers: { "content-type": "application/json" },
      },
    );
  }

  const responseHeaders = new Headers(backendRes.headers);
  responseHeaders.delete("content-encoding");

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
