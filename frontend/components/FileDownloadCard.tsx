'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { FileText, Table, File, Download } from 'lucide-react';
import {
  ensureAuthHeader,
  getAuthGeneration,
  subscribeAuthGeneration,
} from '@/lib/auth';
import { getProtectedMediaUrl } from '@/hooks/useAuthenticatedImageUrl';

interface FileDownloadCardProps {
  filename: string;
  fileUrl: string;
  fileSize?: number;
  fileType?: string;
  trailingAction?: ReactNode;
  className?: string;
}

function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function getFileIcon(fileType?: string, filename?: string) {
  const ext =
    fileType?.toLowerCase() || filename?.split('.').pop()?.toLowerCase();

  switch (ext) {
    case 'docx':
    case 'doc':
    case 'pdf':
    case 'txt':
    case 'md':
      return (
        <FileText className="w-8 h-8 text-[var(--color-accent-primary)]" />
      );
    case 'csv':
    case 'xlsx':
    case 'xls':
    case 'json':
      return <Table className="w-8 h-8 text-[var(--color-status-success)]" />;
    default:
      return <File className="w-8 h-8 text-[var(--color-text-muted)]" />;
  }
}

function getFileTypeLabel(fileType?: string, filename?: string): string {
  if (fileType) return fileType.toUpperCase();
  const ext = filename?.split('.').pop()?.toLowerCase();
  return ext ? ext.toUpperCase() : 'FILE';
}

export function FileDownloadCard(props: FileDownloadCardProps) {
  return <SelectedFileDownloadCard key={props.fileUrl} {...props} />;
}

function SelectedFileDownloadCard({
  filename,
  fileUrl,
  fileSize,
  fileType,
  trailingAction,
  className,
}: FileDownloadCardProps) {
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const requestRef = useRef<AbortController | null>(null);
  useEffect(
    () => () => {
      requestRef.current?.abort();
    },
    [fileUrl],
  );
  const handleDownload = async () => {
    if (requestRef.current && !requestRef.current.signal.aborted) return;
    const controller = new AbortController();
    const generation = getAuthGeneration();
    const unsubscribe = subscribeAuthGeneration(() => controller.abort());
    requestRef.current = controller;
    setDownloading(true);
    setError(null);
    let objectUrl: string | null = null;
    let cleanupAnchor: HTMLAnchorElement | null = null;
    try {
      const protectedUrl = getProtectedMediaUrl(fileUrl);
      const authHeader = protectedUrl ? await ensureAuthHeader() : null;
      if (controller.signal.aborted || generation !== getAuthGeneration())
        return;
      if (protectedUrl && !authHeader)
        throw new Error('Sign in again to download this file.');
      const headers = new Headers();
      if (authHeader) {
        headers.set('Authorization', authHeader);
      }
      const response = await fetch(protectedUrl ?? fileUrl, {
        headers,
        signal: controller.signal,
      });
      if (!response.ok)
        throw new Error(
          response.status === 404
            ? 'File unavailable (missing or expired). Your conversation is still here.'
            : response.status === 401 || response.status === 403
              ? 'You do not have access to download this file.'
              : `Download failed (${response.status}). Please try again.`,
        );
      const blob = await response.blob();
      if (controller.signal.aborted || generation !== getAuthGeneration())
        return;
      objectUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = filename;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      document.body.appendChild(link);
      cleanupAnchor = link;
      link.click();
    } catch (err) {
      if (!controller.signal.aborted)
        setError(
          err instanceof Error
            ? err.message
            : 'Download failed. Please try again.',
        );
    } finally {
      unsubscribe();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      if (cleanupAnchor && cleanupAnchor.parentNode) {
        cleanupAnchor.parentNode.removeChild(cleanupAnchor);
      }
      if (requestRef.current === controller) {
        requestRef.current = null;
        setDownloading(false);
      }
    }
  };

  return (
    <div
      className={`flex flex-wrap items-center gap-3 p-4 bg-[var(--color-bg-tertiary)] rounded-xl border border-[var(--color-border-primary)] w-full min-w-0 ${className ?? ''}`}
    >
      <div className="flex-shrink-0 w-12 h-12 flex items-center justify-center bg-[var(--color-bg-secondary)] rounded-lg border border-[var(--color-border-primary)]">
        {getFileIcon(fileType, filename)}
      </div>

      <div className="flex-1 min-w-0 basis-40">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider">
            {getFileTypeLabel(fileType, filename)}
          </span>
          {fileSize !== undefined && (
            <>
              <span className="text-[var(--color-border-secondary)]">•</span>
              <span className="text-xs text-[var(--color-text-muted)]">
                {formatFileSize(fileSize)}
              </span>
            </>
          )}
        </div>
        <p
          className="text-sm font-medium text-[var(--color-text-secondary)] break-all"
          title={filename}
        >
          {filename}
        </p>
      </div>

      <button
        type="button"
        onClick={handleDownload}
        disabled={downloading}
        className="min-h-touch flex-shrink-0 flex items-center gap-2 px-3 py-2 bg-[var(--color-accent-primary)] hover:bg-[var(--color-accent-hover)] text-[var(--color-text-on-accent)] text-sm font-medium rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--color-bg-tertiary)] disabled:opacity-60"
        title={`Download ${filename}`}
      >
        <Download className="w-4 h-4" />
        <span>{downloading ? 'Downloading…' : 'Download'}</span>
      </button>

      {trailingAction ? (
        <div className="flex-shrink-0">{trailingAction}</div>
      ) : null}
      {error && (
        <p
          role="alert"
          className="w-full text-sm text-[var(--color-text-primary)]"
        >
          {error}
        </p>
      )}
    </div>
  );
}
