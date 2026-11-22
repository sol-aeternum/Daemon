'use client';

import { useState } from 'react';
import { Download, Play, Square } from 'lucide-react';
import {
  downloadTextFile,
  textFileMime,
  textFileName,
  type TextFileKind,
} from '@/lib/textDownload';
import { SandboxedHtmlFrame } from './SandboxedHtmlFrame';

interface CodeFenceActionsProps {
  source: string;
  kind: Extract<TextFileKind, 'html' | 'svg'>;
  streaming: boolean;
}

/** A readable default name: the document's own <title>, else the kind. */
export function fenceTitle(
  source: string,
  kind: CodeFenceActionsProps['kind'],
) {
  const title = /<title[^>]*>([^<]{1,120})<\/title>/i.exec(source)?.[1]?.trim();
  return title || (kind === 'html' ? 'page' : 'image');
}

const buttonClass =
  'inline-flex min-h-touch items-center gap-1.5 rounded-lg border border-[var(--color-border-primary)] px-3 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-50';

/**
 * Actions under an ordinary ```html / ```svg fence. The fence itself stays
 * source-only; running is an explicit user action in the isolated frame.
 */
export function CodeFenceActions({
  source,
  kind,
  streaming,
}: CodeFenceActionsProps) {
  const [running, setRunning] = useState(false);
  const title = fenceTitle(source, kind);
  const canRun = kind === 'html' && !streaming && source.trim() !== '';

  return (
    <div className="not-prose my-2">
      <div className="flex flex-wrap gap-2">
        {kind === 'html' && (
          <button
            type="button"
            className={buttonClass}
            disabled={!canRun}
            aria-pressed={running}
            onClick={() => setRunning((value) => !value)}
          >
            {running ? (
              <Square className="h-4 w-4" aria-hidden />
            ) : (
              <Play className="h-4 w-4" aria-hidden />
            )}
            {running ? 'Stop' : 'Run in sandbox'}
          </button>
        )}
        <button
          type="button"
          className={buttonClass}
          disabled={streaming}
          onClick={() =>
            downloadTextFile(
              source,
              textFileName(title, kind),
              textFileMime(kind),
            )
          }
        >
          <Download className="h-4 w-4" aria-hidden />
          Download
        </button>
      </div>
      {running && canRun && (
        <div className="mt-2 overflow-hidden rounded-lg border border-[var(--color-border-primary)]">
          <p className="border-b border-[var(--color-border-primary)] px-3 py-1.5 text-xs text-[var(--color-text-muted)]">
            Generated content, isolated from Daemon with no network access
          </p>
          <SandboxedHtmlFrame content={source} title={title} autoHeight />
        </div>
      )}
    </div>
  );
}
