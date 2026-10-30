// @vitest-environment node
import { Writable } from 'node:stream';
import { expect, it } from 'vitest';
import { pipeFixtureResponse } from '../../tests/resource_fixture_stream.mjs';

it.each([
  ['AbortError', true, true, true],
  ['AbortError', false, true, false],
  ['AbortError', true, false, false],
  ['AbortError', false, false, false],
  ['Error', true, true, false],
] as const)(
  'classifies stream %s with aborted=%s destroyed=%s',
  async (name, aborted, destroyed, accepted) => {
    let upstream!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        upstream = controller;
      },
    });
    const response = new Writable({
      write(_chunk, _encoding, done) {
        done();
      },
    });
    const lifetime = new AbortController();
    if (destroyed) response.destroy();
    if (aborted) lifetime.abort();
    const failures: { name: string; message: string }[] = [];
    const cancellations: string[] = [];
    const source = pipeFixtureResponse(
      body,
      response,
      lifetime.signal,
      failures,
      cancellations,
    );
    const closed = new Promise<void>((resolve) =>
      source.once('close', resolve),
    );
    const error = new Error('synthetic upstream stream failure');
    error.name = name;
    upstream.error(error);
    await closed;
    expect(failures).toEqual(
      accepted ? [] : [{ name, message: error.message }],
    );
    expect(cancellations).toEqual(accepted ? ['client_disconnect'] : []);
    expect(response.destroyed).toBe(true);
  },
);

it('pipes a normal response without reporting a cancellation or failure', async () => {
  const chunks: Buffer[] = [];
  const response = new Writable({
    write(chunk, _encoding, done) {
      chunks.push(chunk);
      done();
    },
  });
  const finished = new Promise<void>((resolve) =>
    response.once('finish', resolve),
  );
  const failures: { name: string; message: string }[] = [];
  const cancellations: string[] = [];
  pipeFixtureResponse(
    new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('synthetic bytes'));
        controller.close();
      },
    }),
    response,
    new AbortController().signal,
    failures,
    cancellations,
  );
  await finished;
  expect(Buffer.concat(chunks).toString()).toBe('synthetic bytes');
  expect(failures).toEqual([]);
  expect(cancellations).toEqual([]);
});
