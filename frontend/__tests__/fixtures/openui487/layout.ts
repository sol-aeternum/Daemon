/**
 * #487 synthetic pilot layout grammar.
 *
 * Closed ASCII grammar, one byte bound, one parse:
 *
 *   root = PilotLayout([<Block>(), <Block>(), <Block>()])
 *
 * Each allowlisted block appears exactly once. There are no props, nested
 * expressions, queries, tools, actions or generic interpretation. The model
 * text may only choose display order; all values are app-owned.
 */

export const PILOT_BLOCKS = [
  'TaskStatus',
  'ArtifactResults',
  'Figures',
] as const;
export type PilotBlock = (typeof PILOT_BLOCKS)[number];
export const PILOT_FALLBACK_ORDER: readonly PilotBlock[] = [...PILOT_BLOCKS];
export const PILOT_LAYOUT_MAX_BYTES = 256;
export const PILOT_LAYOUT_MAX_CHUNKS = 257;
export const PILOT_LAYOUT_SCHEMA_VERSION = 1;

const BLOCK_SET: ReadonlySet<string> = new Set(PILOT_BLOCKS);

export type PilotLayoutFrame = {
  schema_version: typeof PILOT_LAYOUT_SCHEMA_VERSION;
  recorded_at: string;
  text: string;
};

export type PilotLayoutRejection =
  | 'layout_disabled'
  | 'layout_absent'
  | 'layout_pending_final_bit'
  | 'layout_unversioned'
  | 'layout_unsupported_version'
  | 'layout_chunk_mismatch'
  | 'layout_too_large'
  | 'layout_non_ascii'
  | 'layout_invalid_syntax'
  | 'layout_unknown_block'
  | 'layout_forbidden_props'
  | 'layout_duplicate_block'
  | 'layout_missing_block'
  | 'layout_envelope_unsupported';

export type PilotLayoutParseResult =
  | { ok: true; order: PilotBlock[] }
  | { ok: false; reason: PilotLayoutRejection };

export type PilotLayoutInput = {
  frame?: PilotLayoutFrame | null;
  /** Chunks received so far. Omitted means the whole frame arrived at once. */
  chunks?: readonly string[];
  /** Independent terminal bit. No chunk is parsed for publication until true. */
  final?: boolean;
};

export type PilotLayoutResolution = {
  active: boolean;
  order: readonly PilotBlock[];
  rejection: PilotLayoutRejection | null;
};

export const PILOT_LAYOUT_REASON_TEXT: Record<PilotLayoutRejection, string> = {
  layout_disabled:
    'Recorded layout is disabled. Authorized app data stays visible in the safe fallback order.',
  layout_absent:
    'No recorded layout was supplied. Authorized app data stays visible in the safe fallback order.',
  layout_pending_final_bit:
    'Waiting for the independent final layout bit. No partial frame is active.',
  layout_unversioned:
    'The recorded layout has no schema version. Authorized app data stays visible in the safe fallback order.',
  layout_unsupported_version:
    'The recorded layout schema version is unsupported. Authorized app data stays visible in the safe fallback order.',
  layout_chunk_mismatch:
    'The streamed chunks do not match the recorded layout. Authorized app data stays visible in the safe fallback order.',
  layout_too_large:
    'The recorded layout exceeds the closed byte cap. It was rejected before parsing.',
  layout_non_ascii:
    'The recorded layout is not ASCII. It was rejected before parsing.',
  layout_invalid_syntax:
    'The recorded layout does not use the closed pilot grammar. Authorized app data stays visible in the safe fallback order.',
  layout_unknown_block:
    'The recorded layout names a block outside the pilot allowlist. Authorized app data stays visible in the safe fallback order.',
  layout_forbidden_props:
    'The recorded layout passes arguments to a block. Pilot blocks take no props.',
  layout_duplicate_block:
    'The recorded layout repeats a block. Each pilot block must appear exactly once.',
  layout_missing_block:
    'The recorded layout omits a required block. All three pilot blocks must appear.',
  layout_envelope_unsupported:
    'The recorded layout envelope is unsupported. Authorized native app data stays visible in the safe fallback order.',
};

export function utf8ByteLength(value: string): number {
  let bytes = 0;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code < 0x80) bytes += 1;
    else if (code < 0x800) bytes += 2;
    else if (code >= 0xd800 && code <= 0xdbff) {
      bytes += 4;
      index += 1;
    } else bytes += 3;
  }
  return bytes;
}

export function serializePilotLayout(order: readonly PilotBlock[]): string {
  if (
    order.length !== PILOT_BLOCKS.length ||
    new Set(order).size !== PILOT_BLOCKS.length ||
    !order.every((block) => BLOCK_SET.has(block))
  ) {
    throw new Error('invalid_pilot_layout_order');
  }
  return `root = PilotLayout([${order.map((block) => `${block}()`).join(', ')}])`;
}

