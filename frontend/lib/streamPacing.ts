/**
 * Pacing for parsed chat UI-message chunks (#752).
 *
 * A transport delivers parsed chunks as soon as the response body yields
 * them. Text and thinking deltas synchronously notify React through the AI
 * SDK's external store. A backlog can schedule enough updates in one browser
 * task to trip React's nested update limit (#185).
 *
 * This forwards at most `chunksPerTask` chunks and then yields the task,
 * keeping the SDK's store notifications synchronous while bounding the
 * main-thread work one burst can force before the browser can paint or handle
 * input. Chunks are never dropped, reordered or rewritten: this is scheduling
 * only. Cancellation and source errors propagate through `pipeThrough`.
 */
export const DEFAULT_CHUNKS_PER_TASK = 8;

export interface StreamPacingOptions {
  /** Chunks forwarded before yielding the task; at least 1. */
  chunksPerTask?: number;
  /** Yields the current browser task. Injected by tests. */
  yieldTask?: () => Promise<void>;
}

const yieldToTask = () =>
  new Promise<void>((resolve) => {
    setTimeout(resolve, 0);
  });

export function paceChunkStream<T>(
  source: ReadableStream<T>,
  options: StreamPacingOptions = {},
): ReadableStream<T> {
  const chunksPerTask = Math.max(
    1,
    Math.floor(options.chunksPerTask ?? DEFAULT_CHUNKS_PER_TASK),
  );
  const yieldTask = options.yieldTask ?? yieldToTask;
  let forwardedInTask = 0;
  return source.pipeThrough(
    new TransformStream<T, T>({
      async transform(chunk, controller) {
        controller.enqueue(chunk);
        forwardedInTask += 1;
        if (forwardedInTask >= chunksPerTask) {
          forwardedInTask = 0;
          await yieldTask();
        }
      },
    }),
  );
}
