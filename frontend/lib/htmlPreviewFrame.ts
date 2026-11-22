export const HTML_PREVIEW_FRAME_PATH = '/api/previews/html';
export const HTML_PREVIEW_MESSAGE_TYPE = 'daemon:html-preview';
/** Sent by the frame document to its parent with the content height. */
export const HTML_PREVIEW_HEIGHT_TYPE = 'daemon:html-preview-height';
/** Sent by the generated content to the frame document (never to the app). */
export const HTML_PREVIEW_CONTENT_HEIGHT_TYPE = 'daemon:html-content-height';
/** Upper bound for an auto-sized frame, enforced by both frame and parent. */
export const HTML_PREVIEW_MAX_HEIGHT = 2000;

/**
 * Theme tokens forwarded to generated content as CSS custom properties.
 * Keys are fixed here; the frame document re-validates every value.
 */
export const HTML_PREVIEW_THEME_TOKENS = {
  '--bg-primary': '--color-bg-primary',
  '--bg-secondary': '--color-bg-secondary',
  '--bg-tertiary': '--color-bg-tertiary',
  '--text-primary': '--color-text-primary',
  '--text-secondary': '--color-text-secondary',
  '--text-muted': '--color-text-muted',
  '--accent': '--color-accent-primary',
  '--border': '--color-border-primary',
  '--status-success': '--color-status-success',
  '--status-warning': '--color-status-warning',
  '--status-error': '--color-status-error',
} as const;

export type HtmlPreviewTheme = Partial<
  Record<keyof typeof HTML_PREVIEW_THEME_TOKENS, string>
>;

export interface HtmlPreviewMessage {
  type: typeof HTML_PREVIEW_MESSAGE_TYPE;
  content: string;
  title: string;
  theme?: HtmlPreviewTheme;
}

export interface HtmlPreviewHeightMessage {
  type: typeof HTML_PREVIEW_HEIGHT_TYPE;
  height: number;
}

const THEME_VALUE = /^[-#%(),./\w\s]{1,64}$/;

/** Read the app's current theme tokens; values failing validation are dropped. */
export function readHtmlPreviewTheme(): HtmlPreviewTheme {
  if (typeof document === 'undefined') return {};
  const styles = getComputedStyle(document.documentElement);
  const theme: HtmlPreviewTheme = {};
  for (const [token, source] of Object.entries(HTML_PREVIEW_THEME_TOKENS)) {
    const value = styles.getPropertyValue(source).trim();
    if (THEME_VALUE.test(value)) {
      theme[token as keyof HtmlPreviewTheme] = value;
    }
  }
  return theme;
}

/** Parse a height message from the frame document; null when malformed. */
export function readHtmlPreviewHeight(data: unknown): number | null {
  if (!data || typeof data !== 'object') return null;
  const message = data as Partial<HtmlPreviewHeightMessage>;
  if (message.type !== HTML_PREVIEW_HEIGHT_TYPE) return null;
  const height = message.height;
  if (typeof height !== 'number' || !Number.isFinite(height) || height <= 0) {
    return null;
  }
  return Math.min(Math.ceil(height), HTML_PREVIEW_MAX_HEIGHT);
}
