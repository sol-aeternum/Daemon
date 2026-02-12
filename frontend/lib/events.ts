type BaseEvent = { id?: string; request_id?: string };

export type ChatEvent = BaseEvent &
  (
    | { type: "text"; content: string }
    | { type: "thinking"; content: string; agent?: string }
    | { type: "agent_spawn"; agent: string; agentType: string; task: string }
    | { type: "agent_status"; agent: string; status: "pending" | "running" | "completed" | "error"; progress?: number; message?: string }
    | { type: "agent_complete"; agent: string; result: string }
    | { type: "image_ready"; url: string; prompt: string }
    | { type: "tool_call"; name: string; arguments: Record<string, any> }
    | { type: "tool_result"; name: string; result: string }
    | { type: "pipeline_switch"; pipeline: "cloud" | "local" }
  );

export function isChatEvent(obj: unknown): obj is ChatEvent {
  if (typeof obj !== "object" || obj === null) return false;
  const event = obj as { type?: string };
  const validTypes = [
    "text",
    "thinking",
    "agent_spawn",
    "agent_status",
    "agent_complete",
    "image_ready",
    "tool_call",
    "tool_result",
    "pipeline_switch",
  ];
  return typeof event.type === "string" && validTypes.includes(event.type);
}
