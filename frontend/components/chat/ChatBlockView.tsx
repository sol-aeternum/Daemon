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
  Play,
  Square,
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
import { SandboxedHtmlFrame } from './SandboxedHtmlFrame';

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

const sandboxNote = 'Generated content, isolated from Daemon and your data';

/**
 * SVG renders passively as an image: no script runs and no subresource
 * loads, inline or in Details. Only `kind: html` uses the executable frame.
 */
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

/** A block inside its message: inline widget or compact card. */
export function ChatBlockCard({
  block,
  onOpen,
}: {
  block: ChatBlock;
  onOpen: () => void;
}) {
  const [view, setView] = useState<'preview' | 'code'>('preview');
  const [running, setRunning] = useState(true);
  const inline = block.display === 'inline';
  const executable = block.kind === 'html';

  return (
    <section
      aria-label={block.title}
      className="not-prose my-3 overflow-hidden rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)]"
    >
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--color-border-primary)] px-3 py-2">
        <BlockHeader block={block} />
        {inline && (
          <div className="flex gap-2">
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
            {view === 'preview' && executable && (
              <button
                type="button"
                className={buttonClass}
                onClick={() => setRunning((value) => !value)}
              >
                {running ? (
                  <Square className="h-4 w-4" aria-hidden />
                ) : (
                  <Play className="h-4 w-4" aria-hidden />
                )}
                {running ? 'Stop' : 'Run'}
              </button>
            )}
          </div>
        )}
      </header>
      {inline && (
        <div className="bg-[var(--color-bg-primary)]">
          {view === 'code' ? (
            <div className="p-3">
              <SourceView block={block} />
            </div>
          ) : !executable ? (
            <div className="p-3">
              <PassiveSvg block={block} />
            </div>
          ) : running ? (
            <>
              <p className="px-3 pt-2 text-xs text-[var(--color-text-muted)]">
                {sandboxNote}
              </p>
              <SandboxedHtmlFrame
                content={block.content}
                title={block.title}
                autoHeight
              />
            </>
          ) : (
            <p className="px-3 py-6 text-center text-sm text-[var(--color-text-muted)]">
              Stopped. Select Run to start it again.
            </p>
          )}
        </div>
      )}
      <div className="px-3 py-2">
        <BlockActions block={block} onOpen={onOpen} />
      </div>
    </section>
  );
}

/** Full preview in the Details panel. Executable HTML runs on request. */
export function ChatBlockPreview({ block }: { block: ChatBlock }) {
  const [running, setRunning] = useState(false);
  const [view, setView] = useState<'preview' | 'code'>('preview');

  let preview: React.ReactNode;
  if (block.kind === 'svg') {
    preview = (
      <div className="rounded-lg border border-[var(--color-border-primary)] p-3">
        <PassiveSvg block={block} />
      </div>
    );
  } else if (block.kind === 'html') {
    preview = !running ? (
      <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-[var(--color-border-primary)] p-6 text-center">
        <p className="text-sm text-[var(--color-text-secondary)]">
          This page runs generated code in a sandbox isolated from Daemon and
          your data.
        </p>
        <button
          type="button"
          className={buttonClass}
          onClick={() => setRunning(true)}
        >
          <Play className="h-4 w-4" aria-hidden />
          Run
        </button>
      </div>
    ) : (
      <div className="overflow-hidden rounded-lg border border-[var(--color-border-primary)]">
        <p className="flex items-center justify-between gap-2 border-b border-[var(--color-border-primary)] px-3 py-1.5 text-xs text-[var(--color-text-muted)]">
          {sandboxNote}
          <button
            type="button"
            className="inline-flex min-h-touch items-center gap-1 px-2"
            onClick={() => setRunning(false)}
          >
            <Square className="h-3.5 w-3.5" aria-hidden />
            Stop
          </button>
        </p>
        <SandboxedHtmlFrame
          content={block.content}
          title={block.title}
          className="min-h-html-preview"
        />
      </div>
    );
  } else if (block.kind === 'markdown') {
    preview = <MarkdownRenderer content={block.content} />;
  } else if (block.kind === 'csv') {
    preview = <CsvPreview content={block.content} />;
  } else {
    preview = <SourceView block={block} />;
  }

  const hasSourceToggle = block.kind !== 'code' && block.kind !== 'json';
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
