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
    const blocks = await resolveChatBlocks(events);
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
    expect(await resolveChatBlocks(events)).toEqual([]);
  });

  it('re-checks inline placement on the client', async () => {
    const big = `<p>${'x'.repeat(49 * 1024)}</p>`;
    const blocks = await resolveChatBlocks([
      call(big),
      result(big),
      call(DOC),
      result(DOC, { block_id: ID_B, kind: 'markdown' }),
    ]);
    expect(blocks.map((block) => block.display)).toEqual(['panel', 'panel']);
  });
});

describe('chat block cards', () => {
  it('runs a small html block inline in the sandbox, with stop and code views', async () => {
    const onOpen = vi.fn();
    render(
      <MessageChatBlocks events={[call(PAGE), result(PAGE)]} onOpen={onOpen} />,
    );
    const frame = await screen.findByTitle('Sales dashboard');
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts');
    expect(frame.getAttribute('src')).toBe('/api/previews/html');

    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    expect(screen.queryByTitle('Sales dashboard')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Run' }));
    expect(screen.getByTitle('Sales dashboard')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Code' }));
    expect(screen.getByText(PAGE)).toBeTruthy();

    fireEvent.click(
      screen.getByRole('button', { name: /Open Sales dashboard in panel/ }),
    );
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

  it('lists blocks and runs html only after an explicit click', async () => {
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
      expect(screen.getByText(/isolated sandbox with no network/)).toBeTruthy(),
    );
    expect(document.querySelector('iframe')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Run' }));
    expect(screen.getByTitle('Sales dashboard')).toBeTruthy();
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
