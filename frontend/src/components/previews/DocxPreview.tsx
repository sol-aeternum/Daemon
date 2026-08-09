'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { Loader2, AlertCircle, FileText, Download } from 'lucide-react';

interface DocxPreviewProps {
  content: ArrayBuffer | string;
  filename?: string;
}

const DOCX_RENDER_TIMEOUT_MS = 20000;
const DOCX_CLASS_NAME = 'docx-preview';

function readCspNonce(): string | undefined {
  const meta = document.querySelector<HTMLMetaElement>(
    'meta[name="csp-nonce"]',
  );
  return meta?.content || undefined;
}

export function DocxPreview({ content, filename }: DocxPreviewProps) {
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const runIdRef = useRef(0);

  const fitDocxToContainer = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;

    const pages = Array.from(
      container.querySelectorAll<HTMLElement>(
        `section.${DOCX_CLASS_NAME}, section.docx, .${DOCX_CLASS_NAME}`,
      ),
    );
    if (!pages.length) return;

    const availableWidth = Math.max(container.clientWidth - 16, 0);
    if (!availableWidth) return;

    const supportsZoom =
      typeof CSS !== 'undefined' &&
      typeof CSS.supports === 'function' &&
      CSS.supports('zoom', '1');

    const naturalSizes = pages.map((page) => {
      page.style.setProperty('zoom', '1');
      page.style.removeProperty('transform');
      page.style.removeProperty('transform-origin');
      page.style.removeProperty('height');
      page.style.removeProperty('max-width');

      const naturalWidth = page.scrollWidth;
      const naturalHeight = page.scrollHeight;

      return { page, naturalWidth, naturalHeight };
    });

    const widestPage = Math.max(
      ...naturalSizes.map(({ naturalWidth }) => naturalWidth),
    );
    if (!widestPage) return;

    const scale = Math.min(1, availableWidth / widestPage);
    naturalSizes.forEach(({ page, naturalWidth, naturalHeight }) => {
      page.style.setProperty('marginLeft', 'auto');
      page.style.setProperty('marginRight', 'auto');
      page.style.setProperty('transformOrigin', 'top center');

      if (supportsZoom) {
        page.style.setProperty('zoom', String(scale));
        page.style.removeProperty('transform');
        page.style.removeProperty('height');
      } else {
        page.style.setProperty('zoom', '1');
        page.style.setProperty('transform', `scale(${scale})`);
        page.style.setProperty(
          'height',
          `${Math.ceil(naturalHeight * scale)}px`,
        );
      }
    });
  }, []);

  const createBlobUrl = useCallback((data: ArrayBuffer | string): string => {
    let blob: Blob;
    if (typeof data === 'string') {
      // If it's base64, decode it
      if (data.includes('base64,')) {
        const base64 = data.split('base64,')[1];
        const binaryString = atob(base64);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
          bytes[i] = binaryString.charCodeAt(i);
        }
        blob = new Blob([bytes], {
          type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        });
      } else {
        blob = new Blob([data], { type: 'text/plain' });
      }
    } else {
      blob = new Blob([data], {
        type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      });
    }
    return URL.createObjectURL(blob);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let currentBlobUrl: string | null = null;
    const runId = ++runIdRef.current;

    const renderDocx = async () => {
      if (!containerRef.current) {
        if (!cancelled && runIdRef.current === runId) {
          setError('DOCX preview container not available.');
          setIsLoading(false);
        }
        return;
      }

      setIsLoading(true);
      setError(null);
      let timeoutId: number | null = null;

      try {
        // Convert content to ArrayBuffer if needed
        let docxData: ArrayBuffer;
        if (typeof content === 'string') {
          if (content.includes('base64,')) {
            const base64 = content.split('base64,')[1];
            const binaryString = atob(base64);
            const bytes = new Uint8Array(binaryString.length);
            for (let i = 0; i < binaryString.length; i++) {
              bytes[i] = binaryString.charCodeAt(i);
            }
            docxData = bytes.buffer;
          } else {
            // Assume it's a URL - fetch it
            const response = await fetch(content);
            if (!response.ok) {
              throw new Error(
                `Failed to fetch DOCX: ${response.status} ${response.statusText}`,
              );
            }
            docxData = await response.arrayBuffer();
          }
        } else {
          docxData = content;
        }

        // Create blob URL for download
        currentBlobUrl = createBlobUrl(docxData);
        if (!cancelled && runIdRef.current === runId) {
          setBlobUrl(currentBlobUrl);
        }

        // Clear container
        if (containerRef.current) {
          containerRef.current.innerHTML = '';
        }

        // Render DOCX
        const { renderAsync } = await import('docx-preview');
        // docx-preview writes generated <style> elements into its optional
        // style container. Keep that container detached until every style has
        // the response nonce; CSP evaluates a style when it is attached, so
        // adding the nonce afterwards is too late.
        const styleContainer = document.createElement('div');

        await Promise.race([
          Promise.resolve().then(() =>
            renderAsync(docxData, containerRef.current!, styleContainer, {
              className: DOCX_CLASS_NAME,
              inWrapper: true,
            }),
          ),
          new Promise<never>((_, reject) => {
            timeoutId = window.setTimeout(() => {
              reject(
                new Error(
                  'DOCX preview timed out. You can still download the file.',
                ),
              );
            }, DOCX_RENDER_TIMEOUT_MS);
          }),
        ]);

        if (cancelled || runIdRef.current !== runId || !containerRef.current) {
          return;
        }

        const nonce = readCspNonce();
        if (nonce) {
          styleContainer.querySelectorAll('style').forEach((style) => {
            style.setAttribute('nonce', nonce);
          });
        }
        containerRef.current.prepend(...Array.from(styleContainer.childNodes));

        requestAnimationFrame(() => {
          fitDocxToContainer();
          window.setTimeout(() => fitDocxToContainer(), 120);
        });

        if (!cancelled && runIdRef.current === runId) {
          setIsLoading(false);
        }
      } catch (err) {
        if (!cancelled && runIdRef.current === runId) {
          setError(
            err instanceof Error ? err.message : 'Failed to render DOCX',
          );
          setIsLoading(false);
        }
      } finally {
        if (timeoutId !== null) {
          window.clearTimeout(timeoutId);
        }
      }
    };

    renderDocx();

    return () => {
      cancelled = true;
      if (currentBlobUrl) {
        URL.revokeObjectURL(currentBlobUrl);
      }
    };
  }, [content, createBlobUrl, fitDocxToContainer]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === 'undefined') return;

    const observer = new ResizeObserver(() => {
      fitDocxToContainer();
    });

    observer.observe(container);

    return () => {
      observer.disconnect();
    };
  }, [fitDocxToContainer]);

  const handleDownload = useCallback(() => {
    if (!blobUrl) return;

    const link = document.createElement('a');
    link.href = blobUrl;
    link.download = filename || 'document.docx';
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }, [blobUrl, filename]);

  return (
    <div className="bg-[var(--color-bg-tertiary)] rounded-xl border border-[var(--color-border-primary)] overflow-hidden max-h-[400px]">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-[var(--color-bg-secondary)] border-b border-[var(--color-border-primary)]">
        <div className="flex items-center gap-2">
          <FileText className="w-4 h-4 text-blue-400" />
          <span className="text-sm font-medium text-[var(--color-text-secondary)]">
            {filename || 'DOCX Preview'}
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

      {/* DOCX render container */}
      <div className="relative overflow-auto max-h-[340px] p-4">
        <div
          ref={containerRef}
          className="docx-preview-container bg-white rounded shadow-sm min-h-[200px]"
        />

        {isLoading && (
          <div className="absolute inset-4 flex items-center justify-center gap-3 bg-[var(--color-bg-tertiary)]/95 rounded-xl border border-[var(--color-border-primary)]">
            <Loader2 className="w-5 h-5 animate-spin text-[var(--color-accent-primary)]" />
            <span className="text-sm text-[var(--color-text-muted)]">
              Rendering DOCX...
            </span>
          </div>
        )}
      </div>

      {error && (
        <div className="m-4 mt-0 flex flex-col items-start gap-3 p-4 bg-red-500/10 border border-red-500/30 rounded-xl">
          <div className="flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="text-sm font-medium text-red-500">
                Failed to render DOCX
              </p>
              <p className="text-xs text-red-400 mt-1">{error}</p>
            </div>
          </div>
          {blobUrl && (
            <button
              onClick={handleDownload}
              className="flex items-center gap-2 px-3 py-1.5 bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-tertiary)] text-[var(--color-text-secondary)] text-xs font-medium rounded-lg transition-colors"
            >
              <Download className="w-3 h-3" />
              Try Download
            </button>
          )}
        </div>
      )}
    </div>
  );
}
