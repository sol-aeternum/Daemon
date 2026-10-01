import type { ChatEvent } from './events';
import { extractToolFailure, parseToolResultPayload } from './toolActivity';

export interface ConversationOutput {
  fileUrl: string;
  filename: string;
  fileType?: string;
  fileSize?: number;
}

export interface DetailTurn {
  id: string;
  events: ChatEvent[];
  running: boolean;
  stopped: boolean;
}

/** Only actual successful returned files qualify; no guessed output paths. */
export function getConversationOutputs(
  events: ChatEvent[],
): ConversationOutput[] {
  const outputs = new Map<string, ConversationOutput>();
  for (const event of events) {
    if (event.type !== 'tool_result') continue;
    const payload = parseToolResultPayload(event.result);
    if (!payload || extractToolFailure(payload)) continue;
    const nested = payload.data;
    const data =
      nested && typeof nested === 'object' && !Array.isArray(nested)
        ? (nested as Record<string, unknown>)
        : payload;
    const fileUrl = data.file_url ?? payload.file_url;
    if (typeof fileUrl !== 'string' || !fileUrl.startsWith('/generated-files/'))
      continue;
    const rawFilename = data.filename ?? payload.filename;
    const rawFormat = data.format ?? payload.format;
    const rawSize = data.file_size ?? payload.file_size;
    const filename =
      typeof rawFilename === 'string' && rawFilename
        ? rawFilename
        : fileUrl.split('/').pop() || 'download';
    outputs.set(fileUrl, {
      fileUrl,
      filename,
      fileType:
        typeof rawFormat === 'string' ? rawFormat : filename.split('.').pop(),
      fileSize:
        typeof rawSize === 'number' && rawSize >= 0 ? rawSize : undefined,
    });
  }
  return [...outputs.values()];
}
