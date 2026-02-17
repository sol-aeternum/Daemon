'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';

// Simple formatMessageContent that strips image/audio paths
// This mirrors the logic from page.tsx
const formatMessageContent = (content: string): string => {
  if (!content) return '';
  // Strip image and audio file paths from content
  return content
    .replace(/!\[.*?\]\(.*?\.(?:png|jpg|jpeg|gif|webp|mp3|wav|ogg)\)/g, '')
    .replace(/\[.*?\]\(.*?\.(?:png|jpg|jpeg|gif|webp|mp3|wav|ogg)\)/g, '')
    .trim();
};

interface MarkdownMessageProps {
  content: string;
}

export default function MarkdownMessage({ content }: MarkdownMessageProps) {
  const processedContent = formatMessageContent(content);

  return (
    <div className="prose prose-sm max-w-none">
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
                  className="px-1.5 py-0.5 bg-gray-100 rounded text-sm font-mono"
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
              className="overflow-x-auto my-2 p-3 bg-gray-50 rounded-lg border border-gray-200"
            />
          ),
          table: ({ node, ...props }) => (
            <div className="overflow-x-auto my-2">
              <table {...props} className="min-w-full divide-y divide-gray-200" />
            </div>
          ),
        }}
      >
        {processedContent}
      </ReactMarkdown>
    </div>
  );
}
