'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { formatMessageContent } from '../lib/format';

interface MarkdownMessageProps {
  content: string;
}

export default function MarkdownMessage({ content }: MarkdownMessageProps) {
  const processedContent = formatMessageContent(content);

  return (
    <div className="prose prose-sm max-w-none text-[var(--color-text-primary)] prose-headings:text-[var(--color-text-primary)] prose-p:text-[var(--color-text-primary)] prose-strong:text-[var(--color-text-primary)] prose-li:text-[var(--color-text-primary)] prose-code:text-[var(--color-text-primary)] prose-a:text-[var(--color-accent-primary)] hover:prose-a:text-[var(--color-accent-hover)] prose-hr:border-[var(--color-border-primary)] prose-blockquote:text-[var(--color-text-secondary)]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          a: ({ node, ...props }) => (
            <a
              {...props}
              target="_blank"
              rel="noopener noreferrer"
            />
          ),
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
          pre: ({ node, ...props }) => (
            <pre
              {...props}
              className="overflow-x-auto my-2 p-3 bg-[var(--color-bg-tertiary)] rounded-lg border border-[var(--color-border-primary)]"
            />
          ),
          table: ({ node, ...props }) => (
            <div className="overflow-x-auto my-2">
              <table {...props} className="min-w-full divide-y divide-[var(--color-border-primary)]" />
            </div>
          ),
        }}
      >
        {processedContent}
      </ReactMarkdown>
    </div>
  );
}
