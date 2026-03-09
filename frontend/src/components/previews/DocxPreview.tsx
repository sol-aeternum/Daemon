"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { Loader2, AlertCircle, FileText, Download } from "lucide-react";
import { renderAsync } from "docx-preview";

interface DocxPreviewProps {
  content: ArrayBuffer | string;
  filename?: string;
}

export function DocxPreview({ content, filename }: DocxPreviewProps) {
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const renderAttempted = useRef(false);

  const createBlobUrl = useCallback((data: ArrayBuffer | string): string => {
    let blob: Blob;
    if (typeof data === "string") {
      // If it's base64, decode it
      if (data.includes("base64,")) {
        const base64 = data.split("base64,")[1];
        const binaryString = atob(base64);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
          bytes[i] = binaryString.charCodeAt(i);
        }
        blob = new Blob([bytes], {
          type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        });
      } else {
        blob = new Blob([data], { type: "text/plain" });
      }
    } else {
      blob = new Blob([data], {
        type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      });
    }
    return URL.createObjectURL(blob);
  }, []);

  useEffect(() => {
    let isMounted = true;
    let currentBlobUrl: string | null = null;

    const renderDocx = async () => {
      if (!containerRef.current || renderAttempted.current) return;

      setIsLoading(true);
      setError(null);
      renderAttempted.current = true;

      try {
        // Convert content to ArrayBuffer if needed
        let docxData: ArrayBuffer;
        if (typeof content === "string") {
          if (content.includes("base64,")) {
            const base64 = content.split("base64,")[1];
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
              throw new Error(`Failed to fetch DOCX: ${response.status} ${response.statusText}`);
            }
            docxData = await response.arrayBuffer();
          }
        } else {
          docxData = content;
        }

        // Create blob URL for download
        currentBlobUrl = createBlobUrl(docxData);
        if (isMounted) {
          setBlobUrl(currentBlobUrl);
        }

        // Clear container
        if (containerRef.current) {
          containerRef.current.innerHTML = "";
        }

        // Render DOCX
        await renderAsync(docxData, containerRef.current!, undefined, {
          className: "docx-preview",
          inWrapper: true,
        });

        if (isMounted) {
          setIsLoading(false);
        }
      } catch (err) {
        if (isMounted) {
          setError(err instanceof Error ? err.message : "Failed to render DOCX");
          setIsLoading(false);
        }
      }
    };

    renderDocx();

    return () => {
      isMounted = false;
      if (currentBlobUrl) {
        URL.revokeObjectURL(currentBlobUrl);
      }
    };
  }, [content, createBlobUrl]);

  const handleDownload = useCallback(() => {
    if (!blobUrl) return;

    const link = document.createElement("a");
    link.href = blobUrl;
    link.download = filename || "document.docx";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }, [blobUrl, filename]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center gap-3 p-8 bg-[var(--color-bg-tertiary)] rounded-xl border border-[var(--color-border-primary)] max-h-[400px]">
        <Loader2 className="w-5 h-5 animate-spin text-[var(--color-accent-primary)]" />
        <span className="text-sm text-[var(--color-text-muted)]">Rendering DOCX...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-start gap-3 p-4 bg-red-500/10 border border-red-500/30 rounded-xl max-h-[400px]">
        <div className="flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-sm font-medium text-red-500">Failed to render DOCX</p>
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
    );
  }

  return (
    <div className="bg-[var(--color-bg-tertiary)] rounded-xl border border-[var(--color-border-primary)] overflow-hidden max-h-[400px]">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-[var(--color-bg-secondary)] border-b border-[var(--color-border-primary)]">
        <div className="flex items-center gap-2">
          <FileText className="w-4 h-4 text-blue-400" />
          <span className="text-sm font-medium text-[var(--color-text-secondary)]">
            {filename || "DOCX Preview"}
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
      <div className="overflow-auto max-h-[340px] p-4">
        <div
          ref={containerRef}
          className="docx-preview-container bg-white rounded shadow-sm min-h-[200px]"
        />
      </div>

      {/* Additional styles for docx-preview */}
      <style jsx global>{`
        .docx-preview {
          font-family: "Times New Roman", serif;
        }
        .docx-preview p {
          margin: 0.5em 0;
        }
        .docx-preview table {
          border-collapse: collapse;
          width: 100%;
        }
        .docx-preview td,
        .docx-preview th {
          border: 1px solid #ccc;
          padding: 4px 8px;
        }
      `}</style>
    </div>
  );
}
