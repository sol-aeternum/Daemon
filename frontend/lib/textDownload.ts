/** Kinds of text the chat can hand to the user as a file. */
export type TextFileKind =
  | 'html'
  | 'svg'
  | 'markdown'
  | 'code'
  | 'csv'
  | 'json';

const KIND_FILE: Record<TextFileKind, { ext: string; mime: string }> = {
  html: { ext: 'html', mime: 'text/html' },
  svg: { ext: 'svg', mime: 'image/svg+xml' },
  markdown: { ext: 'md', mime: 'text/markdown' },
  code: { ext: 'txt', mime: 'text/plain' },
  csv: { ext: 'csv', mime: 'text/csv' },
  json: { ext: 'json', mime: 'application/json' },
};

// Extensions for `code` files. Unknown languages fall back to .txt.
const CODE_EXTENSIONS: Record<string, string> = {
  bash: 'sh',
  c: 'c',
  cpp: 'cpp',
  css: 'css',
  go: 'go',
  java: 'java',
  javascript: 'js',
  js: 'js',
  jsx: 'jsx',
  kotlin: 'kt',
  python: 'py',
  py: 'py',
  ruby: 'rb',
  rust: 'rs',
  sh: 'sh',
  sql: 'sql',
  swift: 'swift',
  toml: 'toml',
  ts: 'ts',
  tsx: 'tsx',
  typescript: 'ts',
  xml: 'xml',
  yaml: 'yaml',
  yml: 'yml',
};

export function slugifyFilename(title: string): string {
  const slug = title
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60)
    .replace(/-+$/, '');
  return slug || 'download';
}

export function textFileName(
  title: string,
  kind: TextFileKind,
  options: { language?: string; version?: number } = {},
): string {
  const ext =
    kind === 'code'
      ? (CODE_EXTENSIONS[options.language?.toLowerCase() ?? ''] ?? 'txt')
      : KIND_FILE[kind].ext;
  const version =
    options.version && options.version > 1 ? `-v${options.version}` : '';
  return `${slugifyFilename(title)}${version}.${ext}`;
}

export function textFileMime(kind: TextFileKind): string {
  return `${KIND_FILE[kind].mime};charset=utf-8`;
}

/**
 * Save text as a file without a server round trip. The object URL is used
 * only as a download target (never navigated to), so generated HTML does
 * not execute with the app's origin.
 */
export function downloadTextFile(
  content: string,
  filename: string,
  mime: string,
): void {
  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  try {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = 'noopener';
    anchor.style.display = 'none';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    // Let the click's navigation start before revoking.
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}
