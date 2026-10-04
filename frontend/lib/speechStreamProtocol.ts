/**
 * Daemon progressive speech stream — client wire protocol.
 *
 * Transport (success only):
 *   Content-Type: application/vnd.daemon.speech-stream;version=1
 *   meta.version === 1
 *   magic: ASCII "DSP1"
 *   frames: uint8 kind | uint32 big-endian payload length | payload
 *
 *   kind 0 META      strict UTF-8 flat JSON, first frame, exactly once
 *   kind 1 AUDIO     uint32 big-endian seq | non-empty MP3 bytes
 *   kind 2 COMPLETE  strict UTF-8 flat JSON terminal success
 *   kind 3 ERROR     strict UTF-8 flat JSON terminal failure (sanitized code)
 *   kind 4 HEARTBEAT empty payload, at most one per cadence window
 *
 * Success is ONLY: a valid COMPLETE frame whose declared totals equal the
 * frames actually received, followed by a clean read EOF with nothing left
 * over. An HTTP 200, a playable prefix, or EOF on its own are never success.
 *
 * This module is deliberately self-contained and side-effect free. It holds no
 * audio longer than one callback and never persists, logs or transmits audio.
 */

/** Exact media type; `;version=1` is mandatory (see `isStrictSpeechStreamContentType`). */
export const SPEECH_STREAM_CONTENT_TYPE =
  'application/vnd.daemon.speech-stream';
export const SPEECH_STREAM_CONTENT_TYPE_VERSIONED = `${SPEECH_STREAM_CONTENT_TYPE};version=1`;
export const SPEECH_STREAM_VERSION = 1;
export const SPEECH_STREAM_MAGIC = 'DSP1';
export const SPEECH_STREAM_MP3_FORMAT = 'mp3';
export const SPEECH_STREAM_MP3_MIME = 'audio/mpeg';
export const SPEECH_STREAM_MP3_SAMPLE_RATE = 24000;
export const SPEECH_STREAM_MP3_RENDERING = 'speech-mp3-progressive-v1';
export const SPEECH_STREAM_CANONICAL_VOICE = 'daemon-default';

export const SPEECH_FRAME_META = 0;
export const SPEECH_FRAME_AUDIO = 1;
export const SPEECH_FRAME_COMPLETE = 2;
export const SPEECH_FRAME_ERROR = 3;
export const SPEECH_FRAME_HEARTBEAT = 4;

export const SPEECH_MAGIC_BYTES = 4;
export const SPEECH_FRAME_HEADER_BYTES = 5;
export const SPEECH_AUDIO_SEQ_BYTES = 4;

/**
 * Client hard bounds. These never widen with what a server advertises: a
 * capabilities payload may lower them, never raise them.
 */
export const MAX_SPEECH_AUDIO_FRAME_PAYLOAD_BYTES = 65536; // includes the 4-byte seq
export const MAX_SPEECH_CONTROL_BYTES = 4096;
export const MAX_SPEECH_AUDIO_BYTES = 16_000_000;
export const MAX_SPEECH_AUDIO_FRAMES = 16_384;
export const MAX_SPEECH_WIRE_BYTES = 20_000_000; // magic + headers + seq included
export const MAX_SPEECH_HEARTBEATS = 32;
export const MIN_HEARTBEAT_INTERVAL_SECONDS = 5;
export const MAX_SPEECH_SOURCE_SECONDS = 300;
export const MAX_SPEECH_PADDING_SECONDS = 0.15;
/** 5 header bytes + 65 536 payload bytes, plus one bounded 256-byte read slab. */
export const MAX_SPEECH_PARSER_PENDING_BYTES = 65541 + 256;

/** Float slack for the declared encoded-padding ceiling; far below one MP3 frame. */
const PADDING_EPSILON = 1e-9;
const UUID_PATTERN = /^[a-f0-9]{32}$(?![\s\S])/;
const PROVIDER_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;
/** Protected artifact path only: no credentials, query, fragment or traversal. */
const PROTECTED_MP3_PATH_PATTERN =
  /^\/generated-audio\/[a-f0-9]{64}\.mp3$(?![\s\S])/;
/** Upper bound the server must respect for whole-call synthesis time. */
const MAX_SYNTHESIS_SECONDS = 120;
const NUMBER_PATTERN = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/;

/** A wire violation. Never carries payload text, audio or credentials. */
export class SpeechStreamProtocolError extends Error {
  readonly reason: string;

