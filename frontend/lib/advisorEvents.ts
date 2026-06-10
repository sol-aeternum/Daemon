'use client';

import {
  ChatEvent,
  isAdvisorEvent,
  isToolCallEvent,
  isToolResultEvent,
} from './events';

/**
 * Advisor Event Tree Normalization
 *
 * Transforms flat SSE event streams into hierarchical advisor trees for nested rendering.
 * Uses advisor_id plus stable tool_call_id correlation for nesting and merges.
 *
 * ## Design Invariants
 *
 * 1. **Stable correlation**: tool_call_id is used instead of tool names for matching
 *    nested tool calls with their results and parent advisors.
 *
 * 2. **Bidirectional support**: Same tree shape for live SSE events and persisted
 *    advisor_traces from conversation history.
 *
 * 3. **Root isolation**: Top-level consult_advisor tool call preserved once in
 *    orchestrator log; advisor-internal tool calls only render inside nested blocks.
 *
 * 4. **Bootstrap handling**: Advisor-only data events can start assistant message
 *    state without top-level text appearing first.
 */

export type ToolCallResult = {
  type: 'tool_result';
  name: string;
  result: unknown;
  tool_call_id?: string;
};

export type ToolCallNode = {
  type: 'tool_call';
  name: string;
  arguments: Record<string, unknown>;
  tool_call_id: string;
  result?: ToolCallResult;
  advisorId?: string;
  nestedAdvisors: AdvisorNode[];
};

export type AdvisorNode = {
  type: 'advisor';
  advisorId: string;
  domain: string;
  difficulty: string;
  model: string;
  status: 'running' | 'completed' | 'error';
  startEvent: Extract<ChatEvent, { type: 'advisor_start' }>;
  endEvent?: Extract<ChatEvent, { type: 'advisor_end' }>;
  textDeltas: string[];
  textDone?: string;
  toolCalls: ToolCallNode[];
  thinking: Extract<ChatEvent, { type: 'thinking' }>[];
  error?: string;
  usage?: {
    tokensIn?: number;
    tokensOut?: number;
    latencyMs?: number;
  };
};

export type AdvisorTreeRoot = {
  topLevelToolCalls: ToolCallNode[];
  advisors: AdvisorNode[];
  orphanEvents: ChatEvent[];
  flatEvents: ChatEvent[];
};

/**
 * Build a stable correlation key for an event using tool_call_id as primary
 * identifier, falling back to advisor_id for advisor-scoped events.
 */
function getCorrelationKey(event: ChatEvent): string | null {
  if (isToolCallEvent(event) && event.tool_call_id) {
    return `tc:${event.tool_call_id}`;
  }
  if (isToolResultEvent(event) && event.tool_call_id) {
    return `tr:${event.tool_call_id}`;
  }
  if (event.type === 'advisor_text_delta' && event.advisor_id) {
    return `adv:text_delta:${event.advisor_id}:${event.content}`;
  }
  if (event.type === 'advisor_text_done' && event.advisor_id) {
    return `adv:text_done:${event.advisor_id}:${event.content}`;
  }
  if (event.type === 'thinking' && event.advisor_id) {
    return `adv:thinking:${event.advisor_id}:${event.content}`;
  }
  if (event.type === 'advisor_start' && event.advisor_id) {
    return `adv:start:${event.advisor_id}`;
  }
  if (event.type === 'advisor_end' && event.advisor_id) {
    return `adv:end:${event.advisor_id}`;
  }
  if (isToolCallEvent(event) && event.id) {
    return `call:${event.id}`;
  }
  if (isToolResultEvent(event) && event.id) {
    return `res:${event.id}`;
  }
  return null;
}

function getParentToolCallCandidates(advisor: AdvisorNode): string[] {
  const candidates = [
    advisor.startEvent.tool_call_id,
    advisor.endEvent?.tool_call_id,
    advisor.startEvent.parent_trace_key,
    advisor.endEvent?.parent_trace_key,
  ];

  return candidates.filter(
    (value): value is string => typeof value === 'string' && value.length > 0,
  );
}

