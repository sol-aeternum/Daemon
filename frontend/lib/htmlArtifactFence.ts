// Markdown identifies which blocks are code. Source positions then let us
// distinguish a closing fence from EOF and avoid rendered/highlighted children.
export const HTML_ARTIFACT_LANGUAGE = /^html[:-]interactive$/i;

export function hasHtmlArtifactFence(markdown: string): boolean {
  return /(?:```|~~~)[ \t]*html[:-]interactive(?:[ \t\r\n]|$)/i.test(markdown);
}

export function readHtmlArtifactFence(
  markdown: string,
  start: number,
  end: number,
  parsedBody: string,
): { source: string; closed: boolean } | null {
  const block = markdown.slice(start, end);
  const opener = /^(`{3,}|~{3,})[ \t]*([^ \t\r\n]+)[^\r\n]*(\r\n|\r|\n|$)/.exec(
    block,
  );
  if (!opener || !HTML_ARTIFACT_LANGUAGE.test(opener[2])) return null;
  const rest = block.slice(opener[0].length);
  const lines = rest.split(/\r\n|\r|\n/);
  const candidate = lines.at(-1) ?? '';
  const closing = /^[ \t>]*(`{3,}|~{3,})[ \t]*$/.exec(candidate);
  // A real closing marker occupies a source line but not a code-value line.
  // This also handles Markdown containers that strip > or indentation.
  const closed =
    !!closing &&
    closing[1][0] === opener[1][0] &&
    closing[1].length >= opener[1].length &&
    (lines.length === 1
      ? parsedBody === ''
      : parsedBody.split(/\r\n|\r|\n/).length < lines.length);
  const raw = closed ? rest.slice(0, rest.length - candidate.length) : rest;
  const prefix = markdown.slice(
    Math.max(
      markdown.lastIndexOf('\n', start - 1),
      markdown.lastIndexOf('\r', start - 1),
    ) + 1,
    start,
  );
  // Container markers/indentation are Markdown syntax, not part of HTML.
  // Root-level blocks use the exact original bytes, including the last EOL.
  const trailingEol = /(?:\r\n|\r|\n)$/.exec(raw)?.[0] ?? '';
  return {
    source: prefix.trim() ? parsedBody + (parsedBody ? trailingEol : '') : raw,
    closed,
  };
}
