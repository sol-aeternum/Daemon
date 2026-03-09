'use client';

import { formatMessageContent } from '../lib/format';
import MarkdownRenderer from '../src/components/MarkdownRenderer';

interface MarkdownMessageProps {
  content: string;
}

export default function MarkdownMessage({ content }: MarkdownMessageProps) {
  const processedContent = formatMessageContent(content);

  return (
    <MarkdownRenderer
      content={processedContent}
      compact={true}
    />
  );
}
