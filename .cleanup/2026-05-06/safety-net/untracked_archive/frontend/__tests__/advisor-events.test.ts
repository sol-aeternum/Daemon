import { describe, expect, it } from "vitest";

import {
  isAdvisorEndEvent,
  isAdvisorEvent,
  isAdvisorStartEvent,
  isAdvisorTextDeltaEvent,
  isAdvisorTextDoneEvent,
  isChatEvent,
  isToolCallEvent,
  isToolResultEvent,
  type ChatEvent,
} from "../lib/events";

import {
  buildAdvisorTree,
  normalizePersistedAdvisorTraces,
  mergeAdvisorTrees,
  getAdvisorTextContent,
  hasAdvisorEvents,
  type AdvisorTreeRoot,
} from "../lib/advisorEvents";

function buildRealPersistedAdvisorTrace(overrides: Record<string, unknown> = {}) {
  return {
    advisor_id: "advisor_coding_1",
    text_parts: ["Review ", "the ", "auth ", "boundary."],
    reasoning_parts: ["Analyzing...", "Done"],
    tool_calls: [
      { name: "get_time", arguments: { format: "iso" }, tool_call_id: "call_1" },
    ],
    tool_results: [
      { name: "get_time", result: { time: "2026-04-19T12:00:00Z" }, tool_call_id: "call_1" },
    ],
    errors: [],
    usage: {
      prompt_tokens: 150,
      completion_tokens: 75,
      total_tokens: 225,
      tokens_in: 150,
      tokens_out: 75,
      latency_ms: 2500,
    },
    trace_key: "trace_abc_123",
    parent_trace_key: "req_test:assistant",
    tool_call_id: "call_consult_1",
    event_tags: { source: "consult_advisor" },
    domain: "coding",
    difficulty: "high",
    model: "openrouter/anthropic/claude-opus-4.7",
    status: "completed",
    tokens_in: 150,
    tokens_out: 75,
    latency_ms: 2500,
    ...overrides,
  };
}

describe("Advisor event protocol", () => {
  it("accepts advisor lifecycle and text events as chat events", () => {
    const events: ChatEvent[] = [
      {
        type: "advisor_start",
        advisor_id: "advisor_coding_1",
        domain: "coding",
        difficulty: "high",
        model: "openrouter/anthropic/claude-opus-4.7",
        tool_call_id: "call_consult_advisor",
      },
      {
        type: "advisor_text_delta",
        advisor_id: "advisor_coding_1",
        content: "Review the auth boundary first.",
      },
      {
        type: "advisor_text_done",
        advisor_id: "advisor_coding_1",
        content: "Review the auth boundary first.",
      },
      {
        type: "advisor_end",
        advisor_id: "advisor_coding_1",
        status: "completed",
        latency_ms: 125,
        tokens_in: 21,
        tokens_out: 13,
      },
    ];

    events.forEach((event) => {
      expect(isChatEvent(event)).toBe(true);
      expect(isAdvisorEvent(event)).toBe(true);
    });
  });

  it("narrows advisor-specific event guards", () => {
    const advisorStart: ChatEvent = {
      type: "advisor_start",
      advisor_id: "advisor_research_1",
      domain: "research",
      difficulty: "mid",
      model: "openrouter/anthropic/claude-sonnet-4.6",
    };
    const advisorEnd: ChatEvent = {
      type: "advisor_end",
      advisor_id: "advisor_research_1",
      status: "completed",
      usage: { total_tokens: 12 },
    };
    const advisorTextDelta: ChatEvent = {
      type: "advisor_text_delta",
      advisor_id: "advisor_research_1",
      content: "Check the upstream docs.",
    };
    const advisorTextDone: ChatEvent = {
      type: "advisor_text_done",
      advisor_id: "advisor_research_1",
      content: "Check the upstream docs.",
    };

    expect(isAdvisorStartEvent(advisorStart)).toBe(true);
    expect(isAdvisorEndEvent(advisorEnd)).toBe(true);
    expect(isAdvisorTextDeltaEvent(advisorTextDelta)).toBe(true);
    expect(isAdvisorTextDoneEvent(advisorTextDone)).toBe(true);
    expect(isAdvisorStartEvent(advisorEnd)).toBe(false);
    expect(isAdvisorEndEvent(advisorTextDelta)).toBe(false);
  });

  it("keeps nested advisor tool metadata on tool call and result events", () => {
    const toolCall: ChatEvent = {
      type: "tool_call",
      name: "get_time",
      arguments: { format: "iso" },
      advisor_id: "advisor_general_1",
      tool_call_id: "call_nested_1",
    };
    const toolResult: ChatEvent = {
      type: "tool_result",
      name: "get_time",
      result: { time: "2026-04-19T12:34:56Z" },
      advisor_id: "advisor_general_1",
      tool_call_id: "call_nested_1",
    };

    expect(isChatEvent(toolCall)).toBe(true);
    expect(isChatEvent(toolResult)).toBe(true);
    expect(isToolCallEvent(toolCall)).toBe(true);
    expect(isToolResultEvent(toolResult)).toBe(true);
    expect(toolCall.tool_call_id).toBe("call_nested_1");
    expect(toolResult.tool_call_id).toBe("call_nested_1");
  });
});