  constructor(reason: string) {
    super(`Speech stream protocol error: ${reason}`);
    this.name = 'SpeechStreamProtocolError';
    this.reason = reason;
  }
}

export interface SpeechStreamMeta {
  version: number;
  stream_id: string;
  provider: string;
  model: string;
  voice: string;
  speed: number;
  format: string;
  mime: string;
  sample_rate: number;
  rendering: string;
  cached: boolean;
}

export interface SpeechStreamComplete {
  frames: number;
  bytes: number;
  source_seconds: number;
  encoded_seconds: number;
  synthesis_seconds: number;
  cache_available: boolean;
  audio_path: string | null;
}

/** Sanitized terminal failure reported inside an ERROR frame. */
export interface SpeechStreamErrorReport {
  code: string;
  message: string;
}

export interface SpeechStreamAudioFrame {
  seq: number;
  /**
   * Valid only for the duration of the callback: the parser reuses its single
   * bounded buffer. A handler that defers the bytes must copy them itself.
   */
  bytes: Uint8Array;
}

export interface SpeechStreamHandlers {
  onMeta: (meta: SpeechStreamMeta) => void | Promise<void>;
  onAudio: (frame: SpeechStreamAudioFrame) => void | Promise<void>;
  onHeartbeat: () => void | Promise<void>;
  /** Called only after validated terminal AND clean EOF. */
  onComplete: (complete: SpeechStreamComplete) => void | Promise<void>;
  onError: (report: SpeechStreamErrorReport) => void | Promise<void>;
}

/** Everything the meta and complete frames must agree with. */
export interface SpeechStreamExpectation {
  /** Canonical voice that must be echoed (`daemon-default`). */
  voice: string;
  /** Speed captured with the request; must be echoed as captured. */
  speed: number;
  /** Format captured with the request (the progressive render is MP3 only). */
  format: string;
  mime: string;
  sample_rate: number;
  rendering: string;
  /** Advertised capability provider/model when known; null stays unchecked. */
  provider?: string | null;
  model?: string | null;
  max_audio_bytes?: number;
  max_audio_frames?: number;
  max_wire_bytes?: number;
  max_control_bytes?: number;
  max_audio_frame_payload_bytes?: number;
  max_heartbeats?: number;
  heartbeat_seconds?: number;
  max_source_seconds?: number;
  max_padding_seconds?: number;
}

const META_KEYS = [
  'version',
  'stream_id',
  'provider',
  'model',
  'voice',
  'speed',
  'format',
  'mime',
  'sample_rate',
  'rendering',
  'cached',
] as const;

const COMPLETE_KEYS = [
  'frames',
  'bytes',
  'source_seconds',
  'encoded_seconds',
  'synthesis_seconds',
  'cache_available',
  'audio_path',
] as const;

const ERROR_KEYS = ['code'] as const;

/**
 * Sanitized terminal codes, mirroring the production `SpeechError` name set.
 * A code outside this set is a protocol violation, not a generic failure: the
 * server would be inventing a code the private runtime never emits.
 */
const ERROR_CODE_MESSAGES: Record<string, string> = {
  speech_failed: 'Speech synthesis failed',
  speech_admission_unavailable: 'Speech admission unavailable',
  speech_invalid_runtime_output: 'Speech output invalid',
  speech_output_limit: 'Speech output exceeded its limit',
  invalid_format: 'Speech format invalid',
  speech_busy: 'Speech synthesis is busy, try again shortly',
  speech_not_ready: 'Speech synthesis is still starting',
  speech_timeout: 'Speech took too long to synthesize',
  speech_cancelled: 'Speech playback was cancelled',
  speech_output_too_long: 'The response is too long to read aloud',
  speech_output_too_large: 'The speech audio is too large to play',
  invalid_speech_output: 'The speech audio was not valid',
  speech_stream_unsupported: 'Progressive speech is not supported here',
  speech_protocol_error: 'The speech stream was not valid',
  speech_backpressure_timeout: 'Speech playback fell behind and was stopped',
  speech_authorization_lost: 'Speech playback lost its authorization',
  speech_authorization_unavailable:
    'Speech authorization is temporarily unavailable',
  speech_stream_idle_timeout: 'Speech stopped delivering audio',
  speech_unavailable: 'Speech synthesis is unavailable',
  speech_capacity_unavailable:
    'Speech synthesis is at capacity, try again shortly',
  voice_unavailable: 'This voice is unavailable',
  unsupported_format: 'This audio format is not supported',
  invalid_speed: 'This speech speed is not supported',
  text_required: 'There is no text to read aloud',
  text_too_long: 'Speech is too long to synthesize',
  repetitive_text: 'This reply repeats too much to read aloud',
  invalid_text: 'This text cannot be read aloud',
};

