import type { Page } from '@playwright/test';

type FontNode = {
  nodeId: number;
  nodeType: number;
  nodeName: string;
  nodeValue?: string;
  children?: FontNode[];
};

type PlatformFont = {
  familyName: string;
  postScriptName: string;
  isCustomFont: boolean;
  glyphCount: number;
};

// Computed font-family reports only the CSS stack, not the face Chromium used.
// Query actual text-bearing nodes through CDP; do not log their text content.
export async function resolvedFonts(page: Page) {
  const session = await page.context().newCDPSession(page);
  try {
    await session.send('DOM.enable');
    await session.send('CSS.enable');
    const { root } = (await session.send('DOM.getDocument', { depth: -1 })) as {
      root: FontNode;
    };
    const nodes: number[] = [];
    const visit = (node: FontNode) => {
      if (['HEAD', 'SCRIPT', 'STYLE', 'TEMPLATE'].includes(node.nodeName))
        return;
      if (
        node.nodeType === 1 &&
        node.children?.some(
          (child) => child.nodeType === 3 && child.nodeValue?.trim(),
        )
      )
        nodes.push(node.nodeId);
      for (const child of node.children ?? []) visit(child);
    };
    visit(root);
    const fonts = new Map<string, PlatformFont>();
    for (const nodeId of nodes) {
      const result = (await session.send('CSS.getPlatformFontsForNode', {
        nodeId,
      })) as {
        fonts: PlatformFont[];
      };
      for (const font of result.fonts) {
        const key = `${font.postScriptName}:${font.isCustomFont}`;
        const previous = fonts.get(key);
        fonts.set(key, {
          ...font,
          glyphCount: font.glyphCount + (previous?.glyphCount ?? 0),
        });
      }
    }
    return [...fonts.values()].sort((a, b) =>
      a.postScriptName.localeCompare(b.postScriptName),
    );
  } finally {
    await session.detach();
  }
}