describe("buildAdvisorTree - live SSE event tree building", () => {
  it("builds empty tree from empty events array", () => {
    const tree = buildAdvisorTree([]);
    expect(tree.topLevelToolCalls).toHaveLength(0);
    expect(tree.advisors).toHaveLength(0);
    expect(tree.orphanEvents).toHaveLength(0);
    expect(tree.flatEvents).toHaveLength(0);
    expect(hasAdvisorEvents(tree)).toBe(false);
  });

  it("builds tree with single advisor nested under consult_advisor tool call", () => {
    const events: ChatEvent[] = [
      {
        type: "tool_call",
        name: "consult_advisor",
        arguments: { domain: "coding", difficulty: "high" },
        id: "call_consult_1",
        tool_call_id: "call_consult_1",
      },
      {
        type: "advisor_start",
        advisor_id: "advisor_coding_1",
        domain: "coding",
        difficulty: "high",
        model: "openrouter/anthropic/claude-opus-4.7",
        parent_trace_key: "call_consult_1",
      },
      {
        type: "advisor_text_delta",
        advisor_id: "advisor_coding_1",
        content: "Review the auth boundary",
      },
      {
        type: "advisor_text_done",
        advisor_id: "advisor_coding_1",
        content: "Review the auth boundary first.",
      },
      {
        type: "advisor_end",
        advisor_id: "advisor_coding_1",
        status: "completed",
        latency_ms: 125,
        tokens_in: 21,
        tokens_out: 13,
      },
      {
        type: "tool_result",
        name: "consult_advisor",
        result: { answer: "Review the auth boundary first.", sufficient: true },
        id: "call_consult_1",
        tool_call_id: "call_consult_1",
      },
    ];

    const tree = buildAdvisorTree(events);
    expect(tree.topLevelToolCalls).toHaveLength(1);
    expect(tree.topLevelToolCalls[0].name).toBe("consult_advisor");
    expect(tree.topLevelToolCalls[0].nestedAdvisors).toHaveLength(1);
    expect(tree.advisors).toHaveLength(0);
    expect(hasAdvisorEvents(tree)).toBe(true);

    const advisor = tree.topLevelToolCalls[0].nestedAdvisors[0];
    expect(advisor.advisorId).toBe("advisor_coding_1");
    expect(advisor.domain).toBe("coding");
    expect(advisor.status).toBe("completed");
    expect(getAdvisorTextContent(advisor)).toBe("Review the auth boundary first.");
  });

  it("nests an advisor under consult_advisor using advisor tool_call_id even when parent_trace_key is assistant-scoped", () => {
    const events: ChatEvent[] = [
      {
        type: "tool_call",
        name: "consult_advisor",
        arguments: { domain: "coding", difficulty: "high" },
        tool_call_id: "call_consult_2",
      },
      {
        type: "advisor_start",
        advisor_id: "advisor_coding_2",
        domain: "coding",
        difficulty: "high",
        model: "openrouter/anthropic/claude-opus-4.7",
        parent_trace_key: "req_123:assistant",
        tool_call_id: "call_consult_2",
      },
      {
        type: "advisor_end",
        advisor_id: "advisor_coding_2",
        status: "completed",
        parent_trace_key: "req_123:assistant",
        tool_call_id: "call_consult_2",
      },
    ];

    const tree = buildAdvisorTree(events);
    expect(tree.topLevelToolCalls).toHaveLength(1);
    expect(tree.topLevelToolCalls[0].nestedAdvisors).toHaveLength(1);
    expect(tree.topLevelToolCalls[0].nestedAdvisors[0].advisorId).toBe("advisor_coding_2");
    expect(tree.advisors).toHaveLength(0);
  });

  it("correlates advisor-internal tool calls using tool_call_id", () => {
    const events: ChatEvent[] = [
      {
        type: "advisor_start",
        advisor_id: "advisor_general_1",
        domain: "general",
        difficulty: "low",
        model: "openrouter/anthropic/claude-sonnet-4.6",
      },
      {
        type: "tool_call",
        name: "get_time",
        arguments: { format: "iso" },
        advisor_id: "advisor_general_1",
        tool_call_id: "call_time_1",
      },
      {
        type: "tool_result",
        name: "get_time",
        result: { time: "2026-04-19T12:00:00Z" },
        advisor_id: "advisor_general_1",
        tool_call_id: "call_time_1",
      },
      {
        type: "advisor_end",
        advisor_id: "advisor_general_1",
        status: "completed",
        latency_ms: 200,
        tokens_in: 50,
        tokens_out: 30,
      },
    ];

    const tree = buildAdvisorTree(events);
    expect(tree.advisors).toHaveLength(1);

    const advisor = tree.advisors[0];
    expect(advisor.toolCalls).toHaveLength(1);
    expect(advisor.toolCalls[0].name).toBe("get_time");
    expect(advisor.toolCalls[0].tool_call_id).toBe("call_time_1");
    expect(advisor.toolCalls[0].result).toBeDefined();
    expect(advisor.toolCalls[0].result?.result).toEqual({ time: "2026-04-19T12:00:00Z" });
  });

  it("handles advisor with error status", () => {
    const events: ChatEvent[] = [
      {
        type: "advisor_start",
        advisor_id: "advisor_failed_1",
        domain: "graphics",
        difficulty: "high",
        model: "openrouter/anthropic/claude-opus-4.7",
      },
      {
        type: "advisor_end",
        advisor_id: "advisor_failed_1",
        status: "error",
        error: "Model timeout after 30s",
        latency_ms: 30000,
      },
    ];

    const tree = buildAdvisorTree(events);
    expect(tree.advisors).toHaveLength(1);
    expect(tree.advisors[0].status).toBe("error");
    expect(tree.advisors[0].error).toBe("Model timeout after 30s");
  });

  it("collects orphan events for events without matching advisor", () => {
    const events: ChatEvent[] = [
      {
        type: "advisor_text_delta",
        advisor_id: "orphan_advisor_1",
        content: "Some text without start event",
      },
    ];

    const tree = buildAdvisorTree(events);
    expect(tree.advisors).toHaveLength(0);
    expect(tree.orphanEvents).toHaveLength(1);
  });

  it("associates thinking events with their advisor", () => {
    const events: ChatEvent[] = [
      {
        type: "advisor_start",
        advisor_id: "advisor_coding_1",
        domain: "coding",
        difficulty: "high",
        model: "openrouter/anthropic/claude-opus-4.7",
      },
      {
        type: "thinking",
        content: "Analyzing the request...",
        advisor_id: "advisor_coding_1",
      },
      {
        type: "thinking",
        content: "Formulating response...",
        advisor_id: "advisor_coding_1",
      },
      {
        type: "advisor_end",
        advisor_id: "advisor_coding_1",
        status: "completed",
      },
    ];

    const tree = buildAdvisorTree(events);
    expect(tree.advisors[0].thinking).toHaveLength(2);
    expect(tree.advisors[0].thinking[0].content).toBe("Analyzing the request...");
    expect(tree.advisors[0].thinking[1].content).toBe("Formulating response...");
  });

  it("concatenates text deltas when no text_done present", () => {
    const events: ChatEvent[] = [
      {
        type: "advisor_start",
        advisor_id: "advisor_partial_1",
        domain: "general",
        difficulty: "mid",
        model: "openrouter/anthropic/claude-sonnet-4.6",
      },
      {
        type: "advisor_text_delta",
        advisor_id: "advisor_partial_1",
        content: "First part. ",
      },
      {
        type: "advisor_text_delta",
        advisor_id: "advisor_partial_1",
        content: "Second part.",
      },
      {
        type: "advisor_end",
        advisor_id: "advisor_partial_1",
        status: "completed",
      },
    ];

    const tree = buildAdvisorTree(events);
    expect(tree.advisors[0].textDeltas).toEqual(["First part. ", "Second part."]);
    expect(getAdvisorTextContent(tree.advisors[0])).toBe("First part. Second part.");
  });
});

