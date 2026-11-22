'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  HTML_PREVIEW_FRAME_PATH,
  HTML_PREVIEW_MESSAGE_TYPE,
  readHtmlPreviewHeight,
  readHtmlPreviewTheme,
  type HtmlPreviewMessage,
} from '@/lib/htmlPreviewFrame';

interface SandboxedHtmlFrameProps {
  /** Untrusted HTML. It runs only inside the isolated frame document. */
  content: string;
  title: string;
  /** Size the frame to its content (bounded); otherwise CSS decides. */
  autoHeight?: boolean;
  className?: string;
  onError?: () => void;
}

/**
 * Runs generated HTML in Daemon's isolated preview frame:
 * an opaque-origin `allow-scripts` sandbox (no `allow-same-origin`) whose
 * document is served with a no-network CSP. The app only accepts a bounded
 * height message from that frame, and only from this element's window.
 */
export function SandboxedHtmlFrame({
  content,
  title,
  autoHeight = false,
  className = '',
  onError,
}: SandboxedHtmlFrameProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState<number | null>(null);

  const postContent = useCallback(() => {
    const target = iframeRef.current?.contentWindow;
    if (!target) return;
    const message: HtmlPreviewMessage = {
      type: HTML_PREVIEW_MESSAGE_TYPE,
      content,
      title,
      theme: readHtmlPreviewTheme(),
    };
    // The frame's origin is opaque, so postMessage requires "*". The message
    // goes directly to that frame's window, which accepts only its parent.
    target.postMessage(message, '*');
  }, [content, title]);

  useEffect(() => {
    postContent();
  }, [postContent]);

  useEffect(() => {
    if (!autoHeight) return;
    const handleMessage = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) return;
      const next = readHtmlPreviewHeight(event.data);
      if (next === null) return;
      setHeight((previous) =>
        previous !== null && Math.abs(previous - next) < 4 ? previous : next,
      );
    };
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [autoHeight]);

  return (
    <iframe
      ref={iframeRef}
      src={HTML_PREVIEW_FRAME_PATH}
      sandbox="allow-scripts"
      title={title}
      onLoad={postContent}
      onError={onError}
      className={`w-full border-0 bg-[var(--color-bg-document)] ${className}`}
      style={autoHeight ? { height: `${height ?? 240}px` } : undefined}
    />
  );
}
