const API_URLS = [
  process.env.DAEMON_INTERNAL_API_URL,
  process.env.NEXT_PUBLIC_API_URL,
  "http://backend:8000",
  "http://localhost:8000",
].filter((url): url is string => Boolean(url));

function buildProxyHeaders(req: Request): Headers {
  const requestHeaders = new Headers(req.headers);

  if (!req.headers.get("authorization")) {
    requestHeaders.delete("Authorization");
  }
  requestHeaders.delete("host");
  requestHeaders.delete("content-length");

  const xForwardedHost = req.headers.get("x-forwarded-host");
  if (xForwardedHost && !requestHeaders.has("x-forwarded-host")) {
    requestHeaders.set("X-Forwarded-Host", xForwardedHost);
  }

  const xForwardedProto = req.headers.get("x-forwarded-proto");
  if (xForwardedProto && !requestHeaders.has("x-forwarded-proto")) {
    requestHeaders.set("X-Forwarded-Proto", xForwardedProto);
  }

  return requestHeaders;
}

async function proxyToBackend(req: Request, path: string): Promise<Response> {
  const method = req.method.toUpperCase();
  const requestHeaders = buildProxyHeaders(req);

  const body =
    method === "GET" || method === "HEAD" ? undefined : await req.arrayBuffer();

  let backendRes: Response | null = null;
  let lastError: Error | null = null;

  for (const apiUrl of API_URLS) {
    try {
      backendRes = await fetch(`${apiUrl}${path}`, {
        method,
        headers: requestHeaders,
        credentials: "include",
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

  const responseHeaders = new Headers();
  backendRes.headers.forEach((value, key) => {
    if (key.toLowerCase() === "set-cookie") {
      responseHeaders.append(key, value);
    } else if (key.toLowerCase() !== "content-encoding") {
      responseHeaders.set(key, value);
    }
  });

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
