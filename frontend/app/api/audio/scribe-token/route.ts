import { NextRequest, NextResponse } from "next/server";

const API_URLS = [
  process.env.DAEMON_INTERNAL_API_URL,
  process.env.NEXT_PUBLIC_API_URL,
  "http://backend:8000",
].filter((url): url is string => Boolean(url));

export async function GET(req: NextRequest) {
  try {
    const authHeader = req.headers.get("authorization");
    const authToken = authHeader?.replace(/^Bearer\s+/i, "").trim();
    const authorization = authToken
      ? `Bearer ${authToken}`
      : `Bearer ${process.env.DAEMON_API_KEY || "sk-test"}`;

    let lastError: Error | null = null;

    for (const apiUrl of API_URLS) {
      try {
        const response = await fetch(`${apiUrl}/audio/scribe-token`, {
          headers: {
            Authorization: authorization,
          },
        });

        if (!response.ok) {
          const error = await response.text();
          return NextResponse.json(
            { error: `Failed to get Scribe token: ${error}` },
            { status: response.status }
          );
        }

        const data = await response.json();
        return NextResponse.json(data);
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