function clampLimit(value: number | undefined, hardMax: number): number {
  if (value === undefined) return hardMax;
  if (!Number.isFinite(value) || value < 1)
    throw new SpeechStreamProtocolError('limit_invalid');
  return Math.min(Math.floor(value), hardMax);
}

function durationLimit(
  value: number | undefined,
  hardMax: number,
  allowZero = false,
): number {
  if (value === undefined) return hardMax;
  if (!Number.isFinite(value) || (allowZero ? value < 0 : value <= 0)) {
    throw new SpeechStreamProtocolError('limit_invalid');
  }
  return Math.min(value, hardMax);
}

function readUint32BE(bytes: Uint8Array, offset: number): number {
  return (
    (bytes[offset] * 0x1000000 +
      (bytes[offset + 1] << 16) +
      (bytes[offset + 2] << 8) +
      bytes[offset + 3]) >>>
    0
  );
}

/**
 * Validate a flat JSON object and reject duplicate member names.
 *
 * `JSON.parse` silently keeps the last duplicate member, so the raw text is
 * scanned first. Every schema here is flat, so a single bounded pass over the
 * top-level members is sufficient. `JSON.parse` then supplies the values once
 * the text is known to be well-formed.
 */
function parseFlatJson(
  text: string,
  allowedKeys: readonly string[],
): Record<string, unknown> {
  let index = 0;
  const length = text.length;

  const skipWhitespace = () => {
    while (index < length) {
      const code = text.charCodeAt(index);
      // JSON whitespace only: space, tab, LF, CR.
      if (code === 0x20 || code === 0x09 || code === 0x0a || code === 0x0d) {
        index += 1;
      } else {
        break;
      }
    }
  };

  const fail = (reason: string): never => {
    throw new SpeechStreamProtocolError(reason);
  };

  /** Reads the raw text between the quotes, decoding escapes is not needed. */
  const readRawString = (): string => {
    if (text[index] !== '"') return fail('json_string_expected');
    index += 1;
    const from = index;
    while (index < length) {
      const code = text.charCodeAt(index);
      if (code === 0x22) {
        const raw = text.slice(from, index);
        index += 1;
        return raw;
      }
      if (code === 0x5c) {
        if (index + 1 >= length) return fail('json_escape_truncated');
        index += 2;
        continue;
      }
      if (code < 0x20) return fail('json_control_character');
      index += 1;
    }
    return fail('json_string_unterminated');
  };

  const readScalar = (): void => {
    const character = text[index];
    if (character === '"') {
      readRawString();
      return;
    }
    if (character === 't' || character === 'f' || character === 'n') {
      const literal =
        character === 't' ? 'true' : character === 'f' ? 'false' : 'null';
      if (text.startsWith(literal, index)) {
        index += literal.length;
        return;
      }
      return fail('json_literal_invalid');
    }
    if (character === '{' || character === '[') {
      return fail('json_nested_value_unsupported');
    }
    const match = NUMBER_PATTERN.exec(text.slice(index));
    if (!match || match[0].length === 0) return fail('json_value_invalid');
    index += match[0].length;
  };

  skipWhitespace();
  if (text[index] !== '{') return fail('json_object_expected');
  index += 1;

  const seen = new Set<string>();
  skipWhitespace();
  if (text[index] !== '}') {
    for (;;) {
      skipWhitespace();
      const rawKey = readRawString();
      // Decode escapes so `"\u0061"` cannot hide a duplicate of `"a"`.
      const key = JSON.parse(`"${rawKey}"`) as string;
      skipWhitespace();
      if (text[index] !== ':') return fail('json_colon_expected');
      index += 1;
      skipWhitespace();
      readScalar();
      if (seen.has(key)) return fail('json_duplicate_key');
      if (!allowedKeys.includes(key)) return fail('json_unknown_key');
      seen.add(key);
      skipWhitespace();
      if (text[index] === ',') {
        index += 1;
        continue;
      }
      if (text[index] === '}') {
        index += 1;
        break;
      }
      return fail('json_member_separator_expected');
    }
  } else {
    index += 1;
  }
  skipWhitespace();
  if (index !== length) return fail('json_trailing_content');

  return JSON.parse(text) as Record<string, unknown>;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireExactString(
  record: Record<string, unknown>,
  key: string,
): string {
  const value = record[key];
  if (typeof value !== 'string' || value.length === 0) {
    throw new SpeechStreamProtocolError(`${key}_invalid`);
  }
  return value;
}

function requireFiniteNumber(
  record: Record<string, unknown>,
  key: string,
): number {
  const value = record[key];
  // typeof rejects booleans, strings and null instead of coercing them.
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new SpeechStreamProtocolError(`${key}_invalid`);
  }
  return value;
}

