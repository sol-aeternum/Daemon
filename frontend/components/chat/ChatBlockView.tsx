'use client';

import { useState } from 'react';
import {
  Code2,
  Download,
  FileCode2,
  FileJson,
  FileText,
  Image as ImageIcon,
  LayoutPanelLeft,
  Table,
} from 'lucide-react';
import type { ChatBlock } from '@/lib/chatBlocks';
import {
  downloadTextFile,
  textFileMime,
  textFileName,
} from '@/lib/textDownload';
import MarkdownRenderer from '@/src/components/MarkdownRenderer';
import { CsvPreview } from '@/src/components/previews/CsvPreview';
import { CopyResponseButton } from '../CopyResponseButton';

const KIND_LABEL: Record<ChatBlock['kind'], string> = {
  html: 'Interactive page',
  svg: 'Image',
  markdown: 'Document',
  code: 'Code',
  csv: 'Table',
  json: 'Data',
};

const KIND_ICON = {
  html: LayoutPanelLeft,
  svg: ImageIcon,
  markdown: FileText,
  code: FileCode2,
  csv: Table,
  json: FileJson,
} satisfies Record<ChatBlock['kind'], unknown>;

const buttonClass =
  'inline-flex min-h-touch items-center gap-1.5 rounded-lg border border-[var(--color-border-primary)] px-3 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]';

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
}

function downloadBlock(block: ChatBlock) {
  downloadTextFile(
    block.content,
    textFileName(block.title, block.kind, {
      language: block.language,
      version: block.version,
    }),
    textFileMime(block.kind),
  );
}

function BlockActions({
  block,
  onOpen,
}: {
  block: ChatBlock;
  onOpen?: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {onOpen && (
        <button
          type="button"
          className={buttonClass}
          onClick={onOpen}
          data-block-preview={block.key}
          aria-label={`Open ${block.title} in panel`}
        >
          <LayoutPanelLeft className="h-4 w-4" aria-hidden />
          Open in panel
        </button>
      )}
      <button
        type="button"
        className={buttonClass}
        onClick={() => downloadBlock(block)}
        aria-label={`Download ${block.title}`}
      >
        <Download className="h-4 w-4" aria-hidden />
        Download
      </button>
      <CopyResponseButton content={block.content} label="Copy source" />
    </div>
  );
}

function BlockHeader({ block }: { block: ChatBlock }) {
  const Icon = KIND_ICON[block.kind];
  return (
    <div className="flex min-w-0 items-center gap-2">
      <Icon
        className="h-4 w-4 shrink-0 text-[var(--color-accent-primary)]"
        aria-hidden
      />
      <span className="truncate font-medium text-[var(--color-text-primary)]">
        {block.title}
      </span>
      <span className="shrink-0 text-xs text-[var(--color-text-muted)]">
        {KIND_LABEL[block.kind]}
        {block.version > 1 ? ` · v${block.version}` : ''} ·{' '}
        {formatBytes(block.bytes)}
      </span>
    </div>
  );
}

function SourceView({ block }: { block: ChatBlock }) {
  return (
    <pre className="max-h-file-content overflow-auto rounded-lg border border-[var(--color-border-primary)] bg-[var(--color-bg-tertiary)] p-3 text-xs">
      <code>{block.content}</code>
    </pre>
  );
}

/**
 * Generated script never runs here: E1 execution is held off until the
 * release condition in docs/ARTIFACT_PRESENTATION_DESIGN.md D6 is met. HTML
 * blocks show their source; SVG renders passively as an image (no script, no
 * subresources), inline and in Details.
 */
const htmlHeldNote =
  "Running generated pages in Daemon isn't enabled yet. Download the file to use it.";

function PassiveSvg({ block }: { block: ChatBlock }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element -- data: URL, no optimisation
    <img
      src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(block.content)}`}
      alt={block.title}
      className="mx-auto block h-auto max-h-file-content max-w-full"
    />
  );
}

/** A block inside its message: inline image or compact card. */
export function ChatBlockCard({
  block,
  onOpen,
}: {
  block: ChatBlock;
  onOpen: () => void;
}) {
  const [view, setView] = useState<'preview' | 'code'>('preview');
  // Only passive svg is ever shown inline; html is a card (D4/D6).
  const inline = block.display === 'inline' && block.kind === 'svg';

  return (
    <section
      aria-label={block.title}
      className="not-prose my-3 overflow-hidden rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)]"
    >
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--color-border-primary)] px-3 py-2">
        <BlockHeader block={block} />
        {inline && (
          <button
            type="button"
            className={buttonClass}
            aria-pressed={view === 'code'}
            onClick={() =>
              setView((value) => (value === 'code' ? 'preview' : 'code'))
            }
          >
            <Code2 className="h-4 w-4" aria-hidden />
            {view === 'code' ? 'Preview' : 'Code'}
          </button>
        )}
      </header>
      {inline && (
        <div className="bg-[var(--color-bg-primary)] p-3">
          {view === 'code' ? (
            <SourceView block={block} />
          ) : (
            <PassiveSvg block={block} />
          )}
        </div>
      )}
      <div className="px-3 py-2">
        <BlockActions block={block} onOpen={onOpen} />
      </div>
    </section>
  );
}

/** Full preview in the Details panel. Nothing here executes. */
export function ChatBlockPreview({ block }: { block: ChatBlock }) {
  const [view, setView] = useState<'preview' | 'code'>('preview');

  let preview: React.ReactNode;
  if (block.kind === 'svg') {
    preview = (
      <div className="rounded-lg border border-[var(--color-border-primary)] p-3">
        <PassiveSvg block={block} />
      </div>
    );
  } else if (block.kind === 'html') {
    preview = (
      <div className="space-y-2">
        <p className="text-sm text-[var(--color-text-secondary)]">
          {htmlHeldNote}
        </p>
        <SourceView block={block} />
      </div>
    );
  } else if (block.kind === 'markdown') {
    preview = <MarkdownRenderer content={block.content} />;
  } else if (block.kind === 'csv') {
    preview = <CsvPreview content={block.content} />;
  } else {
    preview = <SourceView block={block} />;
  }

  const hasSourceToggle =
    block.kind !== 'code' && block.kind !== 'json' && block.kind !== 'html';
  return (
    <div className="space-y-3">
      <BlockHeader block={block} />
      <div className="flex flex-wrap gap-2">
        {hasSourceToggle && (
          <button
            type="button"
            className={buttonClass}
            aria-pressed={view === 'code'}
            onClick={() =>
              setView((value) => (value === 'code' ? 'preview' : 'code'))
            }
          >
            <Code2 className="h-4 w-4" aria-hidden />
            {view === 'code' ? 'Preview' : 'Code'}
          </button>
        )}
        <BlockActions block={block} />
      </div>
      {view === 'code' && hasSourceToggle ? (
        <SourceView block={block} />
      ) : (
        preview
      )}
    </div>
  );
}
