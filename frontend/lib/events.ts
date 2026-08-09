type BaseEvent = { id?: string; request_id?: string };
type TraceMeta = {
  tool_call_id?: string;
  advisor_id?: string;
  event_tags?: Record<string, unknown>;
  trace_key?: string;
  parent_trace_key?: string;
};
type AdvisorBaseEvent = TraceMeta & {
  advisor_id: string;
};
type AdvisorUsage = {
  total_tokens?: number;
  tokens_in?: number;
  tokens_out?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  latency_ms?: number;
};

export type ChatEvent = BaseEvent &
  (
    | { type: 'text'; content: string }
    | ({ type: 'thinking'; content: string; agent?: string } & TraceMeta)
    | { type: 'routing'; model: string; tier?: string; reason?: string }
    | { type: 'agent_spawn'; agent: string; agentType: string; task: string }
    | {
        type: 'agent_status';
        agent: string;
        status: 'pending' | 'running' | 'completed' | 'error';
        progress?: number;
        message?: string;
      }
    | { type: 'agent_complete'; agent: string; result: string }
    | { type: 'image_ready'; url: string; prompt: string }
    | {
        type: 'video_generating';
        request_id: string;
        estimated_seconds: number;
      }
    | {
        type: 'video_complete';
        request_id: string;
        url: string;
        duration: number;
        resolution: string;
      }
    | {
        type: 'video_failed';
        request_id: string;
        error: string;
        refunded: boolean;
      }
    | ({
        type: 'tool_call';
        name: string;
        arguments: Record<string, any>;
      } & TraceMeta)
    | ({ type: 'tool_result'; name: string; result: any } & TraceMeta)
    | ({
        type: 'advisor_start';
        domain: string;
        difficulty: string;
        model: string;
      } & AdvisorBaseEvent)
    | ({ type: 'advisor_text_delta'; content: string } & AdvisorBaseEvent)
    | ({ type: 'advisor_text_done'; content: string } & AdvisorBaseEvent)
    | ({
        type: 'advisor_error';
        error: string;
      } & AdvisorBaseEvent)
    | ({
        type: 'advisor_end';
        status: 'completed' | 'error';
        error?: string;
        tokens_in?: number;
        tokens_out?: number;
        latency_ms?: number;
        usage?: AdvisorUsage;
      } & AdvisorBaseEvent)
    | { type: 'pipeline_switch'; pipeline: 'cloud' | 'local' }
    | { type: 'conversation'; conversation_id: string }
    | {
        type: 'council_interview';
        roster: Record<string, any>;
        presets: string[];
        rounds_options: number[];
        audit_default: boolean;
      }
    | {
        type: 'council_progress';
        stage: string;
        current_round: number;
        total_rounds: number;
        models_complete: number;
        models_total: number;
      }
    | {
        type: 'council_output';
        section: string;
        content: string;
        metadata: Record<string, any>;
      }
    | {
        type: 'council_done';
        session_id: string;
        total_tokens: number;
        total_cost_usd: number;
        models_used: string[];
      }
    | { type: 'council_error'; error: string }
  );

export function isChatEvent(obj: unknown): obj is ChatEvent {
  if (typeof obj !== 'object' || obj === null) return false;
  const event = obj as { type?: string };
  const validTypes = [
    'text',
    'thinking',
    'routing',
    'agent_spawn',
    'agent_status',
    'agent_complete',
    'image_ready',
    'video_generating',
    'video_complete',
    'video_failed',
    'tool_call',
    'tool_result',
    'advisor_start',
    'advisor_text_delta',
    'advisor_text_done',
    'advisor_error',
    'advisor_end',
    'pipeline_switch',
    'conversation',
    'council_interview',
    'council_progress',
    'council_output',
    'council_done',
    'council_error',
  ];
  return typeof event.type === 'string' && validTypes.includes(event.type);
}

export function isToolCallEvent(event: ChatEvent): event is ChatEvent & {
  type: 'tool_call';
  name: string;
  arguments: Record<string, any>;
} {
  return event.type === 'tool_call';
}

