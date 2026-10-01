// Pure helpers for the retained web-snapshot Sources UI. The component owns
// all network orchestration; nothing here performs requests. Runtime
// validation fails closed to generic client-side errors so server fragments
// never reach rendered output, and retained page text is never displayed.
import { isSnapshotId } from '@/lib/webSnapshotPaths';

export const SNAPSHOT_LIST_LIMIT = 20;
export const SNAPSHOT_EXPORT_MAX_BYTES = 8 * 1024 * 1024;

/** Body streamed past the export byte ceiling; distinct from other errors
 * only so the caller can show the matching generic message. */
export class SnapshotExportTooLargeError extends Error {}

export interface SnapshotRow {
  id: string;
  title: string;
  linkHref: string | null;
  contentChars: number;
  contentBytes: number;
  retrievedLabel: string;
  expiresLabel: string;
  expired: boolean;
  expiresAt: number;
}

export interface SnapshotPage {
  rows: SnapshotRow[];
  total: number;
  offset: number;
  limit: number;
}

/** Safe HTTP(S) absolute links only; used for metadata links, never requests. */
export function snapshotLinkHref(value: unknown): string | null {
  if (typeof value !== 'string' || !value.trim()) return null;
  try {
    const url = new URL(value);
    if (
      !['http:', 'https:'].includes(url.protocol) ||
      url.username ||
      url.password
    )
      return null;
    return url.href;
  } catch {
    return null;
  }
}

export function snapshotIsoLabel(date: Date): string {
  const iso = date.toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
}

function toDate(value: unknown): Date | null {
  if (typeof value !== 'string' || !value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

const isNonNegativeNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;

/** Runtime metadata-page validation. Throws only on contract violations; the
 * caller maps any failure to one generic message. */
export function parseSnapshotListPage(
  raw: unknown,
  conversationId: string,
  expectedOffset?: number,
): SnapshotPage {
  if (!raw || typeof raw !== 'object')
    throw new Error('Invalid snapshot page.');
  const data = raw as Record<string, unknown>;
  const items = data.snapshots;
  const count = data.total;
  const offset = data.offset;
  const limit = data.limit;
  if (
    !Array.isArray(items) ||
    !isNonNegativeNumber(count) ||
    !isNonNegativeNumber(offset) ||
    !isNonNegativeNumber(limit) ||
    limit !== SNAPSHOT_LIST_LIMIT ||
    items.length > SNAPSHOT_LIST_LIMIT ||
    (expectedOffset !== undefined && offset !== expectedOffset) ||
    (isNonNegativeNumber(count) && items.length > count)
  )
    throw new Error('Invalid snapshot page.');
  const rows = items.map((item) => parseSnapshotRow(item, conversationId));
  if (new Set(rows.map((row) => row.id)).size !== rows.length)
    throw new Error('Invalid snapshot page.');
  return {
    rows,
    total: count,
    offset,
    limit,
  };
}

function parseSnapshotRow(item: unknown, conversationId: string): SnapshotRow {
  if (!item || typeof item !== 'object') throw new Error('Invalid snapshot.');
  const row = item as Record<string, unknown>;
  const id = typeof row.id === 'string' && isSnapshotId(row.id) ? row.id : null;
  const retrievedAt = toDate(row.retrieved_at);
  const expiresAt = toDate(row.expires_at);
  const contentChars = row.content_chars;
  const contentBytes = row.content_bytes;
  if (
    !id ||
    row.conversation_id !== conversationId ||
    !retrievedAt ||
    !expiresAt ||
    typeof row.source_url !== 'string' ||
    typeof row.final_url !== 'string' ||
    typeof row.title !== 'string' ||
    !isNonNegativeNumber(contentChars) ||
    !isNonNegativeNumber(contentBytes)
  )
    throw new Error('Invalid snapshot.');
  const linkHref = snapshotLinkHref(row.source_url);
  return {
    id,
    title: row.title,
    linkHref,
    contentChars,
    contentBytes,
    retrievedLabel: `Retrieved ${snapshotIsoLabel(retrievedAt)}`,
    expiresLabel: `Expires ${snapshotIsoLabel(expiresAt)}`,
    expired: expiresAt.getTime() <= Date.now(),
    expiresAt: expiresAt.getTime(),
  };
}

/** Opaque, validated-UUID-only export filename; never page title/url derived. */
export function snapshotExportFilename(snapshotId: string): string {
  if (!isSnapshotId(snapshotId))
    throw new Error('Snapshot export unavailable.');
  return `web-snapshot-${snapshotId}.json`;
}

// Abort also settles non-fetch awaits (auth refresh, mocked/uncooperative body
// reads). The underlying operation may finish, but cannot revive this request.
export function awaitSnapshotOperation<T>(
  operation: Promise<T>,
  signal: AbortSignal,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const aborted = () => {
      signal.removeEventListener('abort', aborted);
      reject(new DOMException('Snapshot operation aborted', 'AbortError'));
    };
    if (signal.aborted) {
      operation.catch(() => {});
      aborted();
      return;
    }
    signal.addEventListener('abort', aborted, { once: true });
    operation.then(
      (value) => {
        signal.removeEventListener('abort', aborted);
        if (signal.aborted) aborted();
        else resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener('abort', aborted);
        reject(error);
      },
    );
  });
}

/**
 * Stream the export body with a hard byte ceiling. Never allocates an
 * unbounded blob; aborting/refusing happens before the data is retained.
 */
export async function readBoundedExport(
  response: Response,
  maxBytes: number,
  signal?: AbortSignal,
): Promise<string> {
  const body = response.body;
  if (!body) throw new Error('Snapshot export unavailable.');
  if (signal?.aborted) throw new DOMException('Export aborted', 'AbortError');
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  let complete = false;
  try {
    for (;;) {
      if (signal?.aborted)
        throw new DOMException('Export aborted', 'AbortError');
      const { done, value } = signal
        ? await awaitSnapshotOperation(reader.read(), signal)
        : await reader.read();
      if (signal?.aborted)
        throw new DOMException('Export aborted', 'AbortError');
      if (done) {
        complete = true;
        break;
      }
      const received = value ? value.byteLength : 0;
      total += received;
      if (total > maxBytes) {
        throw new SnapshotExportTooLargeError(
          'Snapshot export is larger than the download limit.',
        );
      }
      if (value) chunks.push(value);
    }
  } finally {
    // Cancellation itself may be uncooperative: don't hold a private download
    // or the UI busy state waiting for it to settle.
    if (!complete) void reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  const all = new Uint8Array(total);
  let at = 0;
  for (const chunk of chunks) {
    all.set(chunk, at);
    at += chunk.byteLength;
  }
  return new TextDecoder('utf-8').decode(all);
}