describe("normalizePersistedAdvisorTraces - historical replay normalization", () => {
  it("returns empty tree for null traces", () => {
    const tree = normalizePersistedAdvisorTraces(null);
    expect(tree.advisors).toHaveLength(0);
    expect(tree.topLevelToolCalls).toHaveLength(0);
  });

  it("returns empty tree for undefined traces", () => {
    const tree = normalizePersistedAdvisorTraces(undefined);
    expect(tree.advisors).toHaveLength(0);
    expect(tree.topLevelToolCalls).toHaveLength(0);
  });

  it("hydrates accumulator-shaped backend traces into tree with consult_advisor wrapper", () => {
    // This is the actual shape stored by orchestrator/daemon.py
    const persistedTraces = {
      advisor_coding_1: buildRealPersistedAdvisorTrace(),
    };

    const tree = normalizePersistedAdvisorTraces(persistedTraces);
    
    // Advisor should be nested under top-level consult_advisor tool call, not root advisor
    expect(tree.topLevelToolCalls).toHaveLength(1);
    expect(tree.advisors).toHaveLength(0);

    // Top-level tool call should be consult_advisor
    const consultToolCall = tree.topLevelToolCalls[0];
    expect(consultToolCall.name).toBe("consult_advisor");
    expect(consultToolCall.tool_call_id).toBe("call_consult_1");
    expect(consultToolCall.arguments).toEqual({ domain: "coding", difficulty: "high" });

    // Advisor should be nested under the consult_advisor tool call
    expect(consultToolCall.nestedAdvisors).toHaveLength(1);
    const advisor = consultToolCall.nestedAdvisors[0];
    expect(advisor.advisorId).toBe("advisor_coding_1");
    expect(advisor.domain).toBe("coding");
    expect(advisor.difficulty).toBe("high");
    expect(advisor.model).toBe("openrouter/anthropic/claude-opus-4.7");
    expect(advisor.status).toBe("completed");

    // Text deltas hydrated from text_parts
    expect(advisor.textDeltas).toEqual(["Review ", "the ", "auth ", "boundary."]);
    expect(advisor.textDone).toBe("Review the auth boundary.");

    // Thinking hydrated from reasoning_parts
    expect(advisor.thinking).toHaveLength(2);
    expect(advisor.thinking[0].content).toBe("Analyzing...");
    expect(advisor.thinking[1].content).toBe("Done");

    // Tool calls/results hydrated
    expect(advisor.toolCalls).toHaveLength(1);
    expect(advisor.toolCalls[0].name).toBe("get_time");
    expect(advisor.toolCalls[0].tool_call_id).toBe("call_1");
    expect(advisor.toolCalls[0].result?.result).toEqual({ time: "2026-04-19T12:00:00Z" });

    // Usage hydrated
    expect(advisor.usage?.tokensIn).toBe(150);
    expect(advisor.usage?.tokensOut).toBe(75);
    expect(advisor.usage?.latencyMs).toBe(2500);

    // Parent trace key propagated
    expect(advisor.startEvent.parent_trace_key).toBe("req_test:assistant");
    expect(advisor.startEvent.tool_call_id).toBe("call_consult_1");
  });

  it("handles advisor with errors in accumulator shape", () => {
    const persistedTraces = {
      advisor_failed_1: {
        advisor_id: "advisor_failed_1",
        domain: "graphics",
        difficulty: "high",
        model: "openrouter/anthropic/claude-opus-4.7",
        text_parts: [],
        reasoning_parts: [],
        tool_calls: [],
        tool_results: [],
        errors: ["Model timeout", "Rate limit exceeded"],
        usage: { tokens_in: 0, tokens_out: 0, latency_ms: 30000 },
        trace_key: null,
        parent_trace_key: null,
        event_tags: {},
        status: "error",
      },
    };

    const tree = normalizePersistedAdvisorTraces(persistedTraces);
    expect(tree.advisors).toHaveLength(1);
    expect(tree.advisors[0].status).toBe("error");
    expect(tree.advisors[0].error).toBe("Model timeout");
  });

  it("handles minimal accumulator with defaults", () => {
    const persistedTraces = {
      advisor_minimal_1: {
        advisor_id: "advisor_minimal_1",
        // No domain/difficulty/model provided - should use defaults
        text_parts: ["Hello"],
        reasoning_parts: [],
        tool_calls: [],
        tool_results: [],
        errors: [],
        usage: null,
      },
    };

    const tree = normalizePersistedAdvisorTraces(persistedTraces);
    expect(tree.advisors).toHaveLength(1);
    expect(tree.advisors[0].domain).toBe("general");
    expect(tree.advisors[0].difficulty).toBe("mid");
    expect(tree.advisors[0].model).toBe("unknown");
    expect(tree.advisors[0].textDone).toBe("Hello");
  });

  it("normalizes multiple accumulator traces into unified tree", () => {
    const persistedTraces = {
      advisor_research_1: {
        advisor_id: "advisor_research_1",
        domain: "research",
        difficulty: "mid",
        text_parts: ["Found sources."],
        tool_calls: [],
        tool_results: [],
        errors: [],
        usage: { tokens_in: 50, tokens_out: 25, latency_ms: 1000 },
        status: "completed",
      },
      advisor_coding_1: {
        advisor_id: "advisor_coding_1",
        domain: "coding",
        difficulty: "high",
        text_parts: ["Use function X."],
        tool_calls: [],
        tool_results: [],
        errors: [],
        usage: null,
        status: "completed",
      },
    };

    const tree = normalizePersistedAdvisorTraces(persistedTraces);
    expect(tree.advisors).toHaveLength(2);
    const advisorIds = tree.advisors.map(a => a.advisorId).sort();
    expect(advisorIds).toEqual(["advisor_coding_1", "advisor_research_1"]);
  });
});

