import { NextResponse } from "next/server";

export async function POST(req: Request) {
  try {
    const formData = await req.formData();
    const audioFile = formData.get("audio") as File;
    const model = formData.get("model") as string || "scribe_v2";
    const language = formData.get("language") as string | null;

    if (!audioFile) {
      return NextResponse.json({ error: "Audio file required" }, { status: 400 });
    }

    const authHeader = req.headers.get("authorization");

    const backendFormData = new FormData();
    backendFormData.append("audio_file", audioFile);
    backendFormData.append("model", model);
    if (language) backendFormData.append("language", language);

    const backendRes = await fetch("http://backend:8000/stt", {
      method: "POST",
      headers: {
        ...(authHeader ? { Authorization: authHeader } : {}),
      },
      body: backendFormData,
    });

    const data = await backendRes.json();
    return NextResponse.json(data, { status: backendRes.status });
  } catch (error) {
    console.error("STT API error:", error);
    return NextResponse.json({ error: "STT request failed" }, { status: 500 });
  }
}