export function isToolResultEvent(
  event: ChatEvent,
): event is ChatEvent & { type: 'tool_result'; name: string; result: any } {
  return event.type === 'tool_result';
}

export function isAdvisorEvent(event: ChatEvent): event is ChatEvent & {
  type:
    | 'advisor_start'
    | 'advisor_text_delta'
    | 'advisor_text_done'
    | 'advisor_error'
    | 'advisor_end';
} {
  return [
    'advisor_start',
    'advisor_text_delta',
    'advisor_text_done',
    'advisor_error',
    'advisor_end',
  ].includes(event.type);
}

export function isAdvisorStartEvent(event: ChatEvent): event is ChatEvent & {
  type: 'advisor_start';
  advisor_id: string;
  domain: string;
  difficulty: string;
  model: string;
} {
  return event.type === 'advisor_start';
}

export function isAdvisorTextDeltaEvent(
  event: ChatEvent,
): event is ChatEvent & {
  type: 'advisor_text_delta';
  advisor_id: string;
  content: string;
} {
  return event.type === 'advisor_text_delta';
}

export function isAdvisorTextDoneEvent(event: ChatEvent): event is ChatEvent & {
  type: 'advisor_text_done';
  advisor_id: string;
  content: string;
} {
  return event.type === 'advisor_text_done';
}

export function isAdvisorEndEvent(event: ChatEvent): event is ChatEvent & {
  type: 'advisor_end';
  advisor_id: string;
  status: 'completed' | 'error';
} {
  return event.type === 'advisor_end';
}

export function isVideoGeneratingEvent(event: ChatEvent): event is ChatEvent & {
  type: 'video_generating';
  request_id: string;
  estimated_seconds: number;
} {
  return event.type === 'video_generating';
}

export function isVideoCompleteEvent(event: ChatEvent): event is ChatEvent & {
  type: 'video_complete';
  request_id: string;
  url: string;
  duration: number;
  resolution: string;
} {
  return event.type === 'video_complete';
}

export function isVideoFailedEvent(event: ChatEvent): event is ChatEvent & {
  type: 'video_failed';
  request_id: string;
  error: string;
  refunded: boolean;
} {
  return event.type === 'video_failed';
}
export function isCouncilEvent(event: ChatEvent): event is ChatEvent & {
  type:
    | 'council_interview'
    | 'council_progress'
    | 'council_output'
    | 'council_done'
    | 'council_error';
} {
  if (!event || typeof event !== 'object') return false;
  return [
    'council_interview',
    'council_progress',
    'council_output',
    'council_done',
    'council_error',
  ].includes(event.type);
}

export function isCouncilInterviewEvent(
  event: ChatEvent,
): event is ChatEvent & {
  type: 'council_interview';
  roster: Record<string, any>;
  presets: string[];
  rounds_options: number[];
  audit_default: boolean;
} {
  if (!event || typeof event !== 'object') return false;
  return event.type === 'council_interview';
}

export function isCouncilProgressEvent(event: ChatEvent): event is ChatEvent & {
  type: 'council_progress';
  stage: string;
  current_round: number;
  total_rounds: number;
  models_complete: number;
  models_total: number;
} {
  if (!event || typeof event !== 'object') return false;
  return event.type === 'council_progress';
}

export function isCouncilOutputEvent(event: ChatEvent): event is ChatEvent & {
  type: 'council_output';
  section: string;
  content: string;
  metadata: Record<string, any>;
} {
  if (!event || typeof event !== 'object') return false;
  return event.type === 'council_output';
}

export function isCouncilDoneEvent(event: ChatEvent): event is ChatEvent & {
  type: 'council_done';
  session_id: string;
  total_tokens: number;
  total_cost_usd: number;
  models_used: string[];
} {
  if (!event || typeof event !== 'object') return false;
  return event.type === 'council_done';
}

export function isCouncilErrorEvent(
  event: ChatEvent,
): event is ChatEvent & { type: 'council_error'; error: string } {
  if (!event || typeof event !== 'object') return false;
  return event.type === 'council_error';
}