function requireNonNegativeInteger(
  record: Record<string, unknown>,
  key: string,
): number {
  const value = requireFiniteNumber(record, key);
  if (!Number.isInteger(value) || value < 0) {
    throw new SpeechStreamProtocolError(`${key}_invalid`);
  }
  return value;
}

function requirePositiveNumber(
  record: Record<string, unknown>,
  key: string,
): number {
  const value = requireFiniteNumber(record, key);
  if (value <= 0) throw new SpeechStreamProtocolError(`${key}_invalid`);
  return value;
}

function requireBoolean(record: Record<string, unknown>, key: string): boolean {
  const value = record[key];
  if (typeof value !== 'boolean') {
    throw new SpeechStreamProtocolError(`${key}_invalid`);
  }
  return value;
}

function requirePresent(record: Record<string, unknown>, key: string): void {
  if (!Object.prototype.hasOwnProperty.call(record, key)) {
    throw new SpeechStreamProtocolError(`${key}_missing`);
  }
}

function readUtf8Strict(bytes: Uint8Array): string {
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch {
    throw new SpeechStreamProtocolError('utf8_invalid');
  }
}

/**
 * Exact versioned content type.
 *
 * The media type is case-insensitive. Exactly one `version=1` parameter is required: an
 * unversioned, extra-parameter or duplicate-parameter value is a violation.
 */
export function isStrictSpeechStreamContentType(
  contentType: string | null,
): boolean {
  if (typeof contentType !== 'string') return false;
  const parts = contentType.split(';').map((part) => part.trim().toLowerCase());
  if (parts.length !== 2) return false;
  return parts[0] === SPEECH_STREAM_CONTENT_TYPE && parts[1] === 'version=1';
}

export function validateSpeechStreamMeta(
  record: Record<string, unknown>,
  expectation: SpeechStreamExpectation,
): SpeechStreamMeta {
  if (!isPlainObject(record)) {
    throw new SpeechStreamProtocolError('meta_not_an_object');
  }
  for (const key of META_KEYS) requirePresent(record, key);

  const version = requireNonNegativeInteger(record, 'version');
  if (version !== SPEECH_STREAM_VERSION) {
    throw new SpeechStreamProtocolError('meta_version_unsupported');
  }

  const streamId = requireExactString(record, 'stream_id');
  if (!UUID_PATTERN.test(streamId)) {
    throw new SpeechStreamProtocolError('stream_id_invalid');
  }

  const provider = requireExactString(record, 'provider');
  const model = requireExactString(record, 'model');
  if (!PROVIDER_ID_PATTERN.test(provider)) {
    throw new SpeechStreamProtocolError('provider_invalid');
  }
  if (!PROVIDER_ID_PATTERN.test(model)) {
    throw new SpeechStreamProtocolError('model_invalid');
  }
  if (expectation.provider && provider !== expectation.provider) {
    throw new SpeechStreamProtocolError('provider_mismatch');
  }
  if (expectation.model && model !== expectation.model) {
    throw new SpeechStreamProtocolError('model_mismatch');
  }

  const voice = requireExactString(record, 'voice');
  if (voice !== expectation.voice) {
    throw new SpeechStreamProtocolError('voice_mismatch');
  }
  if (voice !== SPEECH_STREAM_CANONICAL_VOICE) {
    throw new SpeechStreamProtocolError('voice_not_canonical');
  }

  const speed = requireFiniteNumber(record, 'speed');
  if (speed < 0.5 || speed > 2) {
    throw new SpeechStreamProtocolError('speed_out_of_range');
  }
  if (!Number.isFinite(expectation.speed) || speed !== expectation.speed) {
    throw new SpeechStreamProtocolError('speed_mismatch');
  }

  const format = requireExactString(record, 'format');
  if (format !== SPEECH_STREAM_MP3_FORMAT || format !== expectation.format) {
    throw new SpeechStreamProtocolError('format_mismatch');
  }

  const mime = requireExactString(record, 'mime');
  if (mime !== SPEECH_STREAM_MP3_MIME || mime !== expectation.mime) {
    throw new SpeechStreamProtocolError('mime_mismatch');
  }

  const sampleRate = requireNonNegativeInteger(record, 'sample_rate');
  if (
    sampleRate !== SPEECH_STREAM_MP3_SAMPLE_RATE ||
    sampleRate !== expectation.sample_rate
  ) {
    throw new SpeechStreamProtocolError('sample_rate_mismatch');
  }

  const rendering = requireExactString(record, 'rendering');
  if (
    rendering !== SPEECH_STREAM_MP3_RENDERING ||
    rendering !== expectation.rendering
  ) {
    throw new SpeechStreamProtocolError('rendering_mismatch');
  }

  return {
    version,
    stream_id: streamId,
    provider,
    model,
    voice,
    speed,
    format,
    mime,
    sample_rate: sampleRate,
    rendering,
    // Informational only: meta `cached` may legitimately be either value.
    cached: requireBoolean(record, 'cached'),
  };
}

