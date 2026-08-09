'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { AlertCircle, Code2 } from 'lucide-react';
import {
  HTML_PREVIEW_FRAME_PATH,
  HTML_PREVIEW_MESSAGE_TYPE,
  type HtmlPreviewMessage,
} from '@/lib/htmlPreviewFrame';

interface HtmlPreviewProps {
  content: string;
  title?: string;
}

export function HtmlPreview({ content, title }: HtmlPreviewProps) {
  const [error, setError] = useState<string | null>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const isFrameReadyRef = useRef(false);

  const postPreviewContent = useCallback(() => {
    const target = iframeRef.current?.contentWindow;
    if (!target || !isFrameReadyRef.current) return;

    const message: HtmlPreviewMessage = {
      type: HTML_PREVIEW_MESSAGE_TYPE,
      content,
      title: title || 'HTML Preview',
    };
    // The outer frame is intentionally sandboxed without allow-same-origin,
    // so its effective origin is opaque and postMessage requires "*". The
    // message is still sent directly to that frame's contentWindow, and the
    // receiver accepts messages only from window.parent.
    target.postMessage(message, '*');
  }, [content, title]);

  useEffect(() => {
    postPreviewContent();
  }, [postPreviewContent]);

  const handleIframeLoad = () => {
    isFrameReadyRef.current = true;
    postPreviewContent();
  };

  // Handle iframe load errors
  const handleIframeError = () => {
    setError('Failed to load HTML preview');
  };

  if (error) {
    return (
      <div className="flex items-start gap-3 p-4 bg-red-500/10 border border-red-500/30 rounded-xl max-h-[400px]">
        <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
        <div className="flex-1">
          <p className="text-sm font-medium text-red-500">Preview Error</p>
          <p className="text-xs text-red-400 mt-1">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-[var(--color-bg-tertiary)] rounded-xl border border-[var(--color-border-primary)] overflow-hidden max-h-[400px]">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-[var(--color-bg-secondary)] border-b border-[var(--color-border-primary)]">
        <div className="flex items-center gap-2">
          <Code2 className="w-4 h-4 text-[var(--color-accent-primary)]" />
          <span className="text-sm font-medium text-[var(--color-text-secondary)]">
            {title || 'HTML Preview'}
          </span>
        </div>
        <span className="text-xs text-[var(--color-text-muted)] bg-[var(--color-bg-tertiary)] px-2 py-1 rounded">
          Sandboxed
        </span>
      </div>

      {/* Sandboxed iframe */}
      <div className="overflow-auto max-h-[340px]">
        <iframe
          ref={iframeRef}
          src={HTML_PREVIEW_FRAME_PATH}
          sandbox="allow-scripts"
          title={title || 'HTML Preview'}
          className="w-full min-h-[300px] bg-white"
          onLoad={handleIframeLoad}
          onError={handleIframeError}
        />
      </div>
    </div>
  );
}
