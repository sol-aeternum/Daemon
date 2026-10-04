import { type SpeechCapabilities } from './speechCapabilities';
import {
  SpeechStreamParser,
  isStrictSpeechStreamContentType,
  type SpeechStreamComplete,
} from './speechStreamProtocol';

export type SpeechRange = [number, number];

/** Native buffered ∩ seekable intervals, not an estimate of future synthesis. */
export function availableSpeechRanges(audio: HTMLAudioElement): SpeechRange[] {
  const ranges: SpeechRange[] = [];
  for (let b = 0; b < audio.buffered.length; b++) {
    for (let s = 0; s < audio.seekable.length; s++) {
      const start = Math.max(audio.buffered.start(b), audio.seekable.start(s));
      const end = Math.min(audio.buffered.end(b), audio.seekable.end(s));
      if (
        Number.isFinite(start) &&
        Number.isFinite(end) &&
        start >= 0 &&
        end > start
      ) {
        ranges.push([start, end]);
      }
    }
  }
  return ranges.sort((a, b) => a[0] - b[0]);
}

export function clampSpeechSeek(
  seconds: number,
  ranges: SpeechRange[],
): number | null {
  if (!Number.isFinite(seconds) || !ranges.length) return null;
  let nearest = ranges[0][0],
    distance = Infinity;
  for (const [start, end] of ranges) {
    const point = Math.max(start, Math.min(seconds, end));
    if (Math.abs(seconds - point) < distance) {
      nearest = point;
      distance = Math.abs(seconds - point);
    }
  }
  return nearest;
}

/** One attached element, one SourceBuffer, one awaited append and one network
 * read. This owns no playback intent: the provider handles Pause/Resume. */
export class ProgressivePlayback {
  readonly source: MediaSource;
  readonly url: string;
  private buffer: SourceBuffer | null = null;
  private reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  private pending = new Set<() => void>();
  private stopped = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private abort: () => void;
  private operationAbort = new AbortController();

  static supported(): boolean {
    try {
      return (
        typeof MediaSource !== 'undefined' &&
        MediaSource.isTypeSupported('audio/mpeg')
      );
    } catch {
      return false;
    }
  }

  constructor(
    readonly audio: HTMLAudioElement,
    readonly signal: AbortSignal,
    private readonly current: () => boolean,
  ) {
    this.source = new MediaSource();
    this.url = URL.createObjectURL(this.source);
    this.abort = () => this.dispose();
    signal.addEventListener('abort', this.abort, { once: true });
    try {
      audio.src = this.url;
    } catch (error) {
      this.dispose();
      throw error;
    }
    if (signal.aborted) this.dispose();
  }

  private check(): void {
    if (this.stopped || this.signal.aborted || !this.current())
      throw new Error('Speech cancelled');
  }

  private async bounded<T>(
    promise: Promise<T>,
    milliseconds = 15000,
  ): Promise<T> {
    this.check();
    let cancel!: () => void;
    let timer!: ReturnType<typeof setTimeout>;
    const interrupted = new Promise<never>((_, reject) => {
      cancel = () => reject(new Error('Speech interrupted'));
      this.pending.add(cancel);
      timer = setTimeout(
        () => reject(new Error('Speech stream timed out')),
        milliseconds,
      );
    });
    try {
      const result = await Promise.race([promise, interrupted]);
      this.check();
      return result;
    } finally {
      clearTimeout(timer);
      this.pending.delete(cancel);
    }
  }

  private event(
    target: EventTarget,
    name: string,
    milliseconds: number,
    action?: () => void,
  ): Promise<void> {
    let resolve!: () => void, reject!: (error: Error) => void;
    const ready = new Promise<void>((ok, fail) => {
      resolve = ok;
      reject = fail;
    });
    const failed = () => reject(new Error('Speech audio append failed'));
    const done = () => resolve();
    target.addEventListener(name, done, { once: true });
    target.addEventListener('error', failed, { once: true });
    target.addEventListener('abort', failed, { once: true });
    return this.bounded(
      (async () => {
        action?.();
        await ready;
      })(),
      milliseconds,
    ).finally(() => {
      target.removeEventListener(name, done);
      target.removeEventListener('error', failed);
      target.removeEventListener('abort', failed);
    });
  }

  /** A local refusal here is the ONLY place a caller may choose buffered instead,
   * because synthesis POST has not started. */
  async prepare(): Promise<void> {
    this.check();
    if (this.source.readyState !== 'open')
      await this.event(this.source, 'sourceopen', 3000);
    this.check();
    this.buffer = this.source.addSourceBuffer('audio/mpeg');
  }