/**
 * Build advisor tree from flat events.
 *
 * Algorithm:
 * 1. First pass: Index advisor_start events by advisor_id
 * 2. Second pass: Associate advisor lifecycle events with their advisor
 * 3. Third pass: Match tool_call/tool_result pairs using tool_call_id
 * 4. Fourth pass: Nest advisors under parent tool calls via tool_call_id-aware metadata
 */
export function buildAdvisorTree(events: ChatEvent[]): AdvisorTreeRoot {
  const advisorMap = new Map<string, AdvisorNode>();
  const toolCallMap = new Map<string, ToolCallNode>();
  const topLevelToolCalls: ToolCallNode[] = [];
  const orphanEvents: ChatEvent[] = [];

  // First pass: Create advisor nodes from advisor_start events
  for (const event of events) {
    if (event.type === 'advisor_start') {
      const advisorId = event.advisor_id;
      if (!advisorMap.has(advisorId)) {
        advisorMap.set(advisorId, {
          type: 'advisor',
          advisorId,
          domain: event.domain,
          difficulty: event.difficulty,
          model: event.model,
          status: 'running',
          startEvent: event,
          textDeltas: [],
          toolCalls: [],
          thinking: [],
        });
      }
    }
  }

  // Second pass: Associate lifecycle and content events with advisors
  // Handle thinking events separately first to avoid type narrowing issues
  for (const event of events) {
    if (event.type === 'thinking' && 'advisor_id' in event) {
      const advisorId = event.advisor_id;
      if (!advisorId) {
        orphanEvents.push(event);
        continue;
      }
      const advisor = advisorMap.get(advisorId);
      if (!advisor) {
        orphanEvents.push(event);
        continue;
      }
      if (typeof event.content === 'string') {
        advisor.thinking.push(event);
      }
    }
  }

  // Handle core advisor lifecycle events
  for (const event of events) {
    if (!isAdvisorEvent(event)) continue;

    const advisorId = event.advisor_id;
    if (!advisorId) {
      orphanEvents.push(event);
      continue;
    }

    const advisor = advisorMap.get(advisorId);
    if (!advisor) {
      orphanEvents.push(event);
      continue;
    }

    switch (event.type) {
      case 'advisor_end': {
        advisor.endEvent = event;
        if (event.error) {
          advisor.status = 'error';
          advisor.error = event.error;
        } else {
          advisor.status = 'completed';
        }
        if (
          event.tokens_in !== undefined ||
          event.tokens_out !== undefined ||
          event.latency_ms !== undefined
        ) {
          advisor.usage = {
            tokensIn: event.tokens_in,
            tokensOut: event.tokens_out,
            latencyMs: event.latency_ms,
          };
        }
        break;
      }
      case 'advisor_text_delta': {
        advisor.textDeltas.push(event.content || '');
        break;
      }
      case 'advisor_text_done': {
        advisor.textDone = event.content;
        break;
      }
    }
  }

  // Third pass: Process tool calls and results, using tool_call_id for matching
  for (const event of events) {
    if (isToolCallEvent(event)) {
      const toolCallId =
        event.tool_call_id ||
        event.id ||
        `anon-${Math.random().toString(36).slice(2, 9)}`;

      // Check if this is an advisor-internal call (has advisor_id)
      const isAdvisorInternal = 'advisor_id' in event && event.advisor_id;

      const node: ToolCallNode = {
        type: 'tool_call',
        name: event.name,
        arguments: event.arguments,
        tool_call_id: toolCallId,
        advisorId: 'advisor_id' in event ? event.advisor_id : undefined,
        nestedAdvisors: [],
      };

      toolCallMap.set(toolCallId, node);

      if (!isAdvisorInternal) {
        // Top-level tool call (like consult_advisor)
        topLevelToolCalls.push(node);
      }
      // Advisor-internal calls will be associated with their advisor in fourth pass
    }

    if (isToolResultEvent(event)) {
      const toolCallId = event.tool_call_id || event.id;
      if (!toolCallId) {
        orphanEvents.push(event);
        continue;
      }

      const toolCall = toolCallMap.get(toolCallId);
      if (toolCall) {
        toolCall.result = {
          type: 'tool_result',
          name: event.name,
          result: event.result,
          tool_call_id: toolCallId,
        };
      } else {
        // Result without matching call - keep as orphan
        orphanEvents.push(event);
      }
    }
  }

  // Fourth pass: Nest advisors under their parent tool calls
  for (const advisor of advisorMap.values()) {
    const parentCandidates = getParentToolCallCandidates(advisor);

    if (parentCandidates.length > 0) {
      const parentToolCall = parentCandidates
        .map((candidate) => {
          return (
            toolCallMap.get(candidate) ||
            topLevelToolCalls.find(
              (tc) =>
                tc.tool_call_id === candidate ||
                (tc.result && getToolResultId(tc.result) === candidate),
            )
          );
        })
        .find((candidate): candidate is ToolCallNode => Boolean(candidate));

      if (parentToolCall) {
        parentToolCall.nestedAdvisors.push(advisor);
      }
    }
  }

  // Associate advisor-internal tool calls with their advisors
  for (const toolCall of toolCallMap.values()) {
    if (
      toolCall.advisorId &&
      !toolCall.result?.name?.startsWith('consult_advisor')
    ) {
      const advisor = advisorMap.get(toolCall.advisorId);
      if (advisor) {
        // Check if already nested under this advisor
        const alreadyNested = advisor.toolCalls.some(
          (tc) => tc.tool_call_id === toolCall.tool_call_id,
        );
        if (!alreadyNested) {
          advisor.toolCalls.push(toolCall);
        }
      }
    }
  }

  // Collect root advisors (those not nested under any tool call)
  const nestedAdvisorIds = new Set<string>();
  for (const toolCall of [
    ...topLevelToolCalls,
    ...Array.from(toolCallMap.values()),
  ]) {
    for (const nested of toolCall.nestedAdvisors) {
      nestedAdvisorIds.add(nested.advisorId);
    }
  }

  const rootAdvisors = Array.from(advisorMap.values()).filter(
    (a) => !nestedAdvisorIds.has(a.advisorId),
  );

  return {
    topLevelToolCalls,
    advisors: rootAdvisors,
    orphanEvents,
    flatEvents: events,
  };
}