export function validateSpeechStreamComplete(
  record: Record<string, unknown>,
  expectation: SpeechStreamExpectation,
  observed: { audioFrames: number; audioBytes: number },
): SpeechStreamComplete {
  for (const key of COMPLETE_KEYS) requirePresent(record, key);

  const audioFrames = requireNonNegativeInteger(record, 'frames');
  const bytes = requireNonNegativeInteger(record, 'bytes');
  if (audioFrames !== observed.audioFrames || bytes !== observed.audioBytes) {
    throw new SpeechStreamProtocolError('complete_totals_mismatch');
  }
  if (audioFrames <= 0 || bytes <= 0) throwNew('complete_without_audio');

  const maxSource = durationLimit(
    expectation.max_source_seconds,
    MAX_SPEECH_SOURCE_SECONDS,
  );
  const maxPadding = durationLimit(
    expectation.max_padding_seconds,
    MAX_SPEECH_PADDING_SECONDS,
    true,
  );

  const sourceSeconds = requirePositiveNumber(record, 'source_seconds');
  if (sourceSeconds > maxSource) {
    throw new SpeechStreamProtocolError('source_seconds_out_of_range');
  }

  const encodedSeconds = requirePositiveNumber(record, 'encoded_seconds');
  if (
    encodedSeconds < sourceSeconds ||
    encodedSeconds > sourceSeconds + maxPadding + PADDING_EPSILON ||
    encodedSeconds > maxSource + maxPadding + PADDING_EPSILON
  ) {
    throw new SpeechStreamProtocolError('encoded_seconds_out_of_range');
  }

  const synthesisSeconds = requireFiniteNumber(record, 'synthesis_seconds');
  if (synthesisSeconds < 0 || synthesisSeconds > MAX_SYNTHESIS_SECONDS) {
    throw new SpeechStreamProtocolError('synthesis_seconds_negative');
  }

  const cacheAvailable = requireBoolean(record, 'cache_available');
  const audioPath = record.audio_path;
  if (audioPath !== null && typeof audioPath !== 'string') {
    throw new SpeechStreamProtocolError('audio_path_invalid');
  }
  if (typeof audioPath === 'string') {
    if (!PROTECTED_MP3_PATH_PATTERN.test(audioPath)) {
      throw new SpeechStreamProtocolError('audio_path_invalid');
    }
    // A protected artifact path may only be advertised for cached audio.
    if (!cacheAvailable) return throwNew('audio_path_without_cache');
  }
  if (cacheAvailable && audioPath === null)
    return throwNew('cache_without_audio_path');

  return {
    frames: audioFrames,
    bytes,
    source_seconds: sourceSeconds,
    encoded_seconds: encodedSeconds,
    synthesis_seconds: synthesisSeconds,
    cache_available: cacheAvailable,
    audio_path: typeof audioPath === 'string' ? audioPath : null,
  };

  function throwNew(reason: string): never {
    throw new SpeechStreamProtocolError(reason);
  }
}

