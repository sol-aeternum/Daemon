'use client';

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  ChevronDown,
  ChevronRight,
  RefreshCw,
} from 'lucide-react';
import { sanitizeHtml } from '../../lib/sanitizeHtml';
import { useClientMounted } from '../../hooks/useClientMounted';
import { CopyResponseButton } from '../CopyResponseButton';

interface InlineArtifactProps {
  htmlContent: string;
  closed: boolean;
  streaming: boolean;
}

type Preview =
  | { kind: 'ready'; html: string }
  | { kind: 'unsupported' | 'failed' };

function hasUsableSvgViewport(shape: Element): boolean {
  for (
    let svg = shape.closest('svg');
    svg;
    svg = svg.parentElement?.closest('svg') ?? null
  ) {
    const width = Number.parseFloat(svg.getAttribute('width') ?? '');
    const height = Number.parseFloat(svg.getAttribute('height') ?? '');
    if (width <= 0 || height <= 0) return false;
    const viewBox = svg
      .getAttribute('viewBox')
      ?.trim()
      .split(/[\s,]+/);
    if (
      viewBox?.length === 4 &&
      (Number(viewBox[2]) <= 0 || Number(viewBox[3]) <= 0)
    ) {
      return false;
    }
  }
  return true;
}

function hasPreviewText(body: HTMLElement): boolean {
  const text = body.ownerDocument.createNodeIterator(
    body,
    NodeFilter.SHOW_TEXT,
  );
  for (let node = text.nextNode(); node; node = text.nextNode()) {
    const parent = node.parentElement;
    if (
      parent &&
      node.textContent?.replace(/[\s\u200b-\u200d\u2060\ufeff]/g, '') &&
      (!parent.closest('svg') ||
        (parent.closest('svg text') && hasUsableSvgViewport(parent)))
    ) {
      return true;
    }
  }
  return false;
}

function preparePreview(htmlContent: string): Preview {
  try {
    // This is a conservative source-only classification, not sanitization.
    // Do not rewrite the source; only sanitizeHtml's output can reach srcDoc.
    if (
      /<(?:script|style|link|form|input|select|option|textarea|button|iframe|object|embed|canvas|img|video|audio)\b/i.test(
        htmlContent,
      ) ||
      /<[a-z][^<>]*\s(?:style|on[a-z]+)\s*=/i.test(htmlContent)
    ) {
      return { kind: 'unsupported' };
    }
    const html = sanitizeHtml(htmlContent);
    // Only inspect sanitized markup. An empty container (or an SVG root
    // with no shapes) is not a usable preview, even if the iframe loads.
    const body = new DOMParser().parseFromString(html, 'text/html').body;
    if (
      !hasPreviewText(body) &&
      !Array.from(body.querySelectorAll('svg path, svg rect, svg circle')).some(
        (shape) => {
          if (!hasUsableSvgViewport(shape)) return false;
          const positive = (attr: string) =>
            Number.parseFloat(shape.getAttribute(attr) ?? '') > 0;
          if (shape.localName === 'path')
            return Boolean(shape.getAttribute('d')?.trim());
          if (shape.localName === 'rect')
            return positive('width') && positive('height');
          return positive('r');
        },
      )
    ) {
      return { kind: 'unsupported' };
    }
    return { kind: 'ready', html };
  } catch {
    return { kind: 'failed' };
  }
}

export function InlineArtifact({
  htmlContent,
  closed,
  streaming,
}: InlineArtifactProps) {
  const mounted = useClientMounted();
  const preview = useMemo(
    () =>
      mounted && closed && !streaming ? preparePreview(htmlContent) : null,
    [mounted, closed, streaming, htmlContent],
  );
  const notice = streaming
    ? 'Receiving HTML…'
    : !closed
      ? 'HTML preview unavailable: the code block was not closed.'
      : preview?.kind === 'unsupported'
        ? 'HTML preview unavailable: chat supports only static content. This source uses unsupported features or has no visible content.'
        : preview?.kind === 'failed'
          ? 'HTML preview failed to render.'
          : preview === null
            ? 'Preparing static HTML preview…'
            : null;
  return (
    <section
      aria-label="HTML preview"
      className="my-2 text-[var(--color-text-secondary)]"
    >
      {notice && (
        <p role="status" className="my-1 text-sm">
          {notice}
        </p>
      )}
      {preview?.kind === 'ready' && (
        <StaticArtifactFrame key={preview.html} sanitized={preview.html} />
      )}
      <details className="my-2">
        <summary className="cursor-pointer text-sm">View source</summary>
        <pre className="overflow-x-auto">
          <code>{htmlContent}</code>
        </pre>
      </details>
      <CopyResponseButton content={htmlContent} label="Copy source" />
    </section>
  );
}

