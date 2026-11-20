'use client';

import { formatMessageContent } from '../lib/format';
import { hasHtmlArtifactFence } from '../lib/htmlArtifactFence';
import type { ToolSource } from '../lib/toolActivity';
import MarkdownRenderer from '../src/components/MarkdownRenderer';

interface MarkdownMessageProps {
  content: string;
  streaming?: boolean;
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
  streaming,
}: MarkdownMessageProps) {
  // Legacy media/prose cleanup rewrites text globally. Do not apply it to
  // artifact-bearing replies: their fenced source must be recoverable verbatim.
  const processedContent = hasHtmlArtifactFence(content)
    ? content
    : formatMessageContent(content);

  return (
    <MarkdownRenderer
      content={processedContent}
      compact={true}
      sources={sources}
      streaming={streaming}
    />
  );
}
