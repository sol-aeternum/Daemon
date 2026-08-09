import type { ReactNode } from 'react';
import { FileText, Table, File, Download } from 'lucide-react';
import { ensureAuthHeader } from '@/lib/auth';

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

export function FileDownloadCard({
  filename,
  fileUrl,
  fileSize,
  fileType,
  trailingAction,
  className,
}: FileDownloadCardProps) {
  const handleDownload = async () => {
    let objectUrl: string | null = null;
    let cleanupAnchor: HTMLAnchorElement | null = null;
    try {
      const authHeader = await ensureAuthHeader();
      const headers = new Headers();
      if (authHeader) {
        headers.set('Authorization', authHeader);
      }
      const response = await fetch(fileUrl, { headers });
      if (!response.ok) throw new Error(`Download failed: ${response.status}`);
      const blob = await response.blob();
      objectUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = filename;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      document.body.appendChild(link);
      cleanupAnchor = link;
      link.click();
    } finally {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      if (cleanupAnchor && cleanupAnchor.parentNode) {
        cleanupAnchor.parentNode.removeChild(cleanupAnchor);
      }
    }
  };

  return (
    <div
      className={`flex items-center gap-4 p-4 bg-[var(--color-bg-tertiary)] rounded-xl border border-[var(--color-border-primary)] w-full transition-all hover:shadow-md hover:border-[var(--color-border-secondary)] ${className ?? ''}`}
    >
      <div className="flex-shrink-0 w-12 h-12 flex items-center justify-center bg-[var(--color-bg-secondary)] rounded-lg border border-[var(--color-border-primary)]">
        {getFileIcon(fileType, filename)}
      </div>

      <div className="flex-1 min-w-0">
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
          className="text-sm font-medium text-[var(--color-text-secondary)] truncate"
          title={filename}
        >
          {filename}
        </p>
      </div>

      <button
        type="button"
        onClick={handleDownload}
        className="flex-shrink-0 flex items-center gap-2 px-3 py-2 bg-[var(--color-accent-primary)] hover:bg-[var(--color-accent-hover)] text-white text-sm font-medium rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--color-bg-tertiary)]"
        title={`Download ${filename}`}
      >
        <Download className="w-4 h-4" />
        <span className="hidden sm:inline">Download</span>
      </button>

      {trailingAction ? (
        <div className="flex-shrink-0">{trailingAction}</div>
      ) : null}
    </div>
  );
}
