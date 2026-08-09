'use client';

import { useState, useEffect, useCallback } from 'react';
import { Loader2, AlertCircle, FileText, Download } from 'lucide-react';

interface PdfPreviewProps {
  url: string;
  filename?: string;
}

export function PdfPreview({ url, filename }: PdfPreviewProps) {
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;
    let blobUrl: string | null = null;

    const loadPdf = async () => {
      setIsLoading(true);
      setError(null);

      try {
        // If it's already a data URL or blob URL, use it directly
        if (url.startsWith('data:') || url.startsWith('blob:')) {
          if (isMounted) {
            setObjectUrl(url);
            setIsLoading(false);
          }
          return;
        }

        // Otherwise, fetch and create a blob URL
        const response = await fetch(url);
        if (!response.ok) {
          throw new Error(
            `Failed to load PDF: ${response.status} ${response.statusText}`,
          );
        }

        const blob = await response.blob();
        blobUrl = URL.createObjectURL(blob);

        if (isMounted) {
          setObjectUrl(blobUrl);
          setIsLoading(false);
        } else {
          // Clean up if unmounted during fetch
          URL.revokeObjectURL(blobUrl);
        }
      } catch (err) {
        if (isMounted) {
          setError(err instanceof Error ? err.message : 'Failed to load PDF');
          setIsLoading(false);
        }
      }
    };

    loadPdf();

    return () => {
      isMounted = false;
      if (blobUrl) {
        URL.revokeObjectURL(blobUrl);
      }
    };
  }, [url]);

  const handleDownload = useCallback(() => {
    if (!objectUrl) return;
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = filename || 'document.pdf';
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }, [objectUrl, filename]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center gap-3 p-8 bg-[var(--color-bg-tertiary)] rounded-xl border border-[var(--color-border-primary)] max-h-[400px]">
        <Loader2 className="w-5 h-5 animate-spin text-[var(--color-accent-primary)]" />
        <span className="text-sm text-[var(--color-text-muted)]">
          Loading PDF...
        </span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-start gap-3 p-4 bg-red-500/10 border border-red-500/30 rounded-xl max-h-[400px]">
        <div className="flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-sm font-medium text-red-500">
              Failed to load PDF
            </p>
            <p className="text-xs text-red-400 mt-1">{error}</p>
          </div>
        </div>
        <button
          onClick={handleDownload}
          className="flex items-center gap-2 px-3 py-1.5 bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-tertiary)] text-[var(--color-text-secondary)] text-xs font-medium rounded-lg transition-colors"
        >
          <Download className="w-3 h-3" />
          Try Download
        </button>
      </div>
    );
  }

  return (
    <div className="bg-[var(--color-bg-tertiary)] rounded-xl border border-[var(--color-border-primary)] overflow-hidden max-h-[400px]">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-[var(--color-bg-secondary)] border-b border-[var(--color-border-primary)]">
        <div className="flex items-center gap-2">
          <FileText className="w-4 h-4 text-red-400" />
          <span className="text-sm font-medium text-[var(--color-text-secondary)]">
            {filename || 'PDF Preview'}
          </span>
        </div>
        <button
          onClick={handleDownload}
          className="flex items-center gap-1.5 px-2.5 py-1 bg-[var(--color-bg-tertiary)] hover:bg-[var(--color-accent-primary)] hover:text-white text-[var(--color-text-muted)] text-xs font-medium rounded-lg transition-colors"
        >
          <Download className="w-3 h-3" />
          Download
        </button>
      </div>

      {objectUrl ? (
        <div className="overflow-auto max-h-[340px]">
          <iframe
            src={objectUrl}
            title={filename || 'PDF Preview'}
            className="w-full min-h-[340px] bg-[var(--color-bg-secondary)]"
          />
        </div>
      ) : null}
    </div>
  );
}
