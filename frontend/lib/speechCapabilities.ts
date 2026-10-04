import {
  SPEECH_STREAM_MP3_MIME,
  SPEECH_STREAM_MP3_RENDERING,
  SPEECH_STREAM_MP3_SAMPLE_RATE,
} from './speechStreamProtocol';

export interface SpeechCapabilities {
  ready: true;
  provider: string;
  model: string;
  voices: string[];
  formats: string[];
  streams: {
    version: 1;
    format: 'mp3';
    mime: 'audio/mpeg';
    sample_rate: 24000;
    rendering: string;
  }[];
  speed_min: number;
  speed_max: number;
  limits: Record<string, number>;
}

/** Validate before selecting any synthesis transport. Never trust a boolean
 * streaming flag or silently turn a failed capability GET into a buffered POST. */
export function validateSpeechCapabilities(value: unknown): SpeechCapabilities {
  if (!value || typeof value !== 'object')
    throw new Error('Invalid speech capabilities');
  const v = value as Record<string, unknown>;
  const identity = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;
  if (
    v.ready !== true ||
    typeof v.provider !== 'string' ||
    !identity.test(v.provider) ||
    typeof v.model !== 'string' ||
    !identity.test(v.model) ||
    !Array.isArray(v.voices) ||
    !v.voices.includes('daemon-default') ||
    !v.voices.every((voice) => typeof voice === 'string') ||
    !Array.isArray(v.formats) ||
    !v.formats.includes('mp3') ||
    !v.formats.every((format) => ['mp3', 'opus', 'wav'].includes(format)) ||
    !Array.isArray(v.streams) ||
    v.streams.length > 8 ||
    v.speed_min !== 0.5 ||
    v.speed_max !== 2 ||
    !v.limits ||
    typeof v.limits !== 'object' ||
    Array.isArray(v.limits)
  ) {
    throw new Error('Invalid speech capabilities');
  }
  const ceilings: Record<string, number> = {
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
    deadline_seconds: 180,
  };
  const limits = v.limits as Record<string, unknown>;
  for (const [key, ceiling] of Object.entries(ceilings)) {
    const n = limits[key];
    if (typeof n !== 'number' || !Number.isFinite(n) || n <= 0 || n > ceiling) {
      throw new Error('Invalid speech capability limits');
    }
    if (
      !['max_padding_seconds', 'deadline_seconds'].includes(key) &&
      !Number.isInteger(n)
    ) {
      throw new Error('Invalid speech capability limits');
    }
  }
  for (const profile of v.streams) {
    if (
      !profile ||
      typeof profile !== 'object' ||
      profile.version !== 1 ||
      profile.format !== 'mp3' ||
      profile.mime !== SPEECH_STREAM_MP3_MIME ||
      profile.sample_rate !== SPEECH_STREAM_MP3_SAMPLE_RATE ||
      profile.rendering !== SPEECH_STREAM_MP3_RENDERING
    ) {
      throw new Error('Invalid speech streaming profile');
    }
  }
  return v as unknown as SpeechCapabilities;
}
