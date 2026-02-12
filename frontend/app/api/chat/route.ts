const API_URLS = [
  process.env.DAEMON_INTERNAL_API_URL,
  process.env.NEXT_PUBLIC_API_URL,
  "http://backend:8000",
  "http://localhost:8000",
].filter((url): url is string => Boolean(url));

export async function POST(req: Request) {
  const { messages, id } = await req.json();

  const { createDataStreamResponse } = await import("ai");
  const { formatDataStreamPart } = await import("@ai-sdk/ui-utils");

  const normalizedMessages = (messages || []).map((m: any) => ({
    role: m.role,
    content: m.content,
  }));

  const lastUserMessage = [...normalizedMessages].reverse().find((m) => m.role === "user");
  const lastUserText = typeof lastUserMessage?.content === "string" ? lastUserMessage.content : "";

  const authHeader = req.headers.get("authorization");
  const authToken = authHeader?.replace(/^Bearer\s+/i, "").trim();
  const authorization = authToken
    ? `Bearer ${authToken}`
    : `Bearer ${process.env.DAEMON_API_KEY || "sk-test"}`;

  let backendRes: Response | null = null;
  let lastError: Error | null = null;

  for (const apiUrl of API_URLS) {
    try {
      backendRes = await fetch(`${apiUrl}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: authorization,
        },
        body: JSON.stringify({
          message: lastUserText,
          conversation_id: id || null,
          messages: normalizedMessages,
        }),
      });
      break;
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error));
    }
  }

  return createDataStreamResponse({
    execute: async (dataStream) => {
      if (!backendRes) {
        dataStream.write(
          formatDataStreamPart(
            "text",
            `Backend error (network): ${lastError?.message || "unknown error"}.`,
          ),
        );
        return;
      }

      if (!backendRes.ok || !backendRes.body) {
        dataStream.write(
          formatDataStreamPart(
            "text",
            `Backend error (${backendRes.status}): unable to stream response.`,
          ),
        );
        return;
      }

      const reader = backendRes.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let sawToken = false;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        while (true) {
          const sepIdx = buffer.indexOf("\n\n");
          if (sepIdx === -1) break;

          const frame = buffer.slice(0, sepIdx);
          buffer = buffer.slice(sepIdx + 2);

          const lines = frame.split("\n");
          let eventType = "message";
          let dataText = "";

          for (const line of lines) {
            if (line.startsWith("event:")) {
              eventType = line.slice(6).trim();
            } else if (line.startsWith("data:")) {
              dataText += line.slice(5).trim();
            }
          }

          if (!dataText) continue;

          let payload: any;
          try {
            payload = JSON.parse(dataText);
          } catch {
            continue;
          }

          if (eventType === "token") {
            const delta = payload?.data?.delta;
            if (typeof delta === "string" && delta.length > 0) {
              sawToken = true;
              dataStream.write(formatDataStreamPart("text", delta));
            }
          } else if (eventType === "thinking") {
            const content = payload?.data?.content;
            if (typeof content === "string" && content.length > 0) {
              dataStream.write(
                formatDataStreamPart("data", [
                  {
                    type: "thinking",
                    content: content,
                    id: payload?.id ?? payload?.data?.id,
                    request_id: payload?.request_id ?? payload?.data?.request_id,
                  },
                ]),
              );
            }
          } else if (eventType === "tool_call") {
            dataStream.write(
              formatDataStreamPart("data", [
                {
                  type: "tool_call",
                  name: payload?.data?.name || "",
                  arguments: payload?.data?.arguments || {},
                  id: payload?.id ?? payload?.data?.id,
                  request_id: payload?.request_id ?? payload?.data?.request_id,
                },
              ]),
            );
          } else if (eventType === "tool_result") {
            dataStream.write(
              formatDataStreamPart("data", [
                {
                  type: "tool_result",
                  name: payload?.data?.name || "",
                  result: payload?.data?.result || "",
                  id: payload?.id ?? payload?.data?.id,
                  request_id: payload?.request_id ?? payload?.data?.request_id,
                },
              ]),
            );
          } else if (eventType === "final" && !sawToken) {
            const content = payload?.data?.message?.content;
            if (typeof content === "string" && content.length > 0) {
              dataStream.write(formatDataStreamPart("text", content));
            }
          }
        }
      }
    },
  });
}
