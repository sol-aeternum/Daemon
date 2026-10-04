import { describe, expect, it, vi } from 'vitest';
import {
  SpeechStreamParser,
  encodeSpeechStreamAudioFrame as audio,
  encodeSpeechStreamControlFrame as control,
  encodeSpeechStreamFrame as frame,
  encodeSpeechStreamMagic as magic,
  isStrictSpeechStreamContentType,
  MAX_SPEECH_PARSER_PENDING_BYTES,
  type SpeechStreamExpectation,
} from '../lib/speechStreamProtocol';

const expectation: SpeechStreamExpectation = {
  provider: 'kokoro',
  model: 'fictional-model',
  voice: 'daemon-default',
  speed: 1.25,
  format: 'mp3',
  mime: 'audio/mpeg',
  sample_rate: 24000,
  rendering: 'speech-mp3-progressive-v1',
};
const meta = {
  ...expectation,
  version: 1,
  stream_id: 'a'.repeat(32),
  cached: false,
};
const complete = {
  frames: 2,
  bytes: 5,
  source_seconds: 1,
  encoded_seconds: 1.048,
  synthesis_seconds: 0.2,
  audio_path: null,
  cache_available: false,
};

function concat(...chunks: Uint8Array[]) {
  const bytes = new Uint8Array(
    chunks.reduce((size, chunk) => size + chunk.length, 0),
  );
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return bytes;
}
function clip(terminal: unknown = complete) {
  return concat(
    magic(),
    control(0, meta),
    audio(0, new Uint8Array([1, 2])),
    audio(1, new Uint8Array([3, 4, 5])),
    control(2, terminal),
  );
}
function setup(limits: Partial<SpeechStreamExpectation> = {}) {
  const handlers = {
    onMeta: vi.fn(),
    onAudio: vi.fn(),
    onHeartbeat: vi.fn(),
    onComplete: vi.fn(),
    onError: vi.fn(),
  };
  return {
    parser: new SpeechStreamParser({ ...expectation, ...limits }, handlers),
    handlers,
  };
}

