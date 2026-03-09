"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { Eye, EyeOff, Loader2, AlertCircle, FileWarning } from "lucide-react";
import { FileDownloadCard } from "@/components/FileDownloadCard";
import { CsvPreview, HtmlPreview, PdfPreview, DocxPreview } from "@/src/components/previews";
import MarkdownRenderer from "@/src/components/MarkdownRenderer";

interface FilePreviewProps {
  fileUrl: string;
  filename: string;
  format: string;
  fileSize?: number;
}

const MAX_PREVIEW_SIZE = 5 * 1024 * 1024; // 5MB
const AUTO_EXPAND_SIZE = 100 * 1024; // 100KB

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
  const [isExpanded, setIsExpanded] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [content, setContent] = useState<PreviewContent>(null);
  const [hasLoaded, setHasLoaded] = useState(false);

  const normalizedFormat = format.toLowerCase().trim();

  // Check if file is too large to preview
  const isTooLarge = useMemo(() => {
    if (fileSize === undefined) return false;
    return fileSize > MAX_PREVIEW_SIZE;
  }, [fileSize]);

  // Check if format is supported for preview
  const isSupportedFormat = useMemo(() => {
    return ["csv", "md", "html", "pdf", "docx"].includes(normalizedFormat);
  }, [normalizedFormat]);

  // Auto-expand for small files
  useEffect(() => {
    if (fileSize !== undefined && fileSize < AUTO_EXPAND_SIZE && isSupportedFormat && !isTooLarge) {
      setIsExpanded(true);
    }
  }, [fileSize, isSupportedFormat, isTooLarge]);

  // Fetch content when expanded
  const fetchContent = useCallback(async () => {
    if (hasLoaded || !isSupportedFormat || isTooLarge) return;

    setIsLoading(true);
    setError(null);

    try {
      // PDF doesn't need content fetching - just pass URL
      if (normalizedFormat === "pdf") {
        setContent({ type: "url", content: fileUrl });
        setHasLoaded(true);
        setIsLoading(false);
        return;
      }

      // DOCX needs ArrayBuffer
      if (normalizedFormat === "docx") {
        const response = await fetch(fileUrl);
        if (!response.ok) {
          throw new Error(`Failed to fetch file: ${response.status} ${response.statusText}`);
        }
        const buffer = await response.arrayBuffer();
        setContent({ type: "arrayBuffer", content: buffer });
        setHasLoaded(true);
        setIsLoading(false);
        return;
      }

      // CSV, MD, HTML need text
      const response = await fetch(fileUrl);
      if (!response.ok) {
        throw new Error(`Failed to fetch file: ${response.status} ${response.statusText}`);
      }
      const text = await response.text();
      setContent({ type: "text", content: text });
      setHasLoaded(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load file content");
    } finally {
      setIsLoading(false);
    }
  }, [fileUrl, normalizedFormat, isSupportedFormat, isTooLarge, hasLoaded]);

  // Trigger fetch when expanded
  useEffect(() => {
    if (isExpanded && !hasLoaded && !isTooLarge) {
      fetchContent();
    }
  }, [isExpanded, hasLoaded, isTooLarge, fetchContent]);

  const togglePreview = () => {
    setIsExpanded((prev) => !prev);
  };

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
              <MarkdownRenderer content={content.content} compact />
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

  const showToggle = isSupportedFormat && !isTooLarge;

  return (
    <div className="flex flex-col gap-3">
      {/* Preview Panel */}
      {isExpanded && (
        <div className="space-y-3">
          {isLoading && renderLoading()}
          {error && renderError()}
          {!isLoading && !error && renderPreview()}
          {isTooLarge && renderSizeWarning()}
          {!isSupportedFormat && renderUnsupported()}
        </div>
      )}

      {/* File Download Card with Toggle */}
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <FileDownloadCard
            filename={filename}
            fileUrl={fileUrl}
            fileSize={fileSize}
            fileType={format}
          />
        </div>

        {/* Preview Toggle Button */}
        {showToggle && (
          <button
            onClick={togglePreview}
            className="flex-shrink-0 flex items-center justify-center w-10 h-10 bg-[var(--color-bg-tertiary)] hover:bg-[var(--color-bg-secondary)] border border-[var(--color-border-primary)] hover:border-[var(--color-border-secondary)] text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] rounded-xl transition-all focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--color-bg-primary)]"
            title={isExpanded ? "Hide preview" : "Show preview"}
            aria-expanded={isExpanded}
            aria-label={isExpanded ? "Hide file preview" : "Show file preview"}
          >
            {isExpanded ? (
              <EyeOff className="w-5 h-5" />
            ) : (
              <Eye className="w-5 h-5" />
            )}
          </button>
        )}
      </div>
    </div>
  );
}