function getToolResultId(result: ToolCallResult): string | undefined {
  if (typeof result.result === 'object' && result.result !== null) {
    const r = result.result as Record<string, unknown>;
    if (typeof r.trace_key === 'string') return r.trace_key;
    if (typeof r.id === 'string') return r.id;
  }
  return result.tool_call_id;
}

type AccumulatorTrace = {
  advisor_id: string;
  text_parts?: string[];
  reasoning_parts?: string[];
  tool_calls?: Array<{
    name: string;
    arguments?: Record<string, unknown>;
    tool_call_id?: string;
    id?: string;
  }>;
  tool_results?: Array<{
    name: string;
    result?: unknown;
    tool_call_id?: string;
    id?: string;
  }>;
  errors?: string[];
  usage?: {
    tokens_in?: number;
    tokens_out?: number;
    prompt_tokens?: number;
    completion_tokens?: number;
    latency_ms?: number;
  } | null;
  trace_key?: string | null;
  parent_trace_key?: string | null;
  tool_call_id?: string | null;
  event_tags?: Record<string, unknown>;
  domain?: string;
  difficulty?: string;
  model?: string;
  status?: string;
  error?: string | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  latency_ms?: number | null;
};

/**
 * Normalize persisted advisor_traces from conversation payloads.
 * Converts the accumulator-shaped backend format (text_parts, tool_calls, etc.)
 * into the same tree shape as live events by hydrating events from accumulator fields.
 */
