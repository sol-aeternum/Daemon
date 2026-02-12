import { NextResponse } from "next/server";

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const { text, duration_seconds } = body || {};

    if (!text?.trim()) {
      return NextResponse.json({ error: "Text description required" }, { status: 400 });
    }

    const authHeader = req.headers.get("authorization");

    const formData = new URLSearchParams();
    formData.append("text", text);
    formData.append("duration_seconds", String(duration_seconds || 2.0));

    const backendRes = await fetch("http://backend:8000/sound-effects", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        ...(authHeader ? { Authorization: authHeader } : {}),
      },
      body: formData.toString(),
    });

    if (!backendRes.ok) {
      const errorData = await backendRes.json().catch(() => ({ error: "Unknown error" }));
      return NextResponse.json(errorData, { status: backendRes.status });
    }

    const audioBuffer = await backendRes.arrayBuffer();
    return new NextResponse(audioBuffer, {
      status: 200,
      headers: {
        "Content-Type": "audio/mpeg",
        "Content-Disposition": "inline; filename=\"sound-effect.mp3\"",
      },
    });
  } catch (error) {
    console.error("Sound effects API error:", error);
    return NextResponse.json({ error: "Sound effects request failed" }, { status: 500 });
  }
}