/**
 * Read the per-request CSP nonce from the <meta name="csp-nonce"> tag
 * that the root layout renders from the `x-nonce` request header
 * (frontend/proxy.ts). The nonce is used to mark the inline <style> and
 * <script> tags inside the srcDoc so the iframe's inherited CSP does
 * not block the artifact shell.
 */
function readCspNonce(): string | undefined {
  if (typeof document === 'undefined') {
    return undefined;
  }
  const meta = document.querySelector<HTMLMetaElement>(
    'meta[name="csp-nonce"]',
  );
  return meta?.content || undefined;
}

interface ThemeVars {
  bgPrimary: string;
  bgSecondary: string;
  bgTertiary: string;
  textPrimary: string;
  textSecondary: string;
  textMuted: string;
  accent: string;
  accentHover: string;
  border: string;
  borderSecondary: string;
  statusSuccess: string;
  statusWarning: string;
  statusError: string;
}

const FALLBACK_THEME_VARS: ThemeVars = {
  bgPrimary: 'hsl(220, 13%, 10%)',
  bgSecondary: 'hsl(220, 15%, 14%)',
  bgTertiary: 'hsl(220, 13%, 18%)',
  textPrimary: 'hsl(220, 20%, 96%)',
  textSecondary: 'hsl(220, 15%, 75%)',
  textMuted: 'hsl(220, 10%, 55%)',
  accent: 'hsl(215, 60%, 55%)',
  accentHover: 'hsl(215, 65%, 60%)',
  border: 'hsl(220, 15%, 22%)',
  borderSecondary: 'hsl(220, 15%, 28%)',
  statusSuccess: 'hsl(145, 65%, 45%)',
  statusWarning: 'hsl(38, 85%, 55%)',
  statusError: 'hsl(0, 75%, 55%)',
};

const readThemeVars = (): ThemeVars => {
  if (typeof window === 'undefined') {
    return FALLBACK_THEME_VARS;
  }

  const styles = getComputedStyle(document.documentElement);
  const pick = (name: string, fallback: string) =>
    styles.getPropertyValue(name).trim() || fallback;

  return {
    bgPrimary: pick('--color-bg-primary', FALLBACK_THEME_VARS.bgPrimary),
    bgSecondary: pick('--color-bg-secondary', FALLBACK_THEME_VARS.bgSecondary),
    bgTertiary: pick('--color-bg-tertiary', FALLBACK_THEME_VARS.bgTertiary),
    textPrimary: pick('--color-text-primary', FALLBACK_THEME_VARS.textPrimary),
    textSecondary: pick(
      '--color-text-secondary',
      FALLBACK_THEME_VARS.textSecondary,
    ),
    textMuted: pick('--color-text-muted', FALLBACK_THEME_VARS.textMuted),
    accent: pick('--color-accent-primary', FALLBACK_THEME_VARS.accent),
    accentHover: pick('--color-accent-hover', FALLBACK_THEME_VARS.accentHover),
    border: pick('--color-border-primary', FALLBACK_THEME_VARS.border),
    borderSecondary: pick(
      '--color-border-secondary',
      FALLBACK_THEME_VARS.borderSecondary,
    ),
    statusSuccess: pick(
      '--color-status-success',
      FALLBACK_THEME_VARS.statusSuccess,
    ),
    statusWarning: pick(
      '--color-status-warning',
      FALLBACK_THEME_VARS.statusWarning,
    ),
    statusError: pick('--color-status-error', FALLBACK_THEME_VARS.statusError),
  };
};

const injectIntoDocument = (html: string, injection: string): string => {
  if (/<\/head>/i.test(html)) {
    return html.replace(/<\/head>/i, `${injection}</head>`);
  }

  if (/<body[^>]*>/i.test(html)) {
    return html.replace(/<body[^>]*>/i, `$&${injection}`);
  }

  return `${injection}${html}`;
};