export function normalizePersistedAdvisorTraces(
  advisorTraces: Record<string, unknown> | null | undefined,
): AdvisorTreeRoot {
  if (!advisorTraces || typeof advisorTraces !== 'object') {
    return {
      topLevelToolCalls: [],
      advisors: [],
      orphanEvents: [],
      flatEvents: [],
    };
  }

  const flatEvents: ChatEvent[] = [];
  const syntheticToolCalls = new Map<string, ToolCallNode>();

  // advisor_traces is a Record<advisor_id, AccumulatorTrace>
  // Backend stores accumulator shape: text_parts, tool_calls, tool_results, errors, usage, etc.
  // Hydrate back into event format for unified tree building
  // Also reconstruct top-level consult_advisor wrapper when parent_trace_key indicates it
  for (const [advisorId, trace] of Object.entries(advisorTraces)) {
    if (!trace || typeof trace !== 'object') continue;

    const t = trace as AccumulatorTrace;

    // Determine domain/difficulty/model from trace or use defaults
    const domain = t.domain || 'general';
    const difficulty = t.difficulty || 'mid';
    const model = t.model || 'unknown';
    const tokensIn =
      t.tokens_in ?? t.usage?.tokens_in ?? t.usage?.prompt_tokens;
    const tokensOut =
      t.tokens_out ?? t.usage?.tokens_out ?? t.usage?.completion_tokens;
    const latencyMs = t.latency_ms ?? t.usage?.latency_ms;
    const parentTraceKey = t.parent_trace_key || null;
    const traceKey = t.trace_key || null;
    const toolCallId = t.tool_call_id || null;

    // Detect if this advisor was triggered by a top-level tool call
    // parent_trace_key starting with "req_" indicates a top-level request-level trigger
    // tool_call_id presence indicates a specific tool call triggered this advisor
    const isTopLevelToolTriggered =
      parentTraceKey?.startsWith('req_') && toolCallId;

    // Generate stable synthetic tool call ID for consult_advisor wrapper
    const syntheticToolCallId =
      isTopLevelToolTriggered && toolCallId ? toolCallId : null;

    // Reconstruct top-level consult_advisor tool call if triggered by one
    if (
      isTopLevelToolTriggered &&
      syntheticToolCallId &&
      !syntheticToolCalls.has(syntheticToolCallId)
    ) {
      // Create synthetic consult_advisor tool call
      const syntheticToolCall: ToolCallNode = {
        type: 'tool_call',
        name: 'consult_advisor',
        arguments: { domain, difficulty },
        tool_call_id: syntheticToolCallId,
        nestedAdvisors: [],
      };
      syntheticToolCalls.set(syntheticToolCallId, syntheticToolCall);

      // Emit synthetic tool_call event
      flatEvents.push({
        type: 'tool_call',
        name: 'consult_advisor',
        arguments: { domain, difficulty },
        tool_call_id: syntheticToolCallId,
        id: syntheticToolCallId,
      } as ChatEvent);

      // Emit synthetic tool_result for the consult_advisor
      flatEvents.push({
        type: 'tool_result',
        name: 'consult_advisor',
        result: { answer: t.text_parts?.join('') || '', sufficient: true },
        tool_call_id: syntheticToolCallId,
        id: syntheticToolCallId,
      } as ChatEvent);
    }

    const traceMeta = {
      ...(parentTraceKey ? { parent_trace_key: parentTraceKey } : {}),
      ...(traceKey ? { trace_key: traceKey } : {}),
      ...(t.event_tags ? { event_tags: t.event_tags } : {}),
    };
    const lifecycleMeta = {
      ...traceMeta,
      ...(toolCallId ? { tool_call_id: toolCallId } : {}),
    };

    // Build advisor_start event from accumulated metadata
    const startEvent: ChatEvent = {
      type: 'advisor_start',
      advisor_id: advisorId,
      domain,
      difficulty,
      model,
      ...lifecycleMeta,
    } as ChatEvent;
    flatEvents.push(startEvent);

    // Hydrate reasoning_parts into thinking events
    if (Array.isArray(t.reasoning_parts)) {
      for (const content of t.reasoning_parts) {
        if (typeof content === 'string') {
          flatEvents.push({
            type: 'thinking',
            content,
            advisor_id: advisorId,
            ...traceMeta,
          } as ChatEvent);
        }
      }
    }

    // Hydrate tool_calls into tool_call events
    if (Array.isArray(t.tool_calls)) {
      for (const tc of t.tool_calls) {
        const toolCallId =
          tc.tool_call_id ||
          tc.id ||
          `persisted-${Math.random().toString(36).slice(2, 9)}`;
        flatEvents.push({
          type: 'tool_call',
          name: tc.name,
          arguments: tc.arguments || {},
          tool_call_id: toolCallId,
          advisor_id: advisorId,
          ...traceMeta,
        } as ChatEvent);
      }
    }

    // Hydrate tool_results into tool_result events
    if (Array.isArray(t.tool_results)) {
      for (const tr of t.tool_results) {
        const toolCallId = tr.tool_call_id || tr.id;
        flatEvents.push({
          type: 'tool_result',
          name: tr.name,
          result: tr.result,
          ...(toolCallId ? { tool_call_id: toolCallId } : {}),
          advisor_id: advisorId,
          ...traceMeta,
        } as ChatEvent);
      }
    }

    // Hydrate text_parts into advisor_text_delta events (concatenate for single done)
    if (Array.isArray(t.text_parts) && t.text_parts.length > 0) {
      const fullText = t.text_parts.join('');
      // Emit deltas for streaming fidelity in replay
      for (const part of t.text_parts) {
        flatEvents.push({
          type: 'advisor_text_delta',
          content: part,
          advisor_id: advisorId,
          ...lifecycleMeta,
        } as ChatEvent);
      }
      // Emit done with full text
      flatEvents.push({
        type: 'advisor_text_done',
        content: fullText,
        advisor_id: advisorId,
        ...lifecycleMeta,
      } as ChatEvent);
    }

    // Build advisor_end event from accumulated status/errors/usage
    const hasErrors = Array.isArray(t.errors) && t.errors.length > 0;
    const endEvent: ChatEvent = {
      type: 'advisor_end',
      advisor_id: advisorId,
      status: t.status || (hasErrors ? 'error' : 'completed'),
      ...(t.error
        ? { error: t.error }
        : hasErrors
          ? { error: t.errors![0] }
          : {}),
      ...(tokensIn !== undefined ? { tokens_in: tokensIn } : {}),
      ...(tokensOut !== undefined ? { tokens_out: tokensOut } : {}),
      ...(latencyMs !== undefined ? { latency_ms: latencyMs } : {}),
      ...lifecycleMeta,
    } as ChatEvent;
    flatEvents.push(endEvent);
  }

  return buildAdvisorTree(flatEvents);
}

