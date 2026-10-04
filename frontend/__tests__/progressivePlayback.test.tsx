import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  AudioPlaybackProvider,
  useAudioPlayback,
} from '../components/AudioPlaybackProvider';
import { TtsPlaybackBar } from '../components/TtsPlaybackBar';
import {
  ProgressivePlayback,
  availableSpeechRanges,
  clampSpeechSeek,
} from '../lib/progressivePlayback';
import { type SpeechCapabilities } from '../lib/speechCapabilities';
import {
  encodeSpeechStreamMagic as magic,
  encodeSpeechStreamControlFrame as control,
  encodeSpeechStreamAudioFrame as audioFrame,
} from '../lib/speechStreamProtocol';

const auth = vi.hoisted(() => ({
  generation: 0,
  listeners: new Set<() => void>(),
}));
vi.mock('../lib/auth', () => ({
  ensureAuthHeader: vi.fn(async () => 'Bearer fictional'),
  getAuthGeneration: () => auth.generation,
  subscribeAuthGeneration: (listener: () => void) => {
    auth.listeners.add(listener);
    return () => auth.listeners.delete(listener);
  },
}));

const caps: SpeechCapabilities = {
  ready: true,
  provider: 'kokoro',
  model: 'fixture',
  voices: ['daemon-default'],
  formats: ['mp3', 'opus', 'wav'],
  speed_min: 0.5,
  speed_max: 2,
  streams: [
    {
      version: 1,
      format: 'mp3',
      mime: 'audio/mpeg',
      sample_rate: 24000,
      rendering: 'speech-mp3-progressive-v1',
    },
  ],
  limits: {
    max_text_code_points: 3000,
    max_request_bytes: 32768,
    max_audio_bytes: 16_000_000,
    max_source_seconds: 300,
    max_padding_seconds: 0.15,
    max_wire_bytes: 20_000_000,
    max_audio_frames: 16384,
    max_heartbeats: 32,
    max_audio_frame_payload: 65536,
    max_control_bytes: 4096,
    queue_bytes: 262144,
    queue_frames: 64,
    heartbeat_seconds: 5,
    idle_seconds: 15,
    backpressure_seconds: 10,
    deadline_seconds: 125,
  },
};
const meta = {
  version: 1,
  stream_id: 'a'.repeat(32),
  provider: 'kokoro',
  model: 'fixture',
  voice: 'daemon-default',
  speed: 1,
  format: 'mp3',
  mime: 'audio/mpeg',
  sample_rate: 24000,
  rendering: 'speech-mp3-progressive-v1',
  cached: false,
};
const complete = {
  frames: 1,
  bytes: 3,
  source_seconds: 1,
  encoded_seconds: 1.048,
  synthesis_seconds: 0.1,
  audio_path: null,
  cache_available: false,
};
function concat(...parts: Uint8Array[]) {
  const bytes = new Uint8Array(
    parts.reduce((size, part) => size + part.length, 0),
  );
  let at = 0;
  for (const part of parts) {
    bytes.set(part, at);
    at += part.length;
  }
  return bytes;
}
const prefix = () =>
  concat(magic(), control(0, meta), audioFrame(0, new Uint8Array([1, 2, 3])));
const clips: HTMLAudioElement[] = [];
const sources: FakeMediaSource[] = [];
let appendAuto = true,
  unsupported = false,
  refuseBuffer = false;