export function parsePilotLayout(text: string): PilotLayoutParseResult {
  if (typeof text !== 'string') {
    return { ok: false, reason: 'layout_invalid_syntax' };
  }
  // Length is a cheap pre-check; the byte check below handles non-ASCII text.
  if (text.length > PILOT_LAYOUT_MAX_BYTES) {
    return { ok: false, reason: 'layout_too_large' };
  }
  // Cap before trimming or applying the grammar.
  if (utf8ByteLength(text) > PILOT_LAYOUT_MAX_BYTES) {
    return { ok: false, reason: 'layout_too_large' };
  }
  for (let index = 0; index < text.length; index += 1) {
    if (text.charCodeAt(index) > 0x7f) {
      return { ok: false, reason: 'layout_non_ascii' };
    }
  }

  const outer = /^root\s*=\s*PilotLayout\s*\(\s*\[([^[\]]*)\]\s*\)$/.exec(
    text.trim(),
  );
  if (!outer) return { ok: false, reason: 'layout_invalid_syntax' };

  const parts = outer[1].split(',').map((part) => part.trim());
  if (parts.some((part) => part.length === 0)) {
    return { ok: false, reason: 'layout_invalid_syntax' };
  }

  const order: PilotBlock[] = [];
  for (const part of parts) {
    const call = /^([A-Za-z][A-Za-z0-9]*)\(\)$/.exec(part);
    if (!call) {
      const named = /^([A-Za-z][A-Za-z0-9]*)\(/.exec(part);
      if (named && BLOCK_SET.has(named[1])) {
        return { ok: false, reason: 'layout_forbidden_props' };
      }
      return {
        ok: false,
        reason: named ? 'layout_unknown_block' : 'layout_invalid_syntax',
      };
    }
    if (!BLOCK_SET.has(call[1])) {
      return { ok: false, reason: 'layout_unknown_block' };
    }
    order.push(call[1] as PilotBlock);
  }

  if (new Set(order).size < order.length) {
    return { ok: false, reason: 'layout_duplicate_block' };
  }
  if (order.length !== PILOT_BLOCKS.length) {
    return { ok: false, reason: 'layout_missing_block' };
  }
  return { ok: true, order };
}

function fallback(reason: PilotLayoutRejection): PilotLayoutResolution {
  return { active: false, order: PILOT_FALLBACK_ORDER, rejection: reason };
}

/**
 * Resolve the recorded frame and chunks. A non-final stream may be a proper
 * prefix; a final stream must equal the complete recorded snapshot. Both the
 * frame and the accumulated chunks are capped before any join or parse.
 */
export function resolvePilotLayout(
  input: PilotLayoutInput | null | undefined,
  enabled = false,
): PilotLayoutResolution {
  if (!enabled) return fallback('layout_disabled');
  if (!input?.frame) return fallback('layout_absent');

  const frame = input.frame as unknown as {
    schema_version?: unknown;
    text?: unknown;
  };
  if (typeof frame !== 'object' || frame === null) {
    return fallback('layout_invalid_syntax');
  }
  if (frame.schema_version === undefined) return fallback('layout_unversioned');
  if (frame.schema_version !== PILOT_LAYOUT_SCHEMA_VERSION) {
    return fallback('layout_unsupported_version');
  }
  if (typeof frame.text !== 'string') {
    return fallback('layout_invalid_syntax');
  }
  if (frame.text.length > PILOT_LAYOUT_MAX_BYTES) {
    return fallback('layout_too_large');
  }
  if (utf8ByteLength(frame.text) > PILOT_LAYOUT_MAX_BYTES) {
    return fallback('layout_too_large');
  }

  const chunks = input.chunks ?? [frame.text];
  if (!Array.isArray(chunks)) return fallback('layout_invalid_syntax');
  if (chunks.length > PILOT_LAYOUT_MAX_CHUNKS) {
    return fallback('layout_too_large');
  }
  let bytes = 0;
  for (const chunk of chunks) {
    if (typeof chunk !== 'string') return fallback('layout_invalid_syntax');
    if (chunk.length > PILOT_LAYOUT_MAX_BYTES) {
      return fallback('layout_too_large');
    }
    bytes += utf8ByteLength(chunk);
    if (bytes > PILOT_LAYOUT_MAX_BYTES) return fallback('layout_too_large');
  }

  const joined = chunks.join('');
  if (!frame.text.startsWith(joined)) return fallback('layout_chunk_mismatch');
  if (input.final !== true) return fallback('layout_pending_final_bit');
  if (joined !== frame.text) return fallback('layout_chunk_mismatch');

  const parsed = parsePilotLayout(joined);
  return parsed.ok
    ? { active: true, order: parsed.order, rejection: null }
    : fallback(parsed.reason);
}
