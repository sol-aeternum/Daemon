import { NextResponse } from "next/server";

export async function POST(req: Request) {
  const body = await req.json();
  const { text, voice, model, speed, format, cache } = body || {};

  const authHeader = req.headers.get("authorization");

  const backendRes = await fetch("http://backend:8000/tts", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: authHeader || `Bearer ${process.env.DAEMON_API_KEY || "sk-test"}`,
    },
    body: JSON.stringify({ text, voice, model, speed, format, cache }),
  });

  const data = await backendRes.json();
  return NextResponse.json(data, { status: backendRes.status });
}
