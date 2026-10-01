'use client';

import { useState, useEffect } from 'react';
import { Loader2, AlertCircle, FileWarning } from 'lucide-react';
import {
  CsvPreview,
  HtmlPreview,
  PdfPreview,
  DocxPreview,
} from '@/src/components/previews';
import MarkdownRenderer from '@/src/components/MarkdownRenderer';
import {
  ensureAuthHeader,
  getAuthGeneration,
  subscribeAuthGeneration,
} from '@/lib/auth';
import { useAuthGeneration } from '@/hooks/useAuthGeneration';
import { getProtectedMediaUrl } from '@/hooks/useAuthenticatedImageUrl';

interface FilePreviewProps {
  fileUrl: string;
  filename: string;
  format: string;
  fileSize?: number;
}

const MAX_PREVIEW_SIZE = 5 * 1024 * 1024; // 5MB
const PREVIEW_FETCH_TIMEOUT_MS = 20000;

async function readPreviewBlob(
  response: Response,
  signal: AbortSignal,
): Promise<Blob> {
  if (!response.body) return response.blob();
  const reader = response.body.getReader();
  const chunks: ArrayBuffer[] = [];
  let size = 0;
  try {
    while (true) {
      signal.throwIfAborted();
      const { value, done } = await reader.read();
      signal.throwIfAborted();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_PREVIEW_SIZE) {
        await reader.cancel();
        throw new Error(
          'File exceeds the 5MB preview limit. Download to view.',
        );
      }
      chunks.push(Uint8Array.from(value).buffer);
    }
    return new Blob(chunks, {
      type: response.headers.get('content-type') || '',
    });
  } finally {
    reader.releaseLock();
  }
}

type PreviewContent =
  | {
      type: 'text';
      content: string;
    }
  | {
      type: 'arrayBuffer';
      content: ArrayBuffer;
    }
  | {
      type: 'url';
      content: string;
    }
  | null;

export function FilePreview(props: FilePreviewProps) {
  const generation = useAuthGeneration();
  // A new selection must never render the preceding file, even for one frame.
  return (
    <SelectedFilePreview
      key={`${generation}:${props.fileUrl}:${props.format}:${props.fileSize}`}
      {...props}
    />
  );
}