function StaticArtifactFrame({ sanitized }: { sanitized: string }) {
  const [isExpanded, setIsExpanded] = useState(true);
  const [height, setHeight] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [showLoader, setShowLoader] = useState(false);
  const [isSlowLoading, setIsSlowLoading] = useState(false);
  const [hasError, setHasError] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const [themeVars, setThemeVars] = useState<ThemeVars>(FALLBACK_THEME_VARS);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    const syncTheme = () => {
      setThemeVars(readThemeVars());
    };

    syncTheme();

    const observer = new MutationObserver(syncTheme);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme', 'class', 'style'],
    });

    return () => observer.disconnect();
  }, []);

  useLayoutEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) {
        return;
      }

      if (event.data?.type !== 'artifact-height') {
        return;
      }

      const nextHeightRaw = Number(event.data.height);
      if (!Number.isFinite(nextHeightRaw) || nextHeightRaw <= 0) {
        return;
      }

      const nextHeight = Math.min(Math.ceil(nextHeightRaw), 2000);
      setHeight((prev) => {
        if (prev !== null && Math.abs(prev - nextHeight) < 8) {
          return prev;
        }
        return nextHeight;
      });

      setIsLoading(false);
      setShowLoader(false);
      setIsSlowLoading(false);
    };

    window.addEventListener('message', handleMessage);

    return () => window.removeEventListener('message', handleMessage);
  }, []);

  useEffect(() => {
    if (!isLoading || !isExpanded || hasError) return;

    const loaderTimer = window.setTimeout(() => {
      setShowLoader(true);
    }, 120);

    const slowTimer = window.setTimeout(() => {
      setIsSlowLoading(true);
    }, 1800);

    // Browsers can report iframe load even when navigation failed. The
    // existing resize message is evidence that the preview shell started.
    // The browser can throttle the frame in a hidden tab, so only time out
    // while the conversation is visible.
    let failureTimer: number | undefined;
    const watchForReady = () => {
      window.clearTimeout(failureTimer);
      if (document.hidden) return;
      failureTimer = window.setTimeout(() => {
        if (document.hidden) return;
        setHasError(true);
        setIsLoading(false);
      }, 8000);
    };
    watchForReady();
    document.addEventListener('visibilitychange', watchForReady);

    return () => {
      window.clearTimeout(loaderTimer);
      window.clearTimeout(slowTimer);
      window.clearTimeout(failureTimer);
      document.removeEventListener('visibilitychange', watchForReady);
    };
  }, [hasError, isExpanded, isLoading, reloadToken]);

  const srcDoc = useMemo(() => {
    const nonce = readCspNonce();
    const styleNonceAttr = nonce ? ` nonce="${nonce}"` : '';
    const scriptNonceAttr = nonce ? ` nonce="${nonce}"` : '';

    const shellStyles = `
<style${styleNonceAttr}>
  :root {
    --bg-primary: ${themeVars.bgPrimary};
    --bg-secondary: ${themeVars.bgSecondary};
    --bg-tertiary: ${themeVars.bgTertiary};
    --text-primary: ${themeVars.textPrimary};
    --text-secondary: ${themeVars.textSecondary};
    --text-muted: ${themeVars.textMuted};
    --accent: ${themeVars.accent};
    --accent-hover: ${themeVars.accentHover};
    --border: ${themeVars.border};
    --border-secondary: ${themeVars.borderSecondary};
    --status-success: ${themeVars.statusSuccess};
    --status-warning: ${themeVars.statusWarning};
    --status-error: ${themeVars.statusError};
  }

  * {
    box-sizing: border-box;
  }

  html,
  body {
    margin: 0;
    min-height: 100%;
    color: var(--text-primary);
    background: var(--bg-primary);
    font-family: "SF Pro Display", "Inter", "Segoe UI", system-ui, sans-serif;
    line-height: 1.5;
  }

  body {
    padding: 12px;
  }

  .artifact-root {
    width: min(980px, 100%);
    margin: 0 auto;
  }

  :where(h1, h2, h3, h4) {
    margin: 0 0 0.75rem;
    color: var(--text-primary);
    letter-spacing: -0.02em;
    line-height: 1.2;
  }

  :where(h1) {
    font-size: clamp(1.5rem, 3vw, 2rem);
  }

  :where(h2) {
    font-size: clamp(1.2rem, 2.2vw, 1.5rem);
  }

  :where(p, li, label, small) {
    color: var(--text-secondary);
  }

  :where(input, select, textarea, button) {
    font: inherit;
    transition: 160ms ease;
  }

  :where(input, select, textarea) {
    width: 100%;
    border-radius: 10px;
    border: 1px solid var(--border-secondary);
    background: var(--bg-secondary);
    color: var(--text-primary);
    padding: 0.58rem 0.72rem;
    outline: none;
  }

  :where(input:focus, select:focus, textarea:focus) {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 22%, transparent);
  }

  :where(button) {
    border: 1px solid color-mix(in srgb, var(--accent) 40%, var(--border-secondary));
    border-radius: 10px;
    background: var(--accent);
    color: white;
    padding: 0.55rem 0.9rem;
    font-weight: 600;
    cursor: pointer;
  }

  :where(button:hover) {
    background: var(--accent-hover);
  }

  :where(table) {
    width: 100%;
    border-collapse: collapse;
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
  }

  :where(th, td) {
    border-bottom: 1px solid var(--border);
    padding: 0.55rem 0.68rem;
    text-align: left;
  }

  :where(canvas, svg, img) {
    display: block;
    max-width: 100%;
    height: auto;
  }

  :where(.card) {
    border-radius: 12px;
    border: 1px solid var(--border);
    background: var(--bg-secondary);
    padding: 1rem;
  }
</style>`;

    const resizeScript = `
<script${scriptNonceAttr}>
  (function () {
    var ticking = false;
    var lastHeight = 0;

    function readHeight() {
      var body = document.body;
      var html = document.documentElement;
      if (!body || !html) {
        return 0;
      }

      return Math.max(
        body.scrollHeight,
        body.offsetHeight,
        html.scrollHeight,
        html.offsetHeight
      );
    }

    function postHeight() {
      var nextHeight = readHeight();
      if (!nextHeight || Math.abs(nextHeight - lastHeight) < 2) {
        return;
      }

      lastHeight = nextHeight;

      if (window.parent !== window) {
        window.parent.postMessage({
          type: 'artifact-height',
          height: nextHeight
        }, '*');
      }
    }

    function queueHeight() {
      if (ticking) {
        return;
      }

      ticking = true;
      requestAnimationFrame(function () {
        ticking = false;
        postHeight();
      });
    }

    if (typeof ResizeObserver !== 'undefined') {
      var resizeObserver = new ResizeObserver(queueHeight);
      if (document.documentElement) {
        resizeObserver.observe(document.documentElement);
      }
      if (document.body) {
        resizeObserver.observe(document.body);
      }
    }

    // Send the initial size even if the browser pauses animation frames for
    // an offscreen iframe. Subsequent resizes still coalesce through rAF.
    window.addEventListener('load', postHeight);
    window.addEventListener('resize', queueHeight);
    setTimeout(queueHeight, 40);
    setTimeout(queueHeight, 220);
    setTimeout(queueHeight, 850);
  })();
</script>`;

    if (/<html[\s>]/i.test(sanitized)) {
      return injectIntoDocument(sanitized, `${shellStyles}${resizeScript}`);
    }

    return `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  ${shellStyles}
  ${resizeScript}
</head>
<body>
  <main class="artifact-root">${sanitized}</main>
</body>
</html>`;
  }, [sanitized, themeVars]);

  const handleRetry = () => {
    setIsLoading(true);
    setShowLoader(false);
    setIsSlowLoading(false);
    setHasError(false);
    setHeight(null);
    setReloadToken((prev) => prev + 1);
  };

  return (
    <div className="my-2">
      <div className="mb-1 flex items-center justify-between px-0.5">
        <span className="truncate text-xs text-[var(--color-text-muted)]">
          Static HTML preview
        </span>

        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              if (!isExpanded && !hasError) handleRetry();
              setIsExpanded(!isExpanded);
            }}
            className="rounded p-0.5 text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text-secondary)]"
            aria-label={isExpanded ? 'Collapse artifact' : 'Expand artifact'}
          >
            {isExpanded ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
          </button>
        </div>
      </div>

      {isExpanded && (
        <div className="relative">
          {hasError ? (
            <div
              role="alert"
              className="rounded-xl border border-red-500/30 bg-red-500/10 p-4"
            >
              <div className="mb-2 flex items-center gap-2 text-red-400">
                <AlertCircle className="h-5 w-5" />
                <span className="text-sm font-medium">
                  HTML preview failed to render.
                </span>
              </div>

              <button
                onClick={handleRetry}
                className="inline-flex items-center gap-2 rounded-lg border border-red-400/30 bg-red-500/10 px-3 py-1.5 text-sm text-red-300 transition-colors hover:bg-red-500/20"
              >
                <RefreshCw className="h-4 w-4" />
                Retry
              </button>
            </div>
          ) : (
            <>
              {showLoader && isLoading && (
                <div className="absolute inset-0 z-10 flex items-center justify-center bg-[var(--color-bg-primary)]/94 backdrop-blur-sm">
                  <div className="w-full max-w-md px-6">
                    <div className="h-1.5 overflow-hidden rounded-full bg-[var(--color-bg-tertiary)]">
                      <div className="h-full w-1/2 animate-pulse rounded-full bg-[var(--color-accent-primary)]" />
                    </div>
                    <p className="mt-2 text-center text-xs text-[var(--color-text-muted)]">
                      {isSlowLoading
                        ? 'Preparing static view...'
                        : 'Loading HTML preview...'}
                    </p>
                  </div>
                </div>
              )}

              <iframe
                key={reloadToken}
                ref={iframeRef}
                srcDoc={srcDoc}
                sandbox="allow-scripts"
                title="Static HTML preview"
                className="min-h-60 w-full border-0 bg-transparent transition-opacity duration-200"
                style={{
                  height: `${height ?? 560}px`,
                  maxHeight: '2000px',
                  opacity: isLoading ? 0.6 : 1,
                }}
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}
