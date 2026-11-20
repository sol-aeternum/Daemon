'use client';

import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { InlineArtifact } from '../../components/chat/InlineArtifact';
import {
  HTML_ARTIFACT_LANGUAGE,
  readHtmlArtifactFence,
} from '../../lib/htmlArtifactFence';
import { canonicalUrlKey } from '../../lib/toolActivity';
import type { ToolSource } from '../../lib/toolActivity';

interface MarkdownRendererProps {
  content: string;
  streaming?: boolean;
  compact?: boolean;
  className?: string;
  /**
   * Sources returned by tool calls in this message's event stream. Markdown
   * links that land on one of these URLs render as inline citation pills;
   * every other link stays a plain link. Nothing is invented — only links
   * the answer already contains that match returned sources are restyled,
   * so sentence attribution is never fabricated.
   */
  sources?: ToolSource[];
}

function CitationPill({
  href,
  domain,
  children,
}: {
  href: string;
  domain: string;
  children?: React.ReactNode;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="mx-0.5 inline-flex max-w-full items-center rounded-full border border-[var(--color-border-primary)] bg-[var(--color-bg-tertiary)] px-2 py-1 text-xs text-[var(--color-text-muted)] align-baseline transition-colors hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-secondary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-accent-primary)]"
      title={href}
    >
      <span className="max-w-48 truncate">{domain}</span>
      {children ? <span className="sr-only"> — {children}</span> : null}
    </a>
  );
}

const getClassNameValue = (value: unknown): string => {
  if (Array.isArray(value)) {
    return value.map((item) => String(item)).join(' ');
  }

  if (typeof value === 'string') {
    return value;
  }

  return '';
};

// Cache only the pure parse/render subtree for this mounted instance. Primitive
// snapshots include every source field/order, so fresh equivalent source arrays
// can reuse work without a text-only comparator freezing changed provenance.
const MarkdownContent = memo(function MarkdownContent({
  content,
  sourceData,
  streaming,
}: {
  content: string;
  sourceData: string;
  streaming: boolean;
}) {
  const sources = JSON.parse(sourceData) as ToolSource[];
  // Host lowercase; path/query compared verbatim (case-sensitive), hash
  // ignored on both sides per canonicalUrlKey.
  const sourceByUrl = (() => {
    const map = new Map<string, ToolSource>();
    for (const source of sources || []) {
      const key = canonicalUrlKey(source.url);
      if (key && !map.has(key)) map.set(key, source);
    }
    return map;
  })();

  const findSource = (href: string | undefined): ToolSource | null => {
    if (!href) return null;
    const key = canonicalUrlKey(href);
    return key ? (sourceByUrl.get(key) ?? null) : null;
  };

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHighlight]}
      components={{
        a: ({ node, href, children, ...props }) => {
          void node;
          const matched = findSource(href);
          if (matched) {
            return (
              <CitationPill href={href!} domain={matched.domain}>
                {children}
              </CitationPill>
            );
          }
          return (
            <a {...props} href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          );
        },
        code: ({ node, className, children, ...props }) => {
          const match = /language-(\w+)/.exec(className || '');
          const isInline = !match && !className;
          if (isInline) {
            return (
              <code
                className="px-1.5 py-0.5 bg-[var(--color-bg-tertiary)] rounded text-sm font-mono"
                {...props}
              >
                {children}
              </code>
            );
          }

          return (
            <code className={`${className} block overflow-x-auto`} {...props}>
              {children}
            </code>
          );
        },
        pre: ({ node, ...props }) => {
          const code = node?.children[0];
          const start = node?.position?.start.offset;
          const end = node?.position?.end.offset;
          if (
            code?.type === 'element' &&
            code.tagName === 'code' &&
            getClassNameValue(code.properties.className)
              .split(/\s+/)
              .some(
                (value) =>
                  value.startsWith('language-') &&
                  HTML_ARTIFACT_LANGUAGE.test(value.slice(9)),
              ) &&
            typeof start === 'number' &&
            typeof end === 'number'
          ) {
            // rehype-highlight may wrap portions in spans. Recover the
            // underlying code text only for nested Markdown containers.
            const text = (child: (typeof code.children)[number]): string =>
              child.type === 'text'
                ? child.value
                : child.type === 'element'
                  ? child.children.map(text).join('')
                  : '';
            const artifact = readHtmlArtifactFence(
              content,
              start,
              end,
              code.children.map(text).join('').replace(/\n$/, ''),
            );
            if (artifact) {
              return (
                <InlineArtifact
                  htmlContent={artifact.source}
                  closed={artifact.closed}
                  streaming={streaming}
                />
              );
            }
          }

          return (
            <pre
              {...props}
              className="overflow-x-auto my-2 p-3 bg-[var(--color-bg-tertiary)] rounded-lg border border-[var(--color-border-primary)]"
            />
          );
        },
        table: ({ node, ...props }) => (
          <div className="overflow-x-auto my-2">
            <table
              {...props}
              className="min-w-full divide-y divide-[var(--color-border-primary)]"
            />
          </div>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  );
});

export default function MarkdownRenderer({
  content,
  compact = false,
  className = '',
  sources,
  streaming = false,
}: MarkdownRendererProps) {
  // Styles stay live independently of parsing. Child-owned state/context updates
  // (for example InlineArtifact controls/theme) are not blocked by React.memo.
  const proseClasses = compact
    ? 'prose prose-sm max-w-none'
    : 'prose max-w-none';
  const themeClasses = `
    text-[var(--color-text-primary)]
    prose-headings:text-[var(--color-text-primary)]
    prose-p:text-[var(--color-text-primary)]
    prose-strong:text-[var(--color-text-primary)]
    prose-li:text-[var(--color-text-primary)]
    prose-code:text-[var(--color-text-primary)]
    prose-a:text-[var(--color-accent-primary)]
    hover:prose-a:text-[var(--color-accent-hover)]
    prose-hr:border-[var(--color-border-primary)]
    prose-blockquote:text-[var(--color-text-secondary)]
  `;
  return (
    <div className={`${proseClasses} ${themeClasses} ${className}`}>
      <MarkdownContent
        content={content}
        sourceData={JSON.stringify(sources || [])}
        streaming={streaming}
      />
    </div>
  );
}
