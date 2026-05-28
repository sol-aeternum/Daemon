import { NextResponse } from "next/server";

const API_URLS = [
  process.env.DAEMON_INTERNAL_API_URL,
  process.env.NEXT_PUBLIC_API_URL,
  "http://backend:8000",
  "http://localhost:8000",
].filter((url): url is string => Boolean(url));

function buildProxyHeaders(req: Request): Headers {
  const headers = new Headers();
  headers.set("Content-Type", "application/x-www-form-urlencoded");

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

function buildAudioResponse(res: Response): NextResponse {
  const responseHeaders = new Headers();
  responseHeaders.set("Content-Type", "audio/mpeg");
  responseHeaders.set("Content-Disposition", "inline; filename=\"sound-effect.mp3\"");

  res.headers.forEach((value, key) => {
    if (key.toLowerCase() === "set-cookie") {
      responseHeaders.append("Set-Cookie", value);
    }
  });

  return new NextResponse(res.body, {
    status: res.status,
    headers: responseHeaders,
  });
}

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const { text, duration_seconds } = body || {};

    if (!text?.trim()) {
      return NextResponse.json({ error: "Text description required" }, { status: 400 });
    }

    const proxyHeaders = buildProxyHeaders(req);

    const formData = new URLSearchParams();
    formData.append("text", text);
    formData.append("duration_seconds", String(duration_seconds || 2.0));

    let backendRes: Response | null = null;
    let lastError: Error | null = null;

    for (const apiUrl of API_URLS) {
      try {
        backendRes = await fetch(`${apiUrl}/sound-effects`, {
          method: "POST",
          headers: proxyHeaders,
          credentials: "include",
          body: formData.toString(),
        });
        break;
      } catch (error) {
        lastError = error instanceof Error ? error : new Error(String(error));
      }
    }

    if (!backendRes) {
      return NextResponse.json(
        { error: `Backend error (network): ${lastError?.message || "unknown error"}` },
        { status: 502 }
      );
    }

    if (!backendRes.ok) {
      const errorData = await backendRes.json().catch(() => ({ error: "Unknown error" }));
      return NextResponse.json(errorData, { status: backendRes.status });
    }

    return buildAudioResponse(backendRes);
  } catch (error) {
    console.error("Sound effects API error:", error);
    return NextResponse.json({ error: "Sound effects request failed" }, { status: 500 });
  }
}
