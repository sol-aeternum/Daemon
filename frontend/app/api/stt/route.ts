import { NextResponse } from "next/server";

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

export async function POST(req: Request) {
  try {
    const formData = await req.formData();
    const audioFile = formData.get("audio") as File;
    const model = formData.get("model") as string || "scribe_v2";
    const language = formData.get("language") as string | null;

    if (!audioFile) {
      return NextResponse.json({ error: "Audio file required" }, { status: 400 });
    }

    const proxyHeaders = buildProxyHeaders(req);

    const backendFormData = new FormData();
    backendFormData.append("audio_file", audioFile);
    backendFormData.append("model", model);
    if (language) backendFormData.append("language", language);

    let backendRes: Response | null = null;
    let lastError: Error | null = null;

    for (const apiUrl of API_URLS) {
      try {
        backendRes = await fetch(`${apiUrl}/stt`, {
          method: "POST",
          headers: proxyHeaders,
          credentials: "include",
          body: backendFormData,
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

    return await buildResponseWithCookies(backendRes);
  } catch (error) {
    console.error("STT API error:", error);
    return NextResponse.json({ error: "STT request failed" }, { status: 500 });
  }
}