function ranges(values: [number, number][]): TimeRanges {
  return {
    length: values.length,
    start: (n) => values[n][0],
    end: (n) => values[n][1],
  };
}
class FakeBuffer extends EventTarget {
  updating = false;
  abortCalls = 0;
  appendBuffer() {
    this.updating = true;
    if (appendAuto) queueMicrotask(() => this.finish());
  }
  finish() {
    this.updating = false;
    this.dispatchEvent(new Event('updateend'));
  }
  abort() {
    this.abortCalls++;
    this.updating = false;
    this.dispatchEvent(new Event('abort'));
  }
}
class FakeMediaSource extends EventTarget {
  static isTypeSupported() {
    return !unsupported;
  }
  readyState = 'closed';
  buffer = new FakeBuffer();
  eos = 0;
  removed = 0;
  constructor() {
    super();
    sources.push(this);
    queueMicrotask(() => {
      this.readyState = 'open';
      this.dispatchEvent(new Event('sourceopen'));
    });
  }
  addSourceBuffer() {
    if (refuseBuffer) throw new Error('fixture local refusal');
    return this.buffer;
  }
  removeSourceBuffer() {
    this.removed++;
  }
  endOfStream() {
    this.eos++;
    this.readyState = 'ended';
    for (const audio of clips) {
      Object.defineProperty(audio, 'duration', {
        value: 1.048,
        configurable: true,
        writable: true,
      });
      audio.dispatchEvent(new Event('durationchange'));
    }
  }
}

