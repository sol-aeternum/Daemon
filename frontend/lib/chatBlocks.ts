import { useEffect, useMemo, useState } from 'react';
import type { ChatEvent } from './events';
import { isAdvisorScoped, parseToolResultPayload } from './toolActivity';
import type { TextFileKind } from './textDownload';

/**
 * A conversation-scoped chat block produced by the `render_block` tool
 * (docs/ARTIFACT_PRESENTATION_DESIGN.md). The source comes from the tool
 * call's arguments; the result supplies the server-assigned identity. A block
 * is shown only when the call content's SHA-256 matches the result, so a
 * mispaired call can never display another block's source.
 */
export interface ChatBlock {
  key: string;
  blockId: string;
  version: number;
  kind: TextFileKind;
  title: string;
  display: 'inline' | 'panel';
  bytes: number;
  content: string;
  language?: string;
}

const KINDS = new Set<TextFileKind>([
  'html',
  'svg',
  'markdown',
  'code',
  'csv',
  'json',
]);
const BLOCK_ID = /^blk_[0-9a-f]{16}$/;
const SHA256 = /^[0-9a-f]{64}$/;
/** Client re-check of the server's inline limit (design D4). */
export const MAX_INLINE_BLOCK_BYTES = 48 * 1024;

interface BlockResult {
  blockId: string;
  version: number;
  kind: TextFileKind;
  title: string;
  display: 'inline' | 'panel';
  bytes: number;
  sha256: string;
}

interface BlockCall {
  content: string;
  language?: string;
}

export function chatBlockKey(blockId: string, version: number): string {
  return `${blockId}:v${version}`;
}

function readResult(event: ChatEvent): BlockResult | null {
  if (event.type !== 'tool_result' || event.name !== 'render_block') {
    return null;
  }
  const payload = parseToolResultPayload(event.result);
  if (!payload || payload.success !== true) return null;
  const data = payload.data;
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
  const record = data as Record<string, unknown>;
  const { block_id, version, kind, title, display, bytes, sha256 } = record;
  if (
    typeof block_id !== 'string' ||
    !BLOCK_ID.test(block_id) ||
    typeof version !== 'number' ||
    !Number.isInteger(version) ||
    version < 1 ||
    typeof kind !== 'string' ||
    !KINDS.has(kind as TextFileKind) ||
    typeof sha256 !== 'string' ||
    !SHA256.test(sha256)
  ) {
    return null;
  }
  return {
    blockId: block_id,
    version,
    kind: kind as TextFileKind,
    title:
      typeof title === 'string' && title.trim()
        ? title.trim().slice(0, 120)
        : 'Untitled',
    display: display === 'inline' ? 'inline' : 'panel',
    bytes: typeof bytes === 'number' && bytes >= 0 ? bytes : 0,
    sha256,
  };
}

function readCall(event: ChatEvent): BlockCall | null {
  if (event.type !== 'tool_call' || event.name !== 'render_block') return null;
  const args = event.arguments;
  if (!args || typeof args.content !== 'string') return null;
  return {
    content: args.content,
    language: typeof args.language === 'string' ? args.language : undefined,
  };
}

async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(text),
  );
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, '0'),
  ).join('');
}

/** Resolve verified blocks in event order, deduplicated by id and version. */
export async function resolveChatBlocks(
  events: ChatEvent[],
): Promise<ChatBlock[]> {
  const calls: BlockCall[] = [];
  const results: BlockResult[] = [];
  for (const event of events) {
    if (isAdvisorScoped(event)) continue;
    const call = readCall(event);
    if (call) calls.push(call);
    const result = readResult(event);
    if (result) results.push(result);
  }
  if (results.length === 0) return [];
  const byDigest = new Map<string, BlockCall>();
  for (const call of calls) {
    const digest = await sha256Hex(call.content);
    if (!byDigest.has(digest)) byDigest.set(digest, call);
  }
  const blocks = new Map<string, ChatBlock>();
  for (const result of results) {
    const call = byDigest.get(result.sha256);
    if (!call) continue;
    const key = chatBlockKey(result.blockId, result.version);
    if (blocks.has(key)) continue;
    const inlineAllowed =
      (result.kind === 'html' || result.kind === 'svg') &&
      new TextEncoder().encode(call.content).length <= MAX_INLINE_BLOCK_BYTES;
    blocks.set(key, {
      key,
      blockId: result.blockId,
      version: result.version,
      kind: result.kind,
      title: result.title,
      display:
        result.display === 'inline' && inlineAllowed ? 'inline' : 'panel',
      bytes: result.bytes,
      content: call.content,
      language: result.kind === 'code' ? call.language : undefined,
    });
  }
  return [...blocks.values()];
}

/** Verified blocks for a set of events; empty until hashing completes. */
export function useChatBlocks(events: ChatEvent[]): ChatBlock[] {
  const [blocks, setBlocks] = useState<ChatBlock[]>([]);
  const signature = useMemo(
    () =>
      JSON.stringify(
        events.filter(
          (event) =>
            (event.type === 'tool_call' || event.type === 'tool_result') &&
            event.name === 'render_block' &&
            !isAdvisorScoped(event),
        ),
      ),
    [events],
  );
  useEffect(() => {
    let cancelled = false;
    resolveChatBlocks(JSON.parse(signature) as ChatEvent[]).then(
      (next) => {
        if (!cancelled) setBlocks(next);
      },
      () => {
        if (!cancelled) setBlocks([]);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [signature]);
  return blocks;
}