function isValidChatEvent(event: unknown): event is ChatEvent {
  if (typeof event !== 'object' || event === null) return false;
  const e = event as Record<string, unknown>;
  return typeof e.type === 'string' && e.type.length > 0;
}

/**
 * Check if any advisor events exist in the tree.
 */
export function hasAdvisorEvents(tree: AdvisorTreeRoot): boolean {
  return (
    tree.advisors.length > 0 ||
    tree.topLevelToolCalls.some((tc) => tc.nestedAdvisors.length > 0)
  );
}

/**
 * Get the consolidated text content for an advisor.
 * Uses text_done if available, otherwise concatenates deltas.
 */
export function getAdvisorTextContent(advisor: AdvisorNode): string {
  if (advisor.textDone) {
    return advisor.textDone;
  }
  return advisor.textDeltas.join('');
}

/**
 * Flatten tree back to events for archive storage.
 * Maintains order: advisor_start → thinking → tool_calls → text_deltas → advisor_end
 */
export function flattenAdvisorTree(tree: AdvisorTreeRoot): ChatEvent[] {
  const events: ChatEvent[] = [];

  // Process top-level tool calls first (in order)
  for (const toolCall of tree.topLevelToolCalls) {
    events.push({
      type: 'tool_call',
      name: toolCall.name,
      arguments: toolCall.arguments,
      tool_call_id: toolCall.tool_call_id,
    } as ChatEvent);

    if (toolCall.result) {
      events.push({
        type: 'tool_result',
        name: toolCall.result.name,
        result: toolCall.result.result,
        tool_call_id: toolCall.result.tool_call_id,
      } as ChatEvent);
    }

    // Add nested advisors
    for (const advisor of toolCall.nestedAdvisors) {
      events.push(...flattenAdvisorNode(advisor));
    }
  }

  // Add root advisors
  for (const advisor of tree.advisors) {
    events.push(...flattenAdvisorNode(advisor));
  }

  // Add orphans at the end
  events.push(...tree.orphanEvents);

  return events;
}