  private async append(bytes: Uint8Array): Promise<void> {
    this.check();
    const buffer = this.buffer;
    if (!buffer || buffer.updating || this.source.readyState !== 'open') {
      throw new Error('Speech audio append unavailable');
    }
    // Copy a single ≤65532-byte frame for SourceBuffer (ArrayBuffer ownership),
    // then await updateend before requesting the next frame/network read.
    const copy = new Uint8Array(bytes.length);
    copy.set(bytes);
    await this.event(buffer, 'updateend', 10000, () =>
      buffer.appendBuffer(copy.buffer),
    );
  }

  private async finish(complete: SpeechStreamComplete): Promise<void> {
    this.check();
    if (
      !this.buffer ||
      this.buffer.updating ||
      this.source.readyState !== 'open'
    ) {
      throw new Error('Speech audio completion failed');
    }
    this.source.endOfStream();
    if (!Number.isFinite(this.audio.duration) || this.audio.duration <= 0) {
      await this.event(this.audio, 'durationchange', 15000);
    }
    this.check();
    if (
      !Number.isFinite(this.audio.duration) ||
      this.audio.duration <= 0 ||
      this.audio.duration > 300.15 ||
      Math.abs(this.audio.duration - complete.encoded_seconds) > 0.048001
    ) {
      throw new Error('Speech decoded duration did not match');
    }
  }

  async receive(
    headers: Record<string, string>,
    payload: Record<string, unknown>,
    capabilities: SpeechCapabilities,
    callbacks: {
      receiving: () => void;
      appended: () => void;
      complete: (value: SpeechStreamComplete) => void;
    },
  ): Promise<void> {
    this.check();
    this.timer = setTimeout(
      () => this.dispose(),
      capabilities.limits.deadline_seconds * 1000,
    );
    try {
      const response = await this.bounded(
        fetch('/api/tts/stream/v1', {
          method: 'POST',
          headers,
          signal: this.operationAbort.signal,
          redirect: 'error',
          cache: 'no-store',
          body: JSON.stringify({ ...payload, format: 'mp3' }),
        }),
      );
      if (
        !response.ok ||
        !response.body ||
        !isStrictSpeechStreamContentType(response.headers.get('content-type'))
      ) {
        void response.body?.cancel().catch(() => {});
        throw new Error(`Speech stream unavailable (${response.status})`);
      }
      this.reader = response.body.getReader();
      const parser = new SpeechStreamParser(
        {
          provider: capabilities.provider,
          model: capabilities.model,
          voice: String(payload.voice),
          speed: Number(payload.speed),
          format: 'mp3',
          mime: 'audio/mpeg',
          sample_rate: 24000,
          rendering: 'speech-mp3-progressive-v1',
          max_audio_bytes: capabilities.limits.max_audio_bytes,
          max_audio_frames: capabilities.limits.max_audio_frames,
          max_wire_bytes: capabilities.limits.max_wire_bytes,
          max_heartbeats: capabilities.limits.max_heartbeats,
          max_source_seconds: capabilities.limits.max_source_seconds,
          max_padding_seconds: capabilities.limits.max_padding_seconds,
          max_audio_frame_payload_bytes:
            capabilities.limits.max_audio_frame_payload,
          max_control_bytes: capabilities.limits.max_control_bytes,
        },
        {
          onMeta: () => {
            this.check();
            callbacks.receiving();
          },
          onAudio: async ({ bytes }) => {
            await this.append(bytes);
            this.check();
            callbacks.appended();
          },
          onHeartbeat: () => this.check(),
          onError: () => {
            throw new Error('Speech generation interrupted');
          },
          onComplete: async (value) => {
            await this.finish(value);
            this.check();
            // The generation deadline ends before publishing accepted completion.
            // Playback may legitimately last longer than the synthesis deadline.
            if (this.timer !== null) clearTimeout(this.timer);
            this.timer = null;
            callbacks.complete(value);
          },
        },
      );
      for (;;) {
        const { done, value } = await this.bounded(this.reader.read());
        if (done) break;
        await parser.push(value);
      }
      await parser.end();
    } finally {
      if (this.timer !== null) clearTimeout(this.timer);
      this.timer = null;
      // Do not await cancellation: a hostile/retired reader may never settle.
      void this.reader?.cancel().catch(() => {});
      this.reader = null;
    }
  }

  dispose(): void {
    if (this.stopped) return;
    this.stopped = true;
    this.signal.removeEventListener('abort', this.abort);
    this.operationAbort.abort();
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    for (const cancel of this.pending) cancel();
    this.pending.clear();
    void this.reader?.cancel().catch(() => {});
    this.reader = null;
    if (this.buffer) {
      try {
        if (this.buffer.updating) this.buffer.abort();
      } catch {
        /* Retired source. */
      }
      try {
        if (this.source.readyState === 'open')
          this.source.removeSourceBuffer(this.buffer);
      } catch {
        /* Source closed by native decode/disconnect. */
      }
      this.buffer = null;
    }
    URL.revokeObjectURL(this.url);
  }
}
