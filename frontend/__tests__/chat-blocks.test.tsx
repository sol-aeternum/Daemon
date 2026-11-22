import { createHash } from 'node:crypto';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ConversationDetails } from '../components/ConversationDetails';
import { MessageChatBlocks } from '../components/chat/MessageChatBlocks';
import { resolveChatBlocks } from '../lib/chatBlocks';
import type { ChatEvent } from '../lib/events';

const sha = (text: string) => createHash('sha256').update(text).digest('hex');
const ID_A = `blk_${'a'.repeat(16)}`;
const ID_B = `blk_${'b'.repeat(16)}`;
const PAGE = '<h1>Sales</h1><script>document.title = "ran"</script>';
const DOC = '# Quarterly report\n\nRevenue grew.';

function call(content: string, extra: Record<string, unknown> = {}): ChatEvent {
  return {
    type: 'tool_call',
    name: 'render_block',
    arguments: { kind: 'html', title: 'x', content, ...extra },
  } as ChatEvent;
}

function result(
  content: string,
  data: Record<string, unknown> = {},
): ChatEvent {
  return {
    type: 'tool_result',
    name: 'render_block',
    result: {
      success: true,
      data: {
        block_id: ID_A,
        version: 1,
        kind: 'html',
        title: 'Sales dashboard',
        display: 'inline',
        bytes: content.length,
        sha256: sha(content),
        ...data,
      },
    },
  } as ChatEvent;
}

describe('resolveChatBlocks', () => {
  it('pairs by content digest even when persisted calls precede all results', async () => {
    const events = [
      call(PAGE),
      call(DOC, { kind: 'markdown' }),
      result(DOC, { block_id: ID_B, kind: 'markdown', display: 'panel' }),
      result(PAGE),
    ];
    const blocks = await resolveChatBlocks([events]);
    expect(blocks.map((block) => [block.blockId, block.content])).toEqual([
      [ID_B, DOC],
      [ID_A, PAGE],
    ]);
  });

  it('drops forged, failed, malformed and advisor-scoped results', async () => {
    const events = [
      call(PAGE),
      result(PAGE, { sha256: sha('other') }),
      result(PAGE, { block_id: '../etc' }),
      result(PAGE, { kind: 'exe' }),
      { type: 'tool_result', name: 'render_block', result: { success: false } },
      { ...result(PAGE), advisor_id: 'adv' },
    ] as ChatEvent[];
    expect(await resolveChatBlocks([events])).toEqual([]);
  });

  it('re-checks inline placement on the client', async () => {
    const big = `<svg>${'x'.repeat(49 * 1024)}</svg>`;
    const blocks = await resolveChatBlocks([
      [
        call(big),
        result(big, { kind: 'svg' }),
        call(DOC),
        result(DOC, { block_id: ID_B, kind: 'markdown' }),
        call(PAGE),
        // html is never inline while execution is held off.
        result(PAGE, { block_id: `blk_${'c'.repeat(16)}` }),
      ],
    ]);
    expect(blocks.map((block) => block.display)).toEqual([
      'panel',
      'panel',
      'panel',
    ]);
  });

  it('takes metadata from the result, so identical sources keep their own', async () => {
    const source = 'print(1)';
    const blocks = await resolveChatBlocks([
      [
        call(source, { kind: 'code', language: 'python' }),
        call(source, { kind: 'code', language: 'ruby' }),
        result(source, { kind: 'code', title: 'A', language: 'python' }),
        result(source, {
          block_id: ID_B,
          kind: 'code',
          title: 'B',
          language: 'ruby',
        }),
      ],
    ]);
    expect(blocks.map((block) => [block.title, block.language])).toEqual([
      ['A', 'python'],
      ['B', 'ruby'],
    ]);
  });

  it('never pairs a call with a result from another message', async () => {
    expect(await resolveChatBlocks([[call(PAGE)], [result(PAGE)]])).toEqual([]);
  });
});

const SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><circle r="1"/></svg>';

