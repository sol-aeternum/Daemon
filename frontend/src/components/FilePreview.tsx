"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { Loader2, AlertCircle, FileWarning } from "lucide-react";
import { CsvPreview, HtmlPreview, PdfPreview, DocxPreview } from "@/src/components/previews";
import MarkdownRenderer from "@/src/components/MarkdownRenderer";
import { ensureAuthHeader } from "@/lib/auth";
import { getProtectedMediaUrl } from "@/hooks/useAuthenticatedImageUrl";

interface FilePreviewProps {
  fileUrl: string;
  filename: string;
  format: string;
  fileSize?: number;
}

const MAX_PREVIEW_SIZE = 5 * 1024 * 1024; // 5MB
const PREVIEW_FETCH_TIMEOUT_MS = 20000;

type PreviewContent = {
  type: "text";
  content: string;
} | {
  type: "arrayBuffer";
  content: ArrayBuffer;
} | {
  type: "url";
  content: string;
} | null;

export function FilePreview({ fileUrl, filename, format, fileSize }: FilePreviewProps) {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [content, setContent] = useState<PreviewContent>(null);
  const [hasLoaded, setHasLoaded] = useState(false);

  const normalizedFormat = format.toLowerCase().trim();

  useEffect(() => {
    setContent(null);
    setHasLoaded(false);
    setError(null);
  }, [fileUrl, normalizedFormat]);

  // Check if file is too large to preview
  const isTooLarge = useMemo(() => {
    if (fileSize === undefined) return false;
    return fileSize > MAX_PREVIEW_SIZE;
  }, [fileSize]);

  // Check if format is supported for preview
  const isSupportedFormat = useMemo(() => {
    return ["csv", "md", "html", "pdf", "docx"].includes(normalizedFormat);
  }, [normalizedFormat]);

  const fetchContent = useCallback(async () => {
    if (hasLoaded || !isSupportedFormat || isTooLarge) return;

    const fetchWithTimeout = async (url: string, headers?: Record<string, string>): Promise<Response> => {
      const controller = new AbortController();
      const timeoutId = window.setTimeout(() => controller.abort(), PREVIEW_FETCH_TIMEOUT_MS);
      try {
        const opts: RequestInit = { signal: controller.signal };
        if (headers) opts.headers = headers;
        return await fetch(url, opts);
      } finally {
        window.clearTimeout(timeoutId);
      }
    };

    setIsLoading(true);
    setError(null);

    const getFetchParams = async (fileUrl: string): Promise<{ url: string; headers?: Record<string, string> }> => {
      const protectedUrl = getProtectedMediaUrl(fileUrl);
      if (protectedUrl) {
        const authHeader = await ensureAuthHeader();
        const headers: Record<string, string> = {};
        if (authHeader) headers["Authorization"] = authHeader;
        return { url: protectedUrl, headers };
      }
      return { url: fileUrl };
    };

    try {
      if (normalizedFormat === "pdf") {
        const { url: fetchUrl, headers: fetchHeaders } = await getFetchParams(fileUrl);
        if (fetchHeaders) {
          const response = await fetchWithTimeout(fetchUrl, fetchHeaders);
          if (!response.ok) {
            throw new Error(`Failed to fetch file: ${response.status} ${response.statusText}`);
          }
          const blob = await response.blob();
          const blobUrl = URL.createObjectURL(blob);
          setContent({ type: "url", content: blobUrl });
        } else {
          setContent({ type: "url", content: fetchUrl });
        }
        setHasLoaded(true);
        setIsLoading(false);
        return;
      }

      if (normalizedFormat === "docx") {
        const { url: fetchUrl, headers: fetchHeaders } = await getFetchParams(fileUrl);
        const response = await fetchWithTimeout(fetchUrl, fetchHeaders);
        if (!response.ok) {
          throw new Error(`Failed to fetch file: ${response.status} ${response.statusText}`);
        }
        const buffer = await response.arrayBuffer();
        setContent({ type: "arrayBuffer", content: buffer });
        setHasLoaded(true);
        setIsLoading(false);
        return;
      }

      const { url: fetchUrl, headers: fetchHeaders } = await getFetchParams(fileUrl);
      const response = await fetchWithTimeout(fetchUrl, fetchHeaders);
      if (!response.ok) {
        throw new Error(`Failed to fetch file: ${response.status} ${response.statusText}`);
      }
      const text = await response.text();
      setContent({ type: "text", content: text });
      setHasLoaded(true);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        setError("Preview request timed out. You can still download the file.");
      } else {
        setError(err instanceof Error ? err.message : "Failed to load file content");
      }
    } finally {
      setIsLoading(false);
    }
  }, [fileUrl, normalizedFormat, isSupportedFormat, isTooLarge, hasLoaded]);

  useEffect(() => {
    if (!hasLoaded && !isTooLarge) {
      fetchContent();
    }
  }, [hasLoaded, isTooLarge, fetchContent]);

  // Render the appropriate preview component
  const renderPreview = () => {
    if (!content) return null;

    switch (normalizedFormat) {
      case "csv":
        return content.type === "text" ? (
          <CsvPreview content={content.content} />
        ) : null;
      case "md":
        return content.type === "text" ? (
          <div className="bg-[var(--color-bg-tertiary)] rounded-xl border border-[var(--color-border-primary)] overflow-hidden max-h-[400px]">
            <div className="flex items-center justify-between px-4 py-3 bg-[var(--color-bg-secondary)] border-b border-[var(--color-border-primary)]">
              <span className="text-sm font-medium text-[var(--color-text-secondary)]">
                Markdown Preview
              </span>
            </div>
            <div className="overflow-auto max-h-[340px] p-4">
              <MarkdownRenderer content={content.content} compact={false} />
            </div>
          </div>
        ) : null;
      case "html":
        return content.type === "text" ? (
          <HtmlPreview content={content.content} title={filename} />
        ) : null;
      case "pdf":
        return content.type === "url" ? (
          <PdfPreview url={content.content} filename={filename} />
        ) : null;
      case "docx":
        return content.type === "arrayBuffer" ? (
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
          <p className="text-sm font-medium text-amber-500">File too large to preview</p>
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
      <div className="flex items-start gap-3 p-4 bg-red-500/10 border border-red-500/30 rounded-xl">
        <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
        <div className="flex-1">
          <p className="text-sm font-medium text-red-500">Failed to load preview</p>
          <p className="text-xs text-red-400/80 mt-1">{error}</p>
        </div>
      </div>
    );
  };

  // Loading state
  const renderLoading = () => {
    return (
      <div className="flex items-center justify-center gap-3 p-8 bg-[var(--color-bg-tertiary)] rounded-xl border border-[var(--color-border-primary)]">
        <Loader2 className="w-5 h-5 animate-spin text-[var(--color-accent-primary)]" />
        <span className="text-sm text-[var(--color-text-muted)]">Loading preview...</span>
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
      {isLoading && renderLoading()}
      {error && renderError()}
      {!isLoading && !error && !isTooLarge && isSupportedFormat && renderPreview()}
      {isTooLarge && renderSizeWarning()}
      {!isTooLarge && !isSupportedFormat && renderUnsupported()}
    </div>
  );
}
