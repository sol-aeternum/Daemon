'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { InlineArtifact } from '../../components/chat/InlineArtifact';
import { canonicalUrlKey } from '../../lib/toolActivity';
import type { ToolSource } from '../../lib/toolActivity';

interface MarkdownRendererProps {
  content: string;
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

const isInteractiveHtmlClassName = (className: string): boolean => {
  const normalized = className.toLowerCase();
  return (
    normalized.includes('language-html') && normalized.includes('interactive')
  );
};

const isInteractiveHtmlPreNode = (node: unknown): boolean => {
  if (!node || typeof node !== 'object') {
    return false;
  }

  const maybeChildren = (node as { children?: unknown }).children;
  if (!Array.isArray(maybeChildren) || maybeChildren.length === 0) {
    return false;
  }

  const firstChild = maybeChildren[0];
  if (!firstChild || typeof firstChild !== 'object') {
    return false;
  }

  const properties = (firstChild as { properties?: unknown }).properties;
  if (!properties || typeof properties !== 'object') {
    return false;
  }

  const rawClassName = (properties as { className?: unknown }).className;
  return isInteractiveHtmlClassName(getClassNameValue(rawClassName));
};

export default function MarkdownRenderer({
  content,
  compact = false,
  className = '',
  sources,
}: MarkdownRendererProps) {
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

  // Base prose classes - compact uses prose-sm, full uses standard prose
  const proseClasses = compact
    ? 'prose prose-sm max-w-none'
    : 'prose max-w-none';

  // Theme color classes using CSS variables
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
              <a
                {...props}
                href={href}
                target="_blank"
                rel="noopener noreferrer"
              >
                {children}
              </a>
            );
          },
          code: ({ node, className, children, ...props }) => {
            const match = /language-(\w+)/.exec(className || '');
            const isInline = !match && !className;
            const normalizedClassName = (className || '').toLowerCase();

            if (isInteractiveHtmlClassName(normalizedClassName)) {
              return (
                <InlineArtifact
                  htmlContent={String(children).trim()}
                  title="Interactive Artifact"
                />
              );
            }

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
            if (isInteractiveHtmlPreNode(node)) {
              return <>{props.children}</>;
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
    </div>
  );
}