describe('chat block cards', () => {
  it('renders svg passively as an image, never in the script frame', async () => {
    render(
      <MessageChatBlocks
        events={[
          call(SVG, { kind: 'svg' }),
          result(SVG, { kind: 'svg', title: 'Dot' }),
        ]}
        onOpen={vi.fn()}
      />,
    );
    const image = await screen.findByRole('img', { name: 'Dot' });
    expect(image.getAttribute('src')).toMatch(/^data:image\/svg\+xml/);
    expect(document.querySelector('iframe')).toBeNull();
    expect(screen.queryByRole('button', { name: /Run|Stop/ })).toBeNull();
  });

  it('downloads identical sources under their own language', async () => {
    const source = 'print(1)';
    const names: string[] = [];
    vi.stubGlobal(
      'URL',
      Object.assign(URL, {
        createObjectURL: vi.fn(() => 'blob:x'),
        revokeObjectURL: vi.fn(),
      }),
    );
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      names.push(this.download);
    });
    render(
      <MessageChatBlocks
        events={[
          call(source, { kind: 'code', language: 'python' }),
          call(source, { kind: 'code', language: 'ruby' }),
          result(source, {
            kind: 'code',
            title: 'A',
            display: 'panel',
            language: 'python',
          }),
          result(source, {
            block_id: ID_B,
            kind: 'code',
            title: 'B',
            display: 'panel',
            language: 'ruby',
          }),
        ]}
        onOpen={vi.fn()}
      />,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Download A' }));
    fireEvent.click(screen.getByRole('button', { name: 'Download B' }));
    expect(names).toEqual(['a.py', 'b.rb']);
  });

  it('never runs an html block: it is a card, even when marked inline', async () => {
    const onOpen = vi.fn();
    render(
      <MessageChatBlocks events={[call(PAGE), result(PAGE)]} onOpen={onOpen} />,
    );
    const open = await screen.findByRole('button', {
      name: /Open Sales dashboard in panel/,
    });
    expect(document.querySelector('iframe')).toBeNull();
    expect(screen.queryByRole('button', { name: /^(Run|Stop)$/ })).toBeNull();
    fireEvent.click(open);
    expect(onOpen).toHaveBeenCalledWith(`${ID_A}:v1`);
  });

  it('shows larger blocks as a compact card without running anything', async () => {
    render(
      <MessageChatBlocks
        events={[
          call(DOC, { kind: 'markdown' }),
          result(DOC, { kind: 'markdown', title: 'Report', display: 'panel' }),
        ]}
        onOpen={vi.fn()}
      />,
    );
    expect(await screen.findAllByText('Report')).not.toHaveLength(0);
    expect(document.querySelector('iframe')).toBeNull();
    expect(
      screen.getByRole('button', { name: /Download Report/ }),
    ).toBeTruthy();
  });
});

describe('details panel', () => {
  const turns = [
    {
      id: 'turn',
      running: false,
      stopped: false,
      events: [
        call(PAGE),
        result(PAGE, { display: 'panel', version: 2 }),
        call(DOC, { kind: 'markdown' }),
        result(DOC, { block_id: ID_B, kind: 'markdown', title: 'Report' }),
      ],
    },
  ];

  it('lists blocks and shows html as source, never executed', async () => {
    const onSelectBlock = vi.fn();
    const view = render(
      <ConversationDetails
        turns={turns}
        tab="Outputs"
        onTab={vi.fn()}
        onSelect={vi.fn()}
        onSelectBlock={onSelectBlock}
        onClose={vi.fn()}
        modal={false}
      />,
    );
    const preview = await screen.findByRole('button', {
      name: 'Preview Sales dashboard',
    });
    fireEvent.click(preview);
    expect(onSelectBlock).toHaveBeenCalledWith(`${ID_A}:v2`);

    view.rerender(
      <ConversationDetails
        turns={turns}
        tab="Outputs"
        onTab={vi.fn()}
        onSelect={vi.fn()}
        selectedBlockKey={`${ID_A}:v2`}
        onSelectBlock={onSelectBlock}
        onClose={vi.fn()}
        modal={false}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText(/isn't enabled yet/)).toBeTruthy(),
    );
    expect(screen.getByText(PAGE)).toBeTruthy();
    expect(document.querySelector('iframe')).toBeNull();
    expect(screen.queryByRole('button', { name: /^(Run|Stop)$/ })).toBeNull();
  });

  it('shows svg passively in Details and pairs calls per turn', async () => {
    const svgTurns = [
      {
        id: 'svg-turn',
        running: false,
        stopped: false,
        events: [
          call(SVG, { kind: 'svg' }),
          result(SVG, { block_id: ID_B, kind: 'svg', title: 'Dot' }),
        ],
      },
      // A result whose source only appears in another turn is not shown.
      { id: 'orphan', running: false, stopped: false, events: [result(PAGE)] },
    ];
    render(
      <ConversationDetails
        turns={svgTurns}
        tab="Outputs"
        onTab={vi.fn()}
        onSelect={vi.fn()}
        selectedBlockKey={`${ID_B}:v1`}
        onSelectBlock={vi.fn()}
        onClose={vi.fn()}
        modal={false}
      />,
    );
    expect(await screen.findByRole('img', { name: 'Dot' })).toBeTruthy();
    expect(document.querySelector('iframe')).toBeNull();
    expect(
      screen.queryByRole('button', { name: 'Preview Sales dashboard' }),
    ).toBeNull();
  });

  it('renders markdown blocks as a document', async () => {
    render(
      <ConversationDetails
        turns={turns}
        tab="Outputs"
        onTab={vi.fn()}
        onSelect={vi.fn()}
        selectedBlockKey={`${ID_B}:v1`}
        onSelectBlock={vi.fn()}
        onClose={vi.fn()}
        modal={false}
      />,
    );
    expect(
      await screen.findByRole('heading', { name: 'Quarterly report' }),
    ).toBeTruthy();
  });
});
