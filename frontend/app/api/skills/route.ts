const API_URLS = [
  process.env.DAEMON_INTERNAL_API_URL,
  process.env.NEXT_PUBLIC_API_URL,
  "http://backend:8000",
  "http://localhost:8000",
].filter((url): url is string => Boolean(url));

async function proxyToBackend(req: Request, path: string): Promise<Response> {
  const method = req.method.toUpperCase();

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
    method === "GET" || method === "HEAD" ? undefined : await req.arrayBuffer();

  let backendRes: Response | null = null;
  let lastError: Error | null = null;

  for (const apiUrl of API_URLS) {
    try {
      backendRes = await fetch(`${apiUrl}${path}`, {
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

export async function GET(req: Request) {
  return proxyToBackend(req, "/skills");
}

export async function POST(req: Request) {
  return proxyToBackend(req, "/skills");
}