export function validateSpeechStreamError(
  record: Record<string, unknown>,
): SpeechStreamErrorReport {
  for (const key of ERROR_KEYS) requirePresent(record, key);
  const code = record.code;
  if (
    typeof code !== 'string' ||
    !Object.prototype.hasOwnProperty.call(ERROR_CODE_MESSAGES, code)
  ) {
    throw new SpeechStreamProtocolError('code_invalid');
  }
  return {
    code,
    message: ERROR_CODE_MESSAGES[code],
  };
}

export function encodeUint32BE(value: number): Uint8Array {
  return new Uint8Array([
    (value >>> 24) & 0xff,
    (value >>> 16) & 0xff,
    (value >>> 8) & 0xff,
    value & 0xff,
  ]);
}

/** Frame encoder shared with the proxy pass-through tests and the harness. */
export function encodeSpeechStreamFrame(
  kind: number,
  payload: Uint8Array,
): Uint8Array {
  const frame = new Uint8Array(SPEECH_FRAME_HEADER_BYTES + payload.length);
  frame[0] = kind;
  frame.set(encodeUint32BE(payload.length), 1);
  frame.set(payload, SPEECH_FRAME_HEADER_BYTES);
  return frame;
}

export function encodeSpeechStreamMagic(): Uint8Array {
  return new Uint8Array([
    SPEECH_STREAM_MAGIC.charCodeAt(0),
    SPEECH_STREAM_MAGIC.charCodeAt(1),
    SPEECH_STREAM_MAGIC.charCodeAt(2),
    SPEECH_STREAM_MAGIC.charCodeAt(3),
  ]);
}

export function encodeSpeechStreamAudioFrame(
  seq: number,
  mp3: Uint8Array,
): Uint8Array {
  const payload = new Uint8Array(SPEECH_AUDIO_SEQ_BYTES + mp3.length);
  payload.set(encodeUint32BE(seq), 0);
  payload.set(mp3, SPEECH_AUDIO_SEQ_BYTES);
  return encodeSpeechStreamFrame(SPEECH_FRAME_AUDIO, payload);
}

export function encodeSpeechStreamControlFrame(
  kind: number,
  value: unknown,
): Uint8Array {
  return encodeSpeechStreamFrame(
    kind,
    new TextEncoder().encode(JSON.stringify(value)),
  );
}

export function parseSpeechStreamControlFrame(
  kind: number,
  payload: Uint8Array,
): Record<string, unknown> {
  return parseFlatJson(
    readUtf8Strict(payload),
    kind === SPEECH_FRAME_META
      ? META_KEYS
      : kind === SPEECH_FRAME_ERROR
        ? ERROR_KEYS
        : COMPLETE_KEYS,
  );
}

/**
 * Incremental, bounded, strictly validating parser.
 *
 * Callbacks are awaited one at a time in wire order, so the player applies real
 * backpressure and no whole-response frame array is ever built. At most
 * `MAX_SPEECH_PARSER_PENDING_BYTES` are ever resident: an oversized network
 * chunk is copied in bounded slabs rather than growing the buffer.
 */
export class SpeechStreamParser {
  private readonly expectation: SpeechStreamExpectation;
  private readonly handlers: SpeechStreamHandlers;
  private readonly maxAudioPayload: number;
  private readonly maxControlBytes: number;
  private readonly maxAudioBytes: number;
  private readonly maxAudioFrames: number;
  private readonly maxWireBytes: number;
  private readonly maxHeartbeats: number;
  private buffer = new Uint8Array(MAX_SPEECH_PARSER_PENDING_BYTES);
  private start = 0;
  private cursor = 0;
  private sawMagic = false;
  private sawMeta = false;
  private cached = false;
  private terminal: 'complete' | 'error' | null = null;
  private complete: SpeechStreamComplete | null = null;
  private wireBytes = 0;
  private audioBytes = 0;
  private audioFrames = 0;
  private heartbeats = 0;
  private expectedSeq = 0;
  private busy = false;
  private ended = false;
  private failure: unknown = null;