function flattenAdvisorNode(advisor: AdvisorNode): ChatEvent[] {
  const events: ChatEvent[] = [];

  // Start event
  events.push(advisor.startEvent);

  // Thinking events
  events.push(...advisor.thinking);

  // Tool calls (advisor-internal)
  for (const toolCall of advisor.toolCalls) {
    events.push({
      type: 'tool_call',
      name: toolCall.name,
      arguments: toolCall.arguments,
      tool_call_id: toolCall.tool_call_id,
      advisor_id: advisor.advisorId,
    } as ChatEvent);

    if (toolCall.result) {
      events.push({
        type: 'tool_result',
        name: toolCall.result.name,
        result: toolCall.result.result,
        tool_call_id: toolCall.result.tool_call_id,
        advisor_id: advisor.advisorId,
      } as ChatEvent);
    }

    // Nested advisors (recursively)
    for (const nestedAdvisor of toolCall.nestedAdvisors) {
      events.push(...flattenAdvisorNode(nestedAdvisor));
    }
  }

  // Text deltas
  for (const delta of advisor.textDeltas) {
    events.push({
      type: 'advisor_text_delta',
      content: delta,
      advisor_id: advisor.advisorId,
    } as ChatEvent);
  }

  // Text done
  if (advisor.textDone) {
    events.push({
      type: 'advisor_text_done',
      content: advisor.textDone,
      advisor_id: advisor.advisorId,
    } as ChatEvent);
  }

  // End event
  if (advisor.endEvent) {
    events.push(advisor.endEvent);
  }

  return events;
}

/**
 * Merge live events with persisted advisor traces.
 * Useful when replaying history and then streaming new events.
 */
export function mergeAdvisorTrees(
  persisted: AdvisorTreeRoot,
  live: AdvisorTreeRoot,
): AdvisorTreeRoot {
  // Merge flat events, preserving intentional repeated chunks while
  // deduplicating overlap between persisted replay and live streaming.
  const persistedCounts = new Map<string, number>();
  const liveCounts = new Map<string, number>();

  for (const event of persisted.flatEvents) {
    const key = getCorrelationKey(event) || JSON.stringify(event);
    persistedCounts.set(key, (persistedCounts.get(key) || 0) + 1);
  }

  for (const event of live.flatEvents) {
    const key = getCorrelationKey(event) || JSON.stringify(event);
    liveCounts.set(key, (liveCounts.get(key) || 0) + 1);
  }

  const emittedCounts = new Map<string, number>();
  const mergedEvents: ChatEvent[] = [];

  for (const event of [...persisted.flatEvents, ...live.flatEvents]) {
    const key = getCorrelationKey(event) || JSON.stringify(event);
    const allowedCount = Math.max(
      persistedCounts.get(key) || 0,
      liveCounts.get(key) || 0,
    );
    const emittedCount = emittedCounts.get(key) || 0;
    if (emittedCount < allowedCount) {
      emittedCounts.set(key, emittedCount + 1);
      mergedEvents.push(event);
    }
  }

  return buildAdvisorTree(mergedEvents);
}