function makeAudio(): HTMLAudioElement {
  const audio = document.createElement('audio');
  clips.push(audio);
  Object.defineProperties(audio, {
    paused: { value: true, writable: true, configurable: true },
    duration: { value: Infinity, writable: true, configurable: true },
    ended: { value: false, writable: true, configurable: true },
    buffered: { value: ranges([[0, 0.8]]), configurable: true },
    seekable: { value: ranges([[0, 0.8]]), configurable: true },
  });
  audio.play = vi.fn(async () => {
    Object.defineProperty(audio, 'paused', {
      value: false,
      writable: true,
      configurable: true,
    });
    audio.onplaying?.(new Event('playing'));
  });
  audio.pause = vi.fn(() => {
    Object.defineProperty(audio, 'paused', {
      value: true,
      writable: true,
      configurable: true,
    });
    audio.onpause?.(new Event('pause'));
  });
  audio.load = vi.fn();
  return audio;
}
function Harness() {
  const state = useAudioPlayback();
  const owner = {
    messageId: state.ownerMessageId ?? 'A',
    conversationId: null,
    requestId: state.ownerRequestId,
  };
  return (
    <>
      <output data-testid="phase">{state.phase}</output>
      <output data-testid="generation">{state.generation}</output>
      {['A', 'B'].map((id) => (
        <button
          key={id}
          onClick={() =>
            state.startSpeech({
              messageId: id,
              conversationId: null,
              text: 'Fictional speech',
              voice: 'daemon-default',
              speed: 1,
              format: 'mp3',
            })
          }
        >
          {id}
        </button>
      ))}
      <button onClick={() => state.pauseSpeech(owner)}>Pause</button>
      <button onClick={() => state.resumeSpeech(owner)}>Resume</button>
      <button onClick={() => state.stopSpeech(owner)}>Stop</button>
      <button onClick={() => state.seekSpeech(owner, 50)}>Seek</button>
      <TtsPlaybackBar />
    </>
  );
}
function mount(enabled = true) {
  return render(
    <AudioPlaybackProvider progressiveEnabled={enabled}>
      <Harness />
    </AudioPlaybackProvider>,
  );
}
function network() {
  const streams: ReadableStreamDefaultController<Uint8Array>[] = [];
  const cancels = vi.fn();
  const fetch = vi.fn(async (url: string) => {
    if (url.includes('capabilities')) return Response.json(caps);
    return new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          streams.push(controller);
        },
        cancel: cancels,
      }),
      {
        headers: {
          'Content-Type': 'application/vnd.daemon.speech-stream;version=1',
        },
      },
    );
  });
  vi.stubGlobal('fetch', fetch);
  return { fetch, streams, cancels };
}
beforeEach(() => {
  appendAuto = true;
  unsupported = false;
  refuseBuffer = false;
  sources.length = 0;
  clips.length = 0;
  auth.generation = 0;
  auth.listeners.clear();
  vi.stubGlobal('MediaSource', FakeMediaSource);
  vi.stubGlobal(
    'Audio',
    vi.fn(function () {
      return makeAudio();
    }),
  );
  vi.stubGlobal(
    'URL',
    class extends URL {
      static createObjectURL = vi.fn(() => `blob:fixture/${sources.length}`);
      static revokeObjectURL = vi.fn();
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('gated progressive provider', () => {
  it('keeps the default disabled path buffered with no capability GET', async () => {
    const fetch = vi.fn(async (url: string) =>
      url === '/api/tts'
        ? Response.json({ audio_path: '/generated-audio/fixture.mp3' })
        : new Response('fixture'),
    );
    vi.stubGlobal('fetch', fetch);
    mount(false);
    fireEvent.click(screen.getByText('A'));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(fetch.mock.calls[0][0]).toBe('/api/tts');
    expect(sources).toHaveLength(0);
  });

  it('streams before EOF, preserves early Pause through completion then resumes without POST', async () => {
    const { fetch, streams } = network();
    mount();
    fireEvent.click(screen.getByText('A'));
    await waitFor(() => expect(streams).toHaveLength(1));
    expect(clips[0].isConnected).toBe(true);
    fireEvent.click(screen.getByText('Pause'));
    await act(async () => {
      streams[0].enqueue(prefix());
    });
    await waitFor(() =>
      expect(screen.getByTestId('generation').textContent).toBe('receiving'),
    );
    expect(clips[0].play).not.toHaveBeenCalled();
    expect(screen.getByTestId('phase').textContent).toBe('paused');
    await act(async () => {
      streams[0].enqueue(control(2, complete));
      streams[0].close();
    });
    await waitFor(() =>
      expect(screen.getByTestId('generation').textContent).toBe('complete'),
    );
    expect(sources[0].eos).toBe(1);
    expect(screen.getByTestId('phase').textContent).toBe('paused');
    fireEvent.click(screen.getByText('Resume'));
    await waitFor(() =>
      expect(screen.getByTestId('phase').textContent).toBe('playing'),
    );
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(clips[0].play).toHaveBeenCalledOnce();
  });

  it('late Pause retains time through EOF, Resume and buffered-range seek', async () => {
    const { streams, fetch } = network();
    mount();
    fireEvent.click(screen.getByText('A'));
    await waitFor(() => expect(streams).toHaveLength(1));
    await act(async () => {
      streams[0].enqueue(prefix());
    });
    await waitFor(() =>
      expect(screen.getByTestId('phase').textContent).toBe('playing'),
    );
    clips[0].currentTime = 0.3;
    fireEvent.click(screen.getByText('Pause'));
    await act(async () => {
      streams[0].enqueue(control(2, complete));
      streams[0].close();
    });
    await waitFor(() =>
      expect(screen.getByTestId('generation').textContent).toBe('complete'),
    );
    expect(clips[0].currentTime).toBe(0.3);
    expect(screen.getByTestId('phase').textContent).toBe('paused');
    fireEvent.click(screen.getByText('Seek'));
    expect(clips[0].currentTime).toBe(0.8);
    fireEvent.click(screen.getByText('Resume'));
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(clips[0].play).toHaveBeenCalledTimes(2);
  });

  it('does not treat terminal receipt as success before clean EOF', async () => {
    const { streams } = network();
    mount();
    fireEvent.click(screen.getByText('A'));
    await waitFor(() => expect(streams).toHaveLength(1));
    await act(async () => {
      streams[0].enqueue(concat(prefix(), control(2, complete)));
    });
    await waitFor(() =>
      expect(screen.getByTestId('phase').textContent).toBe('playing'),
    );
    expect(sources[0].eos).toBe(0);
    expect(screen.getByTestId('generation').textContent).toBe('receiving');
    await act(async () => {
      streams[0].error(new Error('fixture reset'));
    });
    await waitFor(() =>
      expect(screen.getByTestId('phase').textContent).toBe('error'),
    );
    expect(sources[0].eos).toBe(0);
  });

  it('Stop/A→B cancels an active native append before cleanup and ignores stale events', async () => {
    appendAuto = false;
    const { streams, cancels, fetch } = network();
    mount();
    fireEvent.click(screen.getByText('A'));
    await waitFor(() => expect(streams).toHaveLength(1));
    await act(async () => {
      streams[0].enqueue(prefix());
    });
    await waitFor(() => expect(sources[0].buffer.updating).toBe(true));
    const stale = clips[0].onplaying;
    fireEvent.click(screen.getByText('B'));
    await waitFor(() => expect(streams).toHaveLength(2));
    expect(sources[0].buffer.abortCalls).toBe(1);
    expect(clips[0].isConnected).toBe(false);
    expect(cancels).toHaveBeenCalled();
    await act(async () => {
      stale?.call(clips[0], new Event('playing'));
      sources[0].buffer.finish();
    });
    expect(clips[1].isConnected).toBe(true);
    expect(clips[1].play).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('Stop'));
    expect(screen.getByTestId('phase').textContent).toBe('idle');
    expect(fetch).toHaveBeenCalledTimes(4);
  });

  it('auth invalidation cancels receiving media before a stale generation can publish', async () => {
    const { streams } = network();
    mount();
    fireEvent.click(screen.getByText('A'));
    await waitFor(() => expect(streams).toHaveLength(1));
    await act(async () => {
      streams[0].enqueue(prefix());
    });
    await waitFor(() =>
      expect(screen.getByTestId('phase').textContent).toBe('playing'),
    );
    await act(async () => {
      auth.generation++;
      for (const listener of auth.listeners) listener();
    });
    expect(screen.getByTestId('phase').textContent).toBe('idle');
    expect(clips[0].isConnected).toBe(false);
  });

  it('waiting/canplay is buffering, not an automatic retry or loss of pause intent', async () => {
    const { streams, fetch } = network();
    mount();
    fireEvent.click(screen.getByText('A'));
    await waitFor(() => expect(streams).toHaveLength(1));
    await act(async () => {
      streams[0].enqueue(prefix());
    });
    await waitFor(() =>
      expect(screen.getByTestId('phase').textContent).toBe('playing'),
    );
    act(() => clips[0].onwaiting?.(new Event('waiting')));
    expect(screen.getByTestId('phase').textContent).toBe('buffering');
    fireEvent.click(screen.getByText('Pause'));
    act(() => clips[0].oncanplay?.(new Event('canplay')));
    expect(screen.getByTestId('phase').textContent).toBe('paused');
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('local SourceBuffer refusal falls back BEFORE POST, but server/auth errors never do', async () => {
    refuseBuffer = true;
    const fetch = vi.fn(async (url: string) =>
      url.includes('capabilities')
        ? Response.json(caps)
        : url === '/api/tts'
          ? Response.json({ audio_path: '/generated-audio/fixture.mp3' })
          : new Response('fixture'),
    );
    vi.stubGlobal('fetch', fetch);
    mount();
    fireEvent.click(screen.getByText('A'));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
    expect(fetch.mock.calls[1][0]).toBe('/api/tts');
    expect(sources[0].removed).toBe(0);
    cleanup();
    refuseBuffer = false;
    fetch.mockClear();
    fetch.mockImplementation(
      async () => new Response('fixture denied', { status: 401 }),
    );
    mount();
    fireEvent.click(screen.getByText('A'));
    await waitFor(() =>
      expect(screen.getByTestId('phase').textContent).toBe('error'),
    );
    expect(fetch).toHaveBeenCalledOnce();
  });

  it('provider error after a playable prefix visibly interrupts without POST replay', async () => {
    const { streams, fetch } = network();
    mount();
    fireEvent.click(screen.getByText('A'));
    await waitFor(() => expect(streams).toHaveLength(1));
    await act(async () => {
      streams[0].enqueue(prefix());
    });
    await waitFor(() =>
      expect(screen.getByTestId('phase').textContent).toBe('playing'),
    );
    await act(async () => {
      streams[0].enqueue(control(3, { code: 'speech_failed' }));
    });
    await waitFor(() =>
      expect(screen.getByTestId('phase').textContent).toBe('error'),
    );
    expect(clips[0].isConnected).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});

describe('progressive resource/range contract', () => {
  it('retires the generation deadline before publishing complete, not during later playback', async () => {
    const audio = makeAudio();
    document.body.appendChild(audio);
    const playback = new ProgressivePlayback(
      audio,
      new AbortController().signal,
      () => true,
    );
    await playback.prepare();
    vi.useFakeTimers();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(concat(prefix(), control(2, complete)), {
            headers: {
              'Content-Type': 'application/vnd.daemon.speech-stream;version=1',
            },
          }),
      ),
    );
    const completed = vi.fn(() => {
      vi.advanceTimersByTime(130000);
    });
    await playback.receive({}, { voice: 'daemon-default', speed: 1 }, caps, {
      receiving: vi.fn(),
      appended: vi.fn(),
      complete: completed,
    });
    expect(completed).toHaveBeenCalledOnce();
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    playback.dispose();
    audio.remove();
  });

  it('a cancelled buffered blob continuation allocates no media or object URL', async () => {
    let finish!: (value: Blob) => void;
    const fetch = vi.fn(async (url: string) =>
      url === '/api/tts'
        ? Response.json({ audio_path: '/generated-audio/fixture.mp3' })
        : {
            ok: true,
            blob: () =>
              new Promise<Blob>((resolve) => {
                finish = resolve;
              }),
          },
    );
    vi.stubGlobal('fetch', fetch);
    mount(false);
    fireEvent.click(screen.getByText('A'));
    await waitFor(() => expect(finish).toBeDefined());
    fireEvent.click(screen.getByText('Stop'));
    await act(async () => {
      finish(new Blob(['fixture']));
    });
    expect(clips).toHaveLength(0);
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it('reentrant URL construction after invalidation cannot leak an old owner resource', async () => {
    const fetch = vi.fn(async (url: string) =>
      url === '/api/tts'
        ? Response.json({ audio_path: '/generated-audio/fixture.mp3' })
        : new Response('fixture'),
    );
    vi.stubGlobal('fetch', fetch);
    vi.mocked(URL.createObjectURL).mockImplementation(() => {
      auth.generation++;
      for (const listener of auth.listeners) listener();
      return 'blob:reentrant-old-owner';
    });
    mount(false);
    fireEvent.click(screen.getByText('A'));
    await waitFor(() =>
      expect(URL.revokeObjectURL).toHaveBeenCalledWith(
        'blob:reentrant-old-owner',
      ),
    );
    expect(clips).toHaveLength(0);
    expect(screen.getByTestId('phase').textContent).toBe('idle');
  });
  it('seeks only real intersection ranges and clamps gaps, never future PCM', () => {
    const audio = makeAudio();
    Object.defineProperty(audio, 'buffered', {
      value: ranges([
        [0, 2],
        [4, 8],
      ]),
    });
    Object.defineProperty(audio, 'seekable', {
      value: ranges([
        [1, 5],
        [6, 10],
      ]),
    });
    const available = availableSpeechRanges(audio);
    expect(available).toEqual([
      [1, 2],
      [4, 5],
      [6, 8],
    ]);
    expect(clampSpeechSeek(3, available)).toBe(2);
    expect(clampSpeechSeek(100, available)).toBe(8);
    expect(clampSpeechSeek(Infinity, available)).toBeNull();
    expect(clampSpeechSeek(1, [])).toBeNull();
  });

  it('does not await a never-resolving cancelled reader and revokes its URL once', async () => {
    const stop = new AbortController();
    const audio = makeAudio();
    document.body.appendChild(audio);
    const playback = new ProgressivePlayback(audio, stop.signal, () => true);
    await playback.prepare();
    const reader = {
      read: vi.fn(() => new Promise(() => {})),
      cancel: vi.fn(() => new Promise(() => {})),
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        headers: new Headers({
          'Content-Type': 'application/vnd.daemon.speech-stream;version=1',
        }),
        body: { getReader: () => reader },
      })),
    );
    const receiving = playback.receive(
      {},
      { voice: 'daemon-default', speed: 1 },
      caps,
      { receiving: vi.fn(), appended: vi.fn(), complete: vi.fn() },
    );
    await waitFor(() => expect(reader.read).toHaveBeenCalled());
    stop.abort();
    playback.dispose();
    await expect(receiving).rejects.toThrow('Speech interrupted');
    expect(URL.revokeObjectURL).toHaveBeenCalledOnce();
    audio.remove();
  });
});