describe('progressive speech protocol', () => {
  it('requires versioned MIME, not unversioned or unsupported parameters', () => {
    expect(
      isStrictSpeechStreamContentType(
        'Application/Vnd.Daemon.Speech-Stream;version=1',
      ),
    ).toBe(true);
    for (const value of [
      null,
      'audio/mpeg',
      'application/vnd.daemon.speech-stream',
      'application/vnd.daemon.speech-stream;version=2',
      'application/vnd.daemon.speech-stream;version=1;version=1',
    ]) {
      expect(isStrictSpeechStreamContentType(value)).toBe(false);
    }
  });

  it.each([1, 2, 3, 7, 256, 8192])(
    'accepts arbitrary splits of %i bytes, success only after clean EOF',
    async (size) => {
      const { parser, handlers } = setup();
      const bytes = clip();
      for (let offset = 0; offset < bytes.length; offset += size) {
        await parser.push(bytes.subarray(offset, offset + size));
        expect(parser.pendingBytes).toBeLessThanOrEqual(
          MAX_SPEECH_PARSER_PENDING_BYTES,
        );
      }
      expect(handlers.onComplete).not.toHaveBeenCalled();
      await parser.end();
      expect(handlers.onComplete).toHaveBeenCalledExactlyOnceWith(complete);
      expect(parser.pendingBytes).toBe(0);
      expect(parser.audioByteCount).toBe(5);
    },
  );

  it('fuzzes deterministic mixed chunk sizes without modifying input', async () => {
    const bytes = clip(),
      original = bytes.slice();
    for (let seed = 1; seed < 10; seed++) {
      const { parser } = setup();
      let offset = 0,
        random = seed;
      while (offset < bytes.length) {
        random = (random * 1664525 + 1013904223) >>> 0;
        const size = 1 + (random % 400);
        await parser.push(bytes.subarray(offset, offset + size));
        offset += size;
      }
      await parser.end();
    }
    expect(bytes).toEqual(original);
  });

  it('compacts a partial prefix followed by a maximum-size frame safely', async () => {
    const { parser } = setup();
    const large = new Uint8Array(65532).fill(17);
    const bytes = concat(
      magic(),
      control(0, meta),
      audio(0, large),
      audio(1, large),
      control(2, { ...complete, bytes: large.length * 2 }),
    );
    await parser.push(bytes.subarray(0, 350));
    await parser.push(bytes.subarray(350));
    await parser.end();
    expect(parser.audioByteCount).toBe(131064);
  });

  it('accepts coalesced heartbeats irrespective of arrival time, enforcing count', async () => {
    for (const count of [32, 33]) {
      const { parser, handlers } = setup();
      const beats = Array.from({ length: count }, () =>
        frame(4, new Uint8Array()),
      );
      const bytes = concat(
        magic(),
        control(0, meta),
        ...beats,
        audio(0, new Uint8Array([1, 2])),
        audio(1, new Uint8Array([3, 4, 5])),
        control(2, complete),
      );
      if (count === 32) {
        await parser.push(bytes);
        await parser.end();
      } else {
        await expect(parser.push(bytes)).rejects.toThrow('heartbeats_exceeded');
      }
      expect(handlers.onHeartbeat).toHaveBeenCalledTimes(32);
    }
  });

  it('awaits onAudio and rejects concurrent inputs instead of retaining an unbounded queue', async () => {
    let resolve!: () => void;
    const wait = new Promise<void>((done) => {
      resolve = done;
    });
    const handlers = {
      onMeta: vi.fn(),
      onAudio: vi.fn(() => wait),
      onHeartbeat: vi.fn(),
      onComplete: vi.fn(),
      onError: vi.fn(),
    };
    const parser = new SpeechStreamParser(expectation, handlers);
    const pending = parser.push(clip());
    await vi.waitFor(() => expect(handlers.onAudio).toHaveBeenCalled());
    await expect(parser.push(new Uint8Array([1]))).rejects.toThrow(
      'input_in_flight',
    );
    await expect(parser.end()).rejects.toThrow('input_in_flight');
    resolve();
    await pending;
    await parser.end();
  });

  it.each([
    ['frames', 1],
    ['bytes', 4],
    ['source_seconds', 301],
    ['encoded_seconds', 1.151],
    ['synthesis_seconds', -1],
    ['cache_available', true],
    ['audio_path', 'https://example.invalid/audio?token=forbidden'],
    ['frames', true],
    ['source_seconds', 'NaN'],
  ])('rejects invalid complete %s=%s', async (key, value) => {
    const { parser } = setup();
    await expect(
      parser.push(clip({ ...complete, [key]: value })),
    ).rejects.toThrow();
  });

  it.each([
    ['version', 2],
    ['provider', 'other'],
    ['model', 'other'],
    ['voice', 'rachel'],
    ['speed', 1],
    ['format', 'wav'],
    ['mime', 'audio/ogg'],
    ['sample_rate', 48000],
    ['rendering', 'other'],
    ['cached', 1],
  ])('rejects mismatched metadata %s', async (key, value) => {
    const { parser } = setup();
    await expect(
      parser.push(concat(magic(), control(0, { ...meta, [key]: value }))),
    ).rejects.toThrow();
  });

  it.each([
    concat(magic(), audio(0, new Uint8Array([1]))),
    concat(magic(), control(0, meta), control(0, meta)),
    concat(magic(), control(0, meta), audio(2, new Uint8Array([1]))),
    concat(clip(), frame(4, new Uint8Array())),
    concat(magic(), frame(5, new Uint8Array())),
    concat(magic(), control(0, meta), frame(4, new Uint8Array([1]))),
    concat(magic(), control(0, { ...meta, unexpected: 1 })),
  ])('rejects malformed frame order/type/fields', async (bytes) => {
    const { parser } = setup();
    await expect(parser.push(bytes)).rejects.toThrow();
  });

  it('rejects duplicate and escaped duplicate JSON keys', async () => {
    for (const key of ['version', '\\u0076ersion']) {
      const { parser } = setup();
      const text = JSON.stringify(meta).replace(
        '"version":1',
        `"version":1,"${key}":1`,
      );
      await expect(
        parser.push(concat(magic(), frame(0, new TextEncoder().encode(text)))),
      ).rejects.toThrow('json_duplicate_key');
    }
  });

  it('rejects invalid UTF8', async () => {
    const { parser } = setup();
    await expect(
      parser.push(concat(magic(), frame(0, new Uint8Array([255])))),
    ).rejects.toThrow('utf8_invalid');
  });

  it('rejects truncation and EOF without complete', async () => {
    const bytes = clip();
    for (const end of [0, 1, 4, 9, 50, bytes.length - 1]) {
      const { parser } = setup();
      await parser.push(bytes.subarray(0, end));
      await expect(parser.end()).rejects.toThrow('stream_truncated');
    }
  });

  it('cannot turn a typed provider failure into success via a returning onError handler', async () => {
    const { parser, handlers } = setup();
    await expect(
      parser.push(
        concat(
          magic(),
          control(0, meta),
          control(3, { code: 'speech_failed' }),
        ),
      ),
    ).rejects.toThrow('speech_failed');
    await expect(parser.end()).rejects.toThrow('speech_failed');
    expect(handlers.onError).toHaveBeenCalledOnce();
    expect(handlers.onComplete).not.toHaveBeenCalled();
  });

  it('rejects arbitrary/unsafe error codes', async () => {
    const { parser } = setup();
    await expect(
      parser.push(
        concat(magic(), control(0, meta), control(3, { code: 'toString' })),
      ),
    ).rejects.toThrow('code_invalid');
  });

  it('matches cached zero-synthesis and rejects zero totals immediately', async () => {
    const { parser } = setup();
    await expect(
      parser.push(
        concat(
          magic(),
          control(0, { ...meta, cached: true }),
          audio(0, new Uint8Array([1, 2])),
          audio(1, new Uint8Array([3, 4, 5])),
          control(2, complete),
        ),
      ),
    ).rejects.toThrow('cached_synthesis_seconds_invalid');
    const empty = setup().parser;
    await expect(
      empty.push(
        concat(
          magic(),
          control(0, meta),
          control(2, {
            ...complete,
            frames: 0,
            bytes: 0,
          }),
        ),
      ),
    ).rejects.toThrow('complete_without_audio');
  });

  it('lower bounds apply and supplied larger bounds never widen the profile', async () => {
    const { parser } = setup({ max_audio_bytes: 4 });
    await expect(parser.push(clip())).rejects.toThrow('audio_bytes_exceeded');
    const high = setup({
      max_source_seconds: 600,
      max_padding_seconds: 10,
      max_audio_frame_payload_bytes: 100000,
    }).parser;
    await expect(
      high.push(
        concat(magic(), control(0, meta), frame(1, new Uint8Array(65537))),
      ),
    ).rejects.toThrow('audio_frame_length_invalid');
    const duration = setup({
      max_source_seconds: 600,
      max_padding_seconds: 10,
    }).parser;
    await expect(
      duration.push(
        clip({ ...complete, source_seconds: 301, encoded_seconds: 301.048 }),
      ),
    ).rejects.toThrow('source_seconds_out_of_range');
  });
});
