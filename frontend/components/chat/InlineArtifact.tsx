"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, ChevronDown, ChevronRight, ExternalLink, RefreshCw } from "lucide-react";
import { sanitizeHtml } from "../../lib/sanitizeHtml";

interface InlineArtifactProps {
  htmlContent: string;
  title?: string;
  artifactId?: string;
}

/**
 * Read the per-request CSP nonce from the <meta name="csp-nonce"> tag
 * that the root layout renders from the `x-nonce` request header
 * (frontend/proxy.ts). The nonce is used to mark the inline <style> and
 * <script> tags inside the srcDoc so the iframe's inherited CSP does
 * not block the artifact shell.
 */
function readCspNonce(): string | undefined {
  if (typeof document === "undefined") {
    return undefined;
  }
  const meta = document.querySelector<HTMLMetaElement>('meta[name="csp-nonce"]');
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
  bgPrimary: "hsl(220, 13%, 10%)",
  bgSecondary: "hsl(220, 15%, 14%)",
  bgTertiary: "hsl(220, 13%, 18%)",
  textPrimary: "hsl(220, 20%, 96%)",
  textSecondary: "hsl(220, 15%, 75%)",
  textMuted: "hsl(220, 10%, 55%)",
  accent: "hsl(215, 60%, 55%)",
  accentHover: "hsl(215, 65%, 60%)",
  border: "hsl(220, 15%, 22%)",
  borderSecondary: "hsl(220, 15%, 28%)",
  statusSuccess: "hsl(145, 65%, 45%)",
  statusWarning: "hsl(38, 85%, 55%)",
  statusError: "hsl(0, 75%, 55%)",
};

const readThemeVars = (): ThemeVars => {
  if (typeof window === "undefined") {
    return FALLBACK_THEME_VARS;
  }

  const styles = getComputedStyle(document.documentElement);
  const pick = (name: string, fallback: string) => styles.getPropertyValue(name).trim() || fallback;

  return {
    bgPrimary: pick("--color-bg-primary", FALLBACK_THEME_VARS.bgPrimary),
    bgSecondary: pick("--color-bg-secondary", FALLBACK_THEME_VARS.bgSecondary),
    bgTertiary: pick("--color-bg-tertiary", FALLBACK_THEME_VARS.bgTertiary),
    textPrimary: pick("--color-text-primary", FALLBACK_THEME_VARS.textPrimary),
    textSecondary: pick("--color-text-secondary", FALLBACK_THEME_VARS.textSecondary),
    textMuted: pick("--color-text-muted", FALLBACK_THEME_VARS.textMuted),
    accent: pick("--color-accent-primary", FALLBACK_THEME_VARS.accent),
    accentHover: pick("--color-accent-hover", FALLBACK_THEME_VARS.accentHover),
    border: pick("--color-border-primary", FALLBACK_THEME_VARS.border),
    borderSecondary: pick("--color-border-secondary", FALLBACK_THEME_VARS.borderSecondary),
    statusSuccess: pick("--color-status-success", FALLBACK_THEME_VARS.statusSuccess),
    statusWarning: pick("--color-status-warning", FALLBACK_THEME_VARS.statusWarning),
    statusError: pick("--color-status-error", FALLBACK_THEME_VARS.statusError),
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

const escapeSingleQuotedJsString = (value: string): string =>
  value.replace(/\\/g, "\\\\").replace(/'/g, "\\'");

export function InlineArtifact({ htmlContent, title, artifactId }: InlineArtifactProps) {
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
      attributeFilter: ["data-theme", "class", "style"],
    });

    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setIsLoading(true);
    setShowLoader(false);
    setIsSlowLoading(false);
    setHasError(false);
    setHeight(null);
  }, [htmlContent, reloadToken]);

  useEffect(() => {
    const fallbackTimer = window.setTimeout(() => {
      setHeight((prev) => prev ?? 560);
    }, 500);

    const handleMessage = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) {
        return;
      }

      if (event.data?.type !== "artifact-height") {
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

    window.addEventListener("message", handleMessage);

    return () => {
      window.removeEventListener("message", handleMessage);
      window.clearTimeout(fallbackTimer);
    };
  }, [htmlContent, reloadToken]);

  useEffect(() => {
    if (!isLoading) {
      setShowLoader(false);
      setIsSlowLoading(false);
      return;
    }

    const loaderTimer = window.setTimeout(() => {
      setShowLoader(true);
    }, 120);

    const slowTimer = window.setTimeout(() => {
      setIsSlowLoading(true);
    }, 1800);

    return () => {
      window.clearTimeout(loaderTimer);
      window.clearTimeout(slowTimer);
    };
  }, [isLoading]);

  const srcDoc = useMemo(() => {
    const sanitized = sanitizeHtml(htmlContent);
    const safeArtifactId = escapeSingleQuotedJsString(artifactId ?? "");
    const nonce = readCspNonce();
    const styleNonceAttr = nonce ? ` nonce="${nonce}"` : "";
    const scriptNonceAttr = nonce ? ` nonce="${nonce}"` : "";

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
          height: nextHeight,
          artifactId: '${safeArtifactId}'
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

    window.addEventListener('load', queueHeight);
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
  }, [artifactId, htmlContent, themeVars]);

  const handleIframeLoad = () => {
    setIsLoading(false);
    setShowLoader(false);
    setIsSlowLoading(false);
  };

  const handleIframeError = () => {
    setHasError(true);
    setIsLoading(false);
    setShowLoader(false);
    setIsSlowLoading(false);
  };

  const handleRetry = () => {
    setReloadToken((prev) => prev + 1);
  };

  return (
    <div className="my-2" role="region" aria-label={title || "Interactive artifact"}>
      <div className="mb-1 flex items-center justify-between px-0.5">
        <span className="truncate text-[11px] text-[var(--color-text-muted)]">{title || "Interactive artifact"}</span>

        <div className="flex items-center gap-2">
          {artifactId && (
            <a
              href={`/artifacts?artifact=${encodeURIComponent(artifactId)}`}
              className="inline-flex items-center gap-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-accent-primary)]"
              target="_blank"
              rel="noopener noreferrer"
            >
              Gallery
              <ExternalLink className="h-3 w-3" />
            </a>
          )}

          <button
            onClick={() => setIsExpanded((prev) => !prev)}
            className="rounded p-0.5 text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text-secondary)]"
            aria-label={isExpanded ? "Collapse artifact" : "Expand artifact"}
          >
            {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </button>
        </div>
      </div>

      {isExpanded && (
        <div className="relative">
          {hasError ? (
            <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-4">
              <div className="mb-2 flex items-center gap-2 text-red-400">
                <AlertCircle className="h-5 w-5" />
                <span className="text-sm font-medium">Artifact failed to render</span>
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
                      {isSlowLoading ? "Preparing interactive view..." : "Loading artifact..."}
                    </p>
                  </div>
                </div>
              )}

              <iframe
                key={`${artifactId ?? "artifact"}-${reloadToken}`}
                ref={iframeRef}
                srcDoc={srcDoc}
                sandbox="allow-scripts"
                title={title || "Artifact"}
                onLoad={handleIframeLoad}
                onError={handleIframeError}
                className="min-h-[240px] w-full border-0 bg-transparent transition-opacity duration-200"
                style={{
                  height: `${height ?? 560}px`,
                  maxHeight: "2000px",
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
