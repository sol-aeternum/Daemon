import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useStopGeneration } from "../hooks/useStopGeneration";

type Message = { id: string; role: string; content: string };

function Harness({
  messages,
  stop,
  archiveEvents,
}: {
  messages: Message[];
  stop: () => void;
  archiveEvents: (messageId: string) => void;
}) {
  const { stoppedMessageIds, stopGeneration } = useStopGeneration({
    messages,
    stop,
    archiveEvents,
  });

  return (
    <>
      {messages.map((message) => (
        <div key={message.id}>
          <span>{message.content}</span>
          {stoppedMessageIds.has(message.id) && <span>(stopped)</span>}
        </div>
      ))}
      <button type="button" onClick={stopGeneration}>Stop</button>
    </>
  );
}

describe("useStopGeneration", () => {
  it("invokes stop and preserves and labels the latest partial assistant message", () => {
    const stop = vi.fn();
    const archiveEvents = vi.fn();
    render(
      <Harness
        messages={[{ id: "partial", role: "assistant", content: "Partial answer" }]}
        stop={stop}
        archiveEvents={archiveEvents}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));

    expect(stop).toHaveBeenCalledTimes(1);
    expect(archiveEvents).toHaveBeenCalledWith("partial");
    expect(screen.getByText("Partial answer")).not.toBeNull();
    expect(screen.getByText("(stopped)")).not.toBeNull();
  });

  it("does not mark an older assistant message when the latest message is from the user", () => {
    const stop = vi.fn();
    const archiveEvents = vi.fn();
    render(
      <Harness
        messages={[
          { id: "old", role: "assistant", content: "Complete answer" },
          { id: "user", role: "user", content: "New prompt" },
        ]}
        stop={stop}
        archiveEvents={archiveEvents}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));

    expect(stop).toHaveBeenCalledTimes(1);
    expect(archiveEvents).not.toHaveBeenCalled();
    expect(screen.queryByText("(stopped)")).toBeNull();
  });
});
