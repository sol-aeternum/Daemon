'use client';

import { Download } from 'lucide-react';
import {
  downloadTextFile,
  textFileMime,
  textFileName,
  type TextFileKind,
} from '@/lib/textDownload';

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
 * Actions under an ordinary ```html / ```svg fence. Fences stay source-only:
 * executable HTML is approved for `render_block` only
 * (docs/ARTIFACT_PRESENTATION_DESIGN.md D0/D1), so this offers download only.
 */
export function CodeFenceActions({
  source,
  kind,
  streaming,
}: CodeFenceActionsProps) {
  const title = fenceTitle(source, kind);
  return (
    <div className="not-prose my-2 flex flex-wrap gap-2">
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
  );
}
