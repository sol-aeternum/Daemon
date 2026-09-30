'use client';

import { formatMessageContent } from '../lib/format';
import type { ToolSource } from '../lib/toolActivity';
import MarkdownRenderer from '../src/components/MarkdownRenderer';

interface MarkdownMessageProps {
  content: string;
  /**
   * Source URLs surfaced by this message's tool calls. Passed through to
   * MarkdownRenderer so answer links landing on a returned URL render as
   * small inline citation pills; ordinary links stay untouched. Available
   * independently from the debug-tool-hide preference.
   */
  sources?: ToolSource[];
}

export default function MarkdownMessage({
  content,
  sources,
}: MarkdownMessageProps) {
  const processedContent = formatMessageContent(content);

  return (
    <MarkdownRenderer
      content={processedContent}
      compact={true}
      sources={sources}
    />
  );
}
