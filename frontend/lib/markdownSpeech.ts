import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';

const speechParser = unified().use(remarkParse).use(remarkGfm);

interface MarkdownNode {
  type: string;
  value?: string;
  alt?: string;
  children?: MarkdownNode[];
}

/** Preserve content, including literal code/operators, not Markdown delimiters. */
function readableNode(node: MarkdownNode): string {
  if (node.type === 'definition' || node.type === 'thematicBreak') return '';
  if (node.type === 'image' || node.type === 'imageReference') {
    return node.alt ?? '';
  }
  if (node.type === 'break') return '\n';
  if (typeof node.value === 'string') return node.value;

  const children = node.children ?? [];
  const separator =
    node.type === 'root' ||
    node.type === 'list' ||
    node.type === 'listItem' ||
    node.type === 'blockquote' ||
    node.type === 'table'
      ? '\n'
      : node.type === 'tableRow'
        ? ', '
        : '';
  return children
    .map(readableNode)
    .filter((text) => text !== '')
    .join(separator);
}

/**
 * Server-side use only: reuse the same CommonMark/GFM parser as the response UI.
 * Reads the syntax tree directly, without HTML rendering/highlighting; it reuses
 * the already-installed parser packages now declared as direct dependencies.
 * No model, DOM or network access is needed. Raw HTML is
 * kept as literal text, as in the UI, and never interpreted or executed.
 */
export function markdownToSpeechText(markdown: string): string {
  return readableNode(speechParser.parse(markdown) as MarkdownNode);
}
