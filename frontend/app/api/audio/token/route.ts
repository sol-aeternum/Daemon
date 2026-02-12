import { NextRequest, NextResponse } from "next/server";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://backend:8000";

export async function GET(req: NextRequest) {
  try {
    const response = await fetch(`${API_URL}/audio/token`, {
      headers: {
        "Authorization": req.headers.get("Authorization") || "",
      },
    });

    if (!response.ok) {
      const error = await response.text();
      return NextResponse.json(
        { error: `Failed to get audio token: ${error}` },
        { status: response.status }
      );
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    return NextResponse.json(
      { error: `Audio token request failed: ${error}` },
      { status: 500 }
    );
  }
}
