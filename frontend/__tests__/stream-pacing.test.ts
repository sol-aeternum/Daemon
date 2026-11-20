import { describe, expect, it } from 'vitest';
import { DEFAULT_CHUNKS_PER_TASK, paceChunkStream } from '../lib/streamPacing';

async function collect<T>(stream: ReadableStream<T>): Promise<T[]> {
  const values: T[] = [];
  const reader = stream.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return values;
    values.push(value);
  }
}

function burst(count: number): ReadableStream<number> {
  return new ReadableStream<number>({
    start(controller) {
      for (let index = 0; index < count; index += 1) {
        controller.enqueue(index);
      }
      controller.close();
    },
  });
}

describe('paceChunkStream', () => {
  it('forwards every chunk in order and yields once per budget', async () => {
    let yields = 0;
    const values = await collect(
      paceChunkStream(burst(29), {
        chunksPerTask: 4,
        yieldTask: async () => {
          yields += 1;
        },
      }),
    );

    expect(values).toEqual(Array.from({ length: 29 }, (_, index) => index));
    expect(yields).toBe(Math.floor(29 / 4));
  });

  it('uses the exported default budget', async () => {
    let yields = 0;
    const values = await collect(
      paceChunkStream(burst(DEFAULT_CHUNKS_PER_TASK * 3), {
        yieldTask: async () => {
          yields += 1;
        },
      }),
    );

    expect(values).toHaveLength(DEFAULT_CHUNKS_PER_TASK * 3);
    expect(yields).toBe(3);
  });

  it('withholds the next chunk until the task yields', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const reader = paceChunkStream(burst(5), {
      chunksPerTask: 2,
      yieldTask: () => gate,
    }).getReader();

    await expect(reader.read()).resolves.toEqual({ done: false, value: 0 });
    await expect(reader.read()).resolves.toEqual({ done: false, value: 1 });
    let settled = false;
    const third = reader.read().then((result) => {
      settled = true;
      return result;
    });
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(settled).toBe(false);

    release();
    await expect(third).resolves.toEqual({ done: false, value: 2 });
  });

  it('propagates cancellation to the source stream', async () => {
    let cancelled: unknown;
    const source = new ReadableStream<number>({
      start(controller) {
        controller.enqueue(1);
      },
      cancel(reason) {
        cancelled = reason;
      },
    });
    const reader = paceChunkStream(source, {
      chunksPerTask: 2,
      yieldTask: async () => {},
    }).getReader();

    await expect(reader.read()).resolves.toEqual({ done: false, value: 1 });
    await reader.cancel('stop');
    expect(cancelled).toBe('stop');
  });

  it('propagates a source error to the paced reader', async () => {
    let reads = 0;
    const source = new ReadableStream<number>({
      pull(controller) {
        if (reads === 0) {
          reads += 1;
          controller.enqueue(1);
          return;
        }
        throw new Error('stream failed');
      },
    });
    const reader = paceChunkStream(source, {
      chunksPerTask: 4,
      yieldTask: async () => {},
    }).getReader();

    await expect(reader.read()).resolves.toEqual({ done: false, value: 1 });
    await expect(reader.read()).rejects.toThrow('stream failed');
  });
});
