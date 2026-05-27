import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ToolCallLog } from "../components/ToolCallBlock";
import type { ChatEvent } from "../lib/events";

describe("ToolCallLog", () => {
  it("keeps advisor-internal tool events out of the top-level tool log", () => {
    const events: ChatEvent[] = [
      {
        type: "tool_call",
        name: "consult_advisor",
        arguments: { domain: "coding", difficulty: "high" },
        tool_call_id: "call_consult_1",
      },
      {
        type: "tool_result",
        name: "consult_advisor",
        result: { advisor_id: "advisor_1" },
        tool_call_id: "call_consult_1",
      },
      {
        type: "tool_call",
        name: "get_time",
        arguments: { format: "iso" },
        advisor_id: "advisor_1",
        tool_call_id: "call_nested_1",
      },
      {
        type: "tool_result",
        name: "get_time",
        result: { time: "2026-04-20T00:00:00Z" },
        advisor_id: "advisor_1",
        tool_call_id: "call_nested_1",
      },
    ];

    render(React.createElement(ToolCallLog, { events }));

    expect(screen.queryByText("consult_advisor")).not.toBeNull();
    expect(screen.queryByText("get_time")).toBeNull();
    expect(screen.queryByText("Step 1")).not.toBeNull();
    expect(screen.queryByText("Step 2")).toBeNull();
  });

  it("matches top-level tool results by tool_call_id before tool name", () => {
    const events: ChatEvent[] = [
      {
        type: "tool_call",
        name: "web_search",
        arguments: { query: "first" },
        tool_call_id: "call_search_1",
      },
      {
        type: "tool_call",
        name: "web_search",
        arguments: { query: "second" },
        tool_call_id: "call_search_2",
      },
      {
        type: "tool_result",
        name: "web_search",
        result: { hits: ["second"] },
        tool_call_id: "call_search_2",
      },
    ];

    render(React.createElement(ToolCallLog, { events }));

    expect(screen.queryByText("Running web_search...")).not.toBeNull();
    const toolButtons = screen.getAllByRole("button");
    fireEvent.click(toolButtons[0]);

    expect(screen.queryByText(/"query": "second"/)).not.toBeNull();
    expect(screen.queryByText(/"hits": \[/)).not.toBeNull();
  });
});