describe("mergeAdvisorTrees - combining live and persisted trees", () => {
  it("merges trees without duplication", () => {
    const persisted: AdvisorTreeRoot = {
      topLevelToolCalls: [],
      advisors: [
        {
          type: "advisor",
          advisorId: "advisor_1",
          domain: "coding",
          difficulty: "high",
          model: "model-1",
          status: "completed",
          startEvent: { type: "advisor_start", advisor_id: "advisor_1", domain: "coding", difficulty: "high", model: "model-1" },
          textDeltas: ["Old text"],
          toolCalls: [],
          thinking: [],
        },
      ],
      orphanEvents: [],
      flatEvents: [{ type: "advisor_start", advisor_id: "advisor_1", domain: "coding", difficulty: "high", model: "model-1" }],
    };

    const live: AdvisorTreeRoot = {
      topLevelToolCalls: [],
      advisors: [
        {
          type: "advisor",
          advisorId: "advisor_1",
          domain: "coding",
          difficulty: "high",
          model: "model-1",
          status: "completed",
          startEvent: { type: "advisor_start", advisor_id: "advisor_1", domain: "coding", difficulty: "high", model: "model-1" },
          textDeltas: ["Old text"],
          toolCalls: [],
          thinking: [],
        },
      ],
      orphanEvents: [],
      flatEvents: [{ type: "advisor_start", advisor_id: "advisor_1", domain: "coding", difficulty: "high", model: "model-1" }],
    };

    const merged = mergeAdvisorTrees(persisted, live);
    expect(merged.advisors).toHaveLength(1);
    expect(merged.flatEvents).toHaveLength(1);
  });

  it("combines distinct advisors from both trees", () => {
    const persisted: AdvisorTreeRoot = {
      topLevelToolCalls: [],
      advisors: [
        {
          type: "advisor",
          advisorId: "advisor_1",
          domain: "coding",
          difficulty: "high",
          model: "model-1",
          status: "completed",
          startEvent: { type: "advisor_start", advisor_id: "advisor_1", domain: "coding", difficulty: "high", model: "model-1" },
          textDeltas: ["Text 1"],
          toolCalls: [],
          thinking: [],
        },
      ],
      orphanEvents: [],
      flatEvents: [{ type: "advisor_start", advisor_id: "advisor_1", domain: "coding", difficulty: "high", model: "model-1" }],
    };

    const live: AdvisorTreeRoot = {
      topLevelToolCalls: [],
      advisors: [
        {
          type: "advisor",
          advisorId: "advisor_2",
          domain: "research",
          difficulty: "mid",
          model: "model-2",
          status: "running",
          startEvent: { type: "advisor_start", advisor_id: "advisor_2", domain: "research", difficulty: "mid", model: "model-2" },
          textDeltas: ["Text 2"],
          toolCalls: [],
          thinking: [],
        },
      ],
      orphanEvents: [],
      flatEvents: [{ type: "advisor_start", advisor_id: "advisor_2", domain: "research", difficulty: "mid", model: "model-2" }],
    };

    const merged = mergeAdvisorTrees(persisted, live);
    expect(merged.advisors).toHaveLength(2);
    const ids = merged.advisors.map(a => a.advisorId).sort();
    expect(ids).toEqual(["advisor_1", "advisor_2"]);
  });

  it("preserves a single advisor lifecycle and text events across persisted-live merge", () => {
    const persisted = normalizePersistedAdvisorTraces({
      advisor_1: {
        ...buildRealPersistedAdvisorTrace({
          advisor_id: "advisor_1",
          model: "model-1",
          text_parts: ["Persisted text."],
          reasoning_parts: [],
          tool_calls: [],
          tool_results: [],
          usage: {
            prompt_tokens: 11,
            completion_tokens: 7,
            total_tokens: 18,
            tokens_in: 11,
            tokens_out: 7,
            latency_ms: 900,
          },
          tokens_in: 11,
          tokens_out: 7,
          latency_ms: 900,
        }),
      },
    });

    const live = buildAdvisorTree([
      {
        type: "advisor_start",
        advisor_id: "advisor_1",
        domain: "coding",
        difficulty: "high",
        model: "model-1",
        tool_call_id: "call_consult_1",
      },
      {
        type: "advisor_text_delta",
        advisor_id: "advisor_1",
        content: "Persisted text.",
        tool_call_id: "call_consult_1",
      },
      {
        type: "advisor_text_done",
        advisor_id: "advisor_1",
        content: "Persisted text.",
        tool_call_id: "call_consult_1",
      },
      {
        type: "advisor_end",
        advisor_id: "advisor_1",
        status: "completed",
        tokens_in: 11,
        tokens_out: 7,
        latency_ms: 900,
        tool_call_id: "call_consult_1",
      },
    ]);

    const merged = mergeAdvisorTrees(persisted, live);
    // Advisor should be nested under consult_advisor wrapper (synthetic from persisted)
    expect(merged.topLevelToolCalls).toHaveLength(1);
    expect(merged.topLevelToolCalls[0].name).toBe("consult_advisor");
    expect(merged.topLevelToolCalls[0].nestedAdvisors).toHaveLength(1);

    const advisor = merged.topLevelToolCalls[0].nestedAdvisors[0];
    expect(advisor.textDeltas).toEqual(["Persisted text."]);
    expect(advisor.textDone).toBe("Persisted text.");
    expect(advisor.endEvent?.tokens_in).toBe(11);
    expect(advisor.endEvent?.tokens_out).toBe(7);

    const eventTypes = merged.flatEvents.map((event) => event.type);
    expect(eventTypes.filter((type) => type === "advisor_start")).toHaveLength(1);
    expect(eventTypes.filter((type) => type === "advisor_text_delta")).toHaveLength(1);
    expect(eventTypes.filter((type) => type === "advisor_text_done")).toHaveLength(1);
    expect(eventTypes.filter((type) => type === "advisor_end")).toHaveLength(1);
  });

  it("preserves repeated identical advisor chunks across persisted-live merge", () => {
    const persisted = normalizePersistedAdvisorTraces({
      advisor_1: {
        ...buildRealPersistedAdvisorTrace({
          advisor_id: "advisor_1",
          text_parts: ["same", "same"],
          reasoning_parts: ["loop", "loop"],
          tool_calls: [],
          tool_results: [],
        }),
      },
    });

    const live = buildAdvisorTree([
      {
        type: "advisor_start",
        advisor_id: "advisor_1",
        domain: "coding",
        difficulty: "high",
        model: "openrouter/anthropic/claude-opus-4.7",
        tool_call_id: "call_consult_1",
      },
      { type: "thinking", advisor_id: "advisor_1", content: "loop" },
      { type: "thinking", advisor_id: "advisor_1", content: "loop" },
      { type: "advisor_text_delta", advisor_id: "advisor_1", content: "same", tool_call_id: "call_consult_1" },
      { type: "advisor_text_delta", advisor_id: "advisor_1", content: "same", tool_call_id: "call_consult_1" },
      { type: "advisor_text_done", advisor_id: "advisor_1", content: "samesame", tool_call_id: "call_consult_1" },
      { type: "advisor_end", advisor_id: "advisor_1", status: "completed", tool_call_id: "call_consult_1" },
    ]);

    const merged = mergeAdvisorTrees(persisted, live);
    // Advisor should be nested under consult_advisor wrapper (synthetic from persisted)
    expect(merged.topLevelToolCalls).toHaveLength(1);
    expect(merged.topLevelToolCalls[0].nestedAdvisors).toHaveLength(1);
    const advisor = merged.topLevelToolCalls[0].nestedAdvisors[0];
    expect(advisor.thinking.map((event) => event.content)).toEqual(["loop", "loop"]);
    expect(advisor.textDeltas).toEqual(["same", "same"]);
    expect(advisor.textDone).toBe("samesame");
  });
});