function SelectedFilePreview({
  fileUrl,
  filename,
  format,
  fileSize,
}: FilePreviewProps) {
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [content, setContent] = useState<PreviewContent>(null);

  const normalizedFormat = format.toLowerCase().trim();

  const isTooLarge = fileSize !== undefined && fileSize > MAX_PREVIEW_SIZE;
  const isSupportedFormat = ['csv', 'md', 'html', 'pdf', 'docx'].includes(
    normalizedFormat,
  );
  useEffect(() => {
    if (isTooLarge || !isSupportedFormat) return;
    let disposed = false;
    let objectUrl: string | null = null;
    const controller = new AbortController();
    const generation = getAuthGeneration();
    const unsubscribe = subscribeAuthGeneration(() => controller.abort());
    const timeout = window.setTimeout(() => {
      controller.abort();
      if (!disposed) {
        setError('Preview request timed out. Try downloading the file.');
        setIsLoading(false);
      }
    }, PREVIEW_FETCH_TIMEOUT_MS);
    const active = () =>
      !disposed &&
      !controller.signal.aborted &&
      generation === getAuthGeneration();
    async function load() {
      try {
        const protectedUrl = getProtectedMediaUrl(fileUrl);
        const headers: Record<string, string> = {};
        if (protectedUrl) {
          const auth = await ensureAuthHeader();
          if (!active()) return;
          if (!auth) throw new Error('Sign in again to preview this file.');
          headers.Authorization = auth;
        }
        // Public PDF rendering retains its existing iframe path. Protected PDFs
        // are fetched with auth and rendered through a temporary object URL.
        if (normalizedFormat === 'pdf' && !protectedUrl) {
          if (active()) setContent({ type: 'url', content: fileUrl });
          return;
        }
        const response = await fetch(protectedUrl ?? fileUrl, {
          headers,
          signal: controller.signal,
        });
        if (!active()) return;
        if (!response.ok) {
          throw new Error(
            response.status === 404
              ? 'This file is unavailable (missing or expired). Your conversation is still here.'
              : response.status === 401 || response.status === 403
                ? 'You do not have access to this file. Sign in again or check the selected conversation.'
                : `Preview failed (${response.status}). Try again later.`,
          );
        }
        if (Number(response.headers.get('content-length')) > MAX_PREVIEW_SIZE) {
          controller.abort();
          throw new Error(
            'File exceeds the 5MB preview limit. Download to view.',
          );
        }
        const blob = await readPreviewBlob(response, controller.signal);
        if (!active()) return;
        if (blob.size > MAX_PREVIEW_SIZE)
          throw new Error(
            'File exceeds the 5MB preview limit. Download to view.',
          );
        if (normalizedFormat === 'pdf') {
          objectUrl = URL.createObjectURL(blob);
          setContent({ type: 'url', content: objectUrl });
        } else if (normalizedFormat === 'docx') {
          const buffer = await blob.arrayBuffer();
          if (active()) setContent({ type: 'arrayBuffer', content: buffer });
        } else {
          const text = await blob.text();
          if (active()) setContent({ type: 'text', content: text });
        }
      } catch (err) {
        if (
          !disposed &&
          !(err instanceof DOMException && err.name === 'AbortError')
        ) {
          setError(
            err instanceof Error ? err.message : 'Failed to load preview.',
          );
        }
      } finally {
        window.clearTimeout(timeout);
        if (!disposed) setIsLoading(false);
      }
    }
    void load();
    return () => {
      disposed = true;
      controller.abort();
      unsubscribe();
      window.clearTimeout(timeout);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [fileUrl, normalizedFormat, isSupportedFormat, isTooLarge]);

  // Render the appropriate preview component
  const renderPreview = () => {
    if (!content) return null;

    switch (normalizedFormat) {
      case 'csv':
        return content.type === 'text' ? (
          <CsvPreview content={content.content} />
        ) : null;
      case 'md':
        return content.type === 'text' ? (
          <div className="bg-[var(--color-bg-tertiary)] rounded-xl border border-[var(--color-border-primary)] overflow-hidden max-h-file-preview">
            <div className="flex items-center justify-between px-4 py-3 bg-[var(--color-bg-secondary)] border-b border-[var(--color-border-primary)]">
              <span className="text-sm font-medium text-[var(--color-text-secondary)]">
                Markdown Preview
              </span>
            </div>
            <div className="overflow-auto max-h-file-content p-4">
              <MarkdownRenderer content={content.content} compact={false} />
            </div>
          </div>
        ) : null;
      case 'html':
        return content.type === 'text' ? (
          <HtmlPreview content={content.content} title={filename} />
        ) : null;
      case 'pdf':
        return content.type === 'url' ? (
          <PdfPreview url={content.content} filename={filename} />
        ) : null;
      case 'docx':
        return content.type === 'arrayBuffer' ? (
          <DocxPreview content={content.content} filename={filename} />
        ) : null;
      default:
        return null;
    }
  };

  // Size warning for files that can't be previewed
  const renderSizeWarning = () => {
    if (!isTooLarge) return null;

    return (
      <div className="flex items-start gap-3 p-4 bg-amber-500/10 border border-amber-500/30 rounded-xl">
        <FileWarning className="w-5 h-5 text-amber-500 flex-shrink-0 mt-0.5" />
        <div className="flex-1">
          <p className="text-sm font-medium text-amber-500">
            File too large to preview
          </p>
          <p className="text-xs text-amber-400/80 mt-1">
            This file exceeds the 5MB preview limit. Download to view.
          </p>
        </div>
      </div>
    );
  };

  // Error fallback UI
  const renderError = () => {
    if (!error) return null;

    return (
      <div
        role="alert"
        className="flex items-start gap-3 p-4 bg-[var(--color-bg-tertiary)] border border-[var(--color-status-error)] rounded-xl"
      >
        <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
        <div className="flex-1">
          <p className="text-sm font-medium text-[var(--color-text-primary)]">
            Failed to load preview
          </p>
          <p className="text-sm text-[var(--color-text-secondary)] mt-1">
            {error}
          </p>
        </div>
      </div>
    );
  };

  // Loading state
  const renderLoading = () => {
    return (
      <div className="flex items-center justify-center gap-3 p-8 bg-[var(--color-bg-tertiary)] rounded-xl border border-[var(--color-border-primary)]">
        <Loader2 className="w-5 h-5 animate-spin text-[var(--color-accent-primary)]" />
        <span className="text-sm text-[var(--color-text-muted)]">
          Loading preview...
        </span>
      </div>
    );
  };

  // Unsupported format message
  const renderUnsupported = () => {
    if (isSupportedFormat) return null;

    return (
      <div className="flex items-start gap-3 p-4 bg-[var(--color-bg-tertiary)] border border-[var(--color-border-primary)] rounded-xl">
        <FileWarning className="w-5 h-5 text-[var(--color-text-muted)] flex-shrink-0 mt-0.5" />
        <div className="flex-1">
          <p className="text-sm font-medium text-[var(--color-text-secondary)]">
            Preview not available
          </p>
          <p className="text-xs text-[var(--color-text-muted)] mt-1">
            This file format ({format}) is not supported for preview.
          </p>
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-3">
      {isLoading && isSupportedFormat && !isTooLarge && renderLoading()}
      {error && renderError()}
      {!isLoading &&
        !error &&
        !isTooLarge &&
        isSupportedFormat &&
        renderPreview()}
      {isTooLarge && renderSizeWarning()}
      {!isTooLarge && !isSupportedFormat && renderUnsupported()}
    </div>
  );
}
