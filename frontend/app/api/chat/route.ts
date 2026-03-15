const API_URLS = [
  process.env.DAEMON_INTERNAL_API_URL,
  process.env.NEXT_PUBLIC_API_URL,
  "http://backend:8000",
  "http://localhost:8000",
].filter((url): url is string => Boolean(url));

export async function POST(req: Request) {
  const { messages, id, model, attachments, metadata, provider } = await req.json();

  const { createDataStreamResponse } = await import("ai");
  const { formatDataStreamPart } = await import("@ai-sdk/ui-utils");

  const normalizedMessages = (messages || []).map((m: any) => ({
    role: m.role,
    content: m.content,
  }));

  const extractTextContent = (content: unknown): string => {
    if (typeof content === "string") {
      return content;
    }
    if (Array.isArray(content)) {
      return content
        .map((part) => {
          if (
            part
            && typeof part === "object"
            && "type" in part
            && (part as { type?: unknown }).type === "text"
            && "text" in part
            && typeof (part as { text?: unknown }).text === "string"
          ) {
            return (part as { text: string }).text;
          }
          return "";
        })
        .filter(Boolean)
        .join("\n")
        .trim();
    }
    return "";
  };

  const lastUserMessage = [...normalizedMessages].reverse().find((m) => m.role === "user");
  const lastUserText = extractTextContent(lastUserMessage?.content);

  const authHeader = req.headers.get("authorization");
  const hasServerApiKey = Boolean(process.env.DAEMON_API_KEY?.trim());
  const authorization = authHeader?.trim() || null;

  if (!authorization && hasServerApiKey) {
    return new Response(JSON.stringify({ error: "Missing bearer token" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }

  let backendRes: Response | null = null;
  let lastError: Error | null = null;

  for (const apiUrl of API_URLS) {
    try {
      backendRes = await fetch(`${apiUrl}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(authorization ? { Authorization: authorization } : {}),
        },
        body: JSON.stringify({
          message: lastUserText,
          conversation_id: id || null,
          messages: normalizedMessages,
          model: model || "auto",
          provider: provider || null,
          attachments: Array.isArray(attachments) ? attachments : [],
          metadata: metadata && typeof metadata === "object" ? metadata : null,
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
            const delta = payload?.data?.text ?? payload?.data?.delta ?? payload?.text ?? payload?.delta;
            if (typeof delta === "string" && delta.length > 0) {
              sawToken = true;
              dataStream.write(formatDataStreamPart("text", delta));
            }
          } else if (eventType === "thinking") {
            const content = payload?.data?.content ?? payload?.content;
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
          } else if (eventType === "routing") {
            const modelId = payload?.data?.model;
            if (typeof modelId === "string" && modelId.length > 0) {
              dataStream.write(
                formatDataStreamPart("data", [
                  {
                    type: "routing",
                    model: modelId,
                    tier: payload?.data?.tier,
                    reason: payload?.data?.reason,
                    id: payload?.id ?? payload?.data?.id,
                    request_id: payload?.request_id ?? payload?.data?.request_id,
                  },
                ]),
              );
            }
          } else if (eventType === "conversation") {
            const conversationId = payload?.data?.conversation_id || payload?.conversation_id;
            if (conversationId) {
              dataStream.write(
                formatDataStreamPart("data", [
                  {
                    type: "conversation",
                    conversation_id: conversationId,
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
          } else if (eventType === "video_generating") {
            const requestId = payload?.data?.request_id ?? payload?.request_id;
            const estimatedSeconds = payload?.data?.estimated_seconds ?? payload?.estimated_seconds;
            if (requestId) {
              dataStream.write(
                formatDataStreamPart("data", [
                  {
                    type: "video_generating",
                    request_id: requestId,
                    estimated_seconds: typeof estimatedSeconds === "number" ? estimatedSeconds : 0,
                    id: payload?.id ?? payload?.data?.id,
                  },
                ]),
              );
            }
          } else if (eventType === "video_complete") {
            const requestId = payload?.data?.request_id ?? payload?.request_id;
            const url = payload?.data?.url ?? payload?.url;
            if (requestId && url) {
              dataStream.write(
                formatDataStreamPart("data", [
                  {
                    type: "video_complete",
                    request_id: requestId,
                    url: url,
                    duration: payload?.data?.duration ?? payload?.duration,
                    resolution: payload?.data?.resolution ?? payload?.resolution,
                    id: payload?.id ?? payload?.data?.id,
                  },
                ]),
              );
            }
          } else if (eventType === "video_failed") {
            const requestId = payload?.data?.request_id ?? payload?.request_id;
            const error = payload?.data?.error ?? payload?.error;
            if (requestId) {
              dataStream.write(
                formatDataStreamPart("data", [
                  {
                    type: "video_failed",
                    request_id: requestId,
                    error: error || "Video generation failed",
                    refunded: payload?.data?.refunded ?? payload?.refunded ?? false,
                    id: payload?.id ?? payload?.data?.id,
                  },
                ]),
              );
            }
          } else if (eventType === "final" && !sawToken) {
            const content =
              payload?.data?.text
              ?? payload?.data?.message?.content
              ?? payload?.text
              ?? payload?.message?.content;
            if (typeof content === "string" && content.length > 0) {
              dataStream.write(formatDataStreamPart("text", content));
            }
          }
        }
      }
    },
  });
}