  constructor(
    expectation: SpeechStreamExpectation,
    handlers: SpeechStreamHandlers,
  ) {
    this.expectation = { ...expectation };
    this.handlers = { ...handlers };
    this.maxAudioPayload = clampLimit(
      expectation.max_audio_frame_payload_bytes,
      MAX_SPEECH_AUDIO_FRAME_PAYLOAD_BYTES,
    );
    this.maxControlBytes = clampLimit(
      expectation.max_control_bytes,
      MAX_SPEECH_CONTROL_BYTES,
    );
    this.maxAudioBytes = clampLimit(
      expectation.max_audio_bytes,
      MAX_SPEECH_AUDIO_BYTES,
    );
    this.maxAudioFrames = clampLimit(
      expectation.max_audio_frames,
      MAX_SPEECH_AUDIO_FRAMES,
    );
    this.maxWireBytes = clampLimit(
      expectation.max_wire_bytes,
      MAX_SPEECH_WIRE_BYTES,
    );
    this.maxHeartbeats = clampLimit(
      expectation.max_heartbeats,
      MAX_SPEECH_HEARTBEATS,
    );
  }

  get audioFrameCount(): number {
    return this.audioFrames;
  }

  get audioByteCount(): number {
    return this.audioBytes;
  }

  get terminalOutcome(): 'complete' | 'error' | null {
    return this.terminal;
  }

  get completeSummary(): SpeechStreamComplete | null {
    return this.complete;
  }

  /** Bytes currently retained while waiting for more input. */
  get pendingBytes(): number {
    return this.cursor - this.start;
  }

  /** Await each push before reading more input; concurrent calls are rejected,
   * not chained into a hidden promise queue retaining network chunks. */
  async push(chunk: Uint8Array): Promise<void> {
    this.beginInput();
    try {
      if (
        !ArrayBuffer.isView(chunk) ||
        Object.prototype.toString.call(chunk) !== '[object Uint8Array]'
      )
        throw new SpeechStreamProtocolError('input_invalid');
      await this.ingest(chunk);
    } catch (error) {
      this.failure = error;
      throw error;
    } finally {
      this.busy = false;
    }
  }

  /** Clean read EOF. Only COMPLETE plus an empty remainder is success. */
  async end(): Promise<void> {
    this.beginInput();
    try {
      await this.finish();
      this.ended = true;
    } catch (error) {
      this.failure = error;
      throw error;
    } finally {
      this.busy = false;
    }
  }

  private beginInput(): void {
    if (this.failure !== null) throw this.failure;
    if (this.busy) throw new SpeechStreamProtocolError('input_in_flight');
    if (this.ended) throw new SpeechStreamProtocolError('input_after_eof');
    this.busy = true;
  }

  private async ingest(chunk: Uint8Array): Promise<void> {
    if (chunk.length === 0) return;
    this.wireBytes += chunk.length;
    if (this.wireBytes > this.maxWireBytes) {
      throw new SpeechStreamProtocolError('wire_bytes_exceeded');
    }
    let offset = 0;
    while (offset < chunk.length) {
      const room = MAX_SPEECH_PARSER_PENDING_BYTES - this.pendingBytes;
      if (room <= 0) {
        // Unreachable while the frame bound holds; fail closed rather than grow.
        throw new SpeechStreamProtocolError('parser_buffer_exceeded');
      }
      const take = Math.min(256, room, chunk.length - offset);
      this.ensureCapacity(take);
      this.buffer.set(chunk.subarray(offset, offset + take), this.cursor);
      this.cursor += take;
      offset += take;
      await this.drain();
    }
  }

  private ensureCapacity(extra: number): void {
    const pending = this.pendingBytes;
    if (this.buffer.length - this.cursor >= extra) return;
    if (this.start > 0) {
      this.buffer.copyWithin(0, this.start, this.cursor);
      this.start = 0;
      this.cursor = pending;
    }
    if (this.buffer.length - this.cursor < extra) {
      throw new SpeechStreamProtocolError('parser_buffer_exceeded');
    }
  }

