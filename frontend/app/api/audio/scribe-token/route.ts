import { NextRequest, NextResponse } from "next/server";

const API_URLS = [
  process.env.DAEMON_INTERNAL_API_URL,
  process.env.NEXT_PUBLIC_API_URL,
  "http://backend:8000",
  "http://localhost:8000",
].filter((url): url is string => Boolean(url));

function buildProxyHeaders(req: Request): Headers {
  const headers = new Headers();

  const authHeader = req.headers.get("authorization");
  if (authHeader) headers.set("Authorization", authHeader);

  const cookie = req.headers.get("cookie");
  if (cookie) headers.set("Cookie", cookie);

  const origin = req.headers.get("origin");
  if (origin) headers.set("Origin", origin);

  const referer = req.headers.get("referer");
  if (referer) headers.set("Referer", referer);

  const secFetchSite = req.headers.get("sec-fetch-site");
  if (secFetchSite) headers.set("Sec-Fetch-Site", secFetchSite);

  const host = req.headers.get("host");
  if (host) headers.set("Host", host);

  const xForwardedHost = req.headers.get("x-forwarded-host");
  if (xForwardedHost) headers.set("X-Forwarded-Host", xForwardedHost);

  const xForwardedProto = req.headers.get("x-forwarded-proto");
  if (xForwardedProto) headers.set("X-Forwarded-Proto", xForwardedProto);

  return headers;
}

async function buildResponseWithCookies(res: Response): Promise<NextResponse> {
  const data = await res.json();
  const responseHeaders = new Headers();
  responseHeaders.set("Content-Type", "application/json");

  res.headers.forEach((value, key) => {
    if (key.toLowerCase() === "set-cookie") {
      responseHeaders.append("Set-Cookie", value);
    }
  });

  return NextResponse.json(data, {
    status: res.status,
    headers: responseHeaders,
  });
}

export async function GET(req: NextRequest) {
  try {
    const proxyHeaders = buildProxyHeaders(req);

    let lastError: Error | null = null;

    for (const apiUrl of API_URLS) {
      try {
        const response = await fetch(`${apiUrl}/audio/scribe-token`, {
          headers: proxyHeaders,
          credentials: "include",
        });

        if (!response.ok) {
          const error = await response.text();
          return NextResponse.json(
            { error: `Failed to get Scribe token: ${error}` },
            { status: response.status }
          );
        }

        return await buildResponseWithCookies(response);
      } catch (error) {
        lastError = error instanceof Error ? error : new Error(String(error));
      }
    }

    return NextResponse.json(
      { error: `Scribe token request failed: ${lastError?.message || "Unknown error"}` },
      { status: 500 }
    );
  } catch (error) {
    return NextResponse.json(
      { error: `Scribe token request failed: ${error}` },
      { status: 500 }
    );
  }
}