  private async drain(): Promise<void> {
    for (;;) {
      if (this.terminal !== null) {
        // Anything after a terminal frame is a violation, not padding.
        if (this.cursor > this.start) {
          throw new SpeechStreamProtocolError('content_after_terminal');
        }
        return;
      }
      if (!this.sawMagic) {
        if (this.pendingBytes < SPEECH_MAGIC_BYTES) return;
        for (let index = 0; index < SPEECH_MAGIC_BYTES; index += 1) {
          if (
            this.buffer[this.start + index] !==
            SPEECH_STREAM_MAGIC.charCodeAt(index)
          ) {
            throw new SpeechStreamProtocolError('magic_mismatch');
          }
        }
        this.start += SPEECH_MAGIC_BYTES;
        this.sawMagic = true;
        continue;
      }
      if (this.pendingBytes < SPEECH_FRAME_HEADER_BYTES) return;
      const kind = this.buffer[this.start];
      const length = readUint32BE(this.buffer, this.start + 1);
      if (kind > SPEECH_FRAME_HEARTBEAT) {
        throw new SpeechStreamProtocolError('unknown_frame_kind');
      }
      if (kind === SPEECH_FRAME_AUDIO) {
        if (
          length < SPEECH_AUDIO_SEQ_BYTES + 1 ||
          length > this.maxAudioPayload
        ) {
          throw new SpeechStreamProtocolError('audio_frame_length_invalid');
        }
      } else if (kind === SPEECH_FRAME_HEARTBEAT) {
        if (length !== 0) {
          throw new SpeechStreamProtocolError('heartbeat_payload_invalid');
        }
      } else if (length > this.maxControlBytes) {
        throw new SpeechStreamProtocolError('control_frame_too_large');
      }

      if (this.pendingBytes < SPEECH_FRAME_HEADER_BYTES + length) return;

      const payload = this.buffer.subarray(
        this.start + SPEECH_FRAME_HEADER_BYTES,
        this.start + SPEECH_FRAME_HEADER_BYTES + length,
      );
      await this.dispatch(kind, payload);
      this.start += SPEECH_FRAME_HEADER_BYTES + length;
      if (this.start === this.cursor) {
        this.start = 0;
        this.cursor = 0;
      }
    }
  }

  private async dispatch(kind: number, payload: Uint8Array): Promise<void> {
    if (kind === SPEECH_FRAME_META) {
      if (this.sawMeta) {
        throw new SpeechStreamProtocolError('meta_repeated');
      }
      const meta = validateSpeechStreamMeta(
        parseSpeechStreamControlFrame(SPEECH_FRAME_META, payload),
        this.expectation,
      );
      this.sawMeta = true;
      this.cached = meta.cached;
      await this.handlers.onMeta(meta);
      return;
    }
    if (!this.sawMeta) {
      throw new SpeechStreamProtocolError('meta_missing');
    }

    if (kind === SPEECH_FRAME_AUDIO) {
      if (this.audioFrames >= this.maxAudioFrames) {
        throw new SpeechStreamProtocolError('audio_frames_exceeded');
      }
      const seq = readUint32BE(payload, 0);
      if (seq !== this.expectedSeq) {
        throw new SpeechStreamProtocolError('audio_sequence_invalid');
      }
      const bytes = payload.subarray(SPEECH_AUDIO_SEQ_BYTES);
      this.audioBytes += bytes.length;
      if (this.audioBytes > this.maxAudioBytes) {
        throw new SpeechStreamProtocolError('audio_bytes_exceeded');
      }
      this.audioFrames += 1;
      this.expectedSeq += 1;
      await this.handlers.onAudio({ seq, bytes });
      return;
    }

    if (kind === SPEECH_FRAME_HEARTBEAT) {
      if (this.heartbeats >= this.maxHeartbeats) {
        throw new SpeechStreamProtocolError('heartbeats_exceeded');
      }
      this.heartbeats += 1;
      await this.handlers.onHeartbeat();
      return;
    }

    const record = parseSpeechStreamControlFrame(kind, payload);
    if (kind === SPEECH_FRAME_ERROR) {
      const report = validateSpeechStreamError(record);
      this.terminal = 'error';
      await this.handlers.onError(report);
      // A consumer callback cannot turn a provider error into clean success.
      throw new SpeechStreamProtocolError(report.code);
    }
    const summary = validateSpeechStreamComplete(record, this.expectation, {
      audioFrames: this.audioFrames,
      audioBytes: this.audioBytes,
    });
    if (this.cached && summary.synthesis_seconds !== 0) {
      throw new SpeechStreamProtocolError('cached_synthesis_seconds_invalid');
    }
    this.terminal = 'complete';
    this.complete = summary;
  }

  private async finish(): Promise<void> {
    if (!this.sawMagic) {
      throw new SpeechStreamProtocolError('stream_truncated');
    }
    if (this.terminal !== 'complete' || this.complete === null) {
      throw new SpeechStreamProtocolError('stream_truncated');
    }
    if (this.audioFrames === 0) {
      throw new SpeechStreamProtocolError('complete_without_audio');
    }
    if (this.cursor > this.start) {
      throw new SpeechStreamProtocolError('trailing_bytes_after_terminal');
    }
    await this.handlers.onComplete(this.complete);
  }
}
