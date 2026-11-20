// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resourcePath } from '@/lib/resources';
import { Pilot } from './fixtures/openui487/Pilot';
import {
  OPENUI487_ALPHA_CSV,
  OPENUI487_FIXTURE,
  OPENUI487_LAYOUT_CHUNKS,
  OPENUI487_RECORDED_LAYOUT,
  OPENUI487_RESULT_CSV,
  OPENUI487_SESSION_A,
  OPENUI487_SESSION_B,
  createFixtureAdapter,
  type OpenUiFixture,
} from './fixtures/openui487/fixture';
import {
  PILOT_LAYOUT_MAX_BYTES,
  PILOT_LAYOUT_MAX_CHUNKS,
  parsePilotLayout,
  resolvePilotLayout,
  serializePilotLayout,
  type PilotBlock,
  type PilotLayoutFrame,
  type PilotLayoutInput,
  type PilotLayoutRejection,
} from './fixtures/openui487/layout';

const auth = vi.hoisted(() => ({
  generation: 1,
  listeners: new Set<() => void>(),
  header: vi.fn<() => Promise<string | null>>(),
}));

vi.mock('@/lib/auth', () => ({
  ensureAuthHeader: auth.header,
  getAuthGeneration: () => auth.generation,
  subscribeAuthGeneration: (listener: () => void) => {
    auth.listeners.add(listener);
    return () => auth.listeners.delete(listener);
  },
}));

beforeEach(() => {
  auth.generation = 1;
  auth.listeners.clear();
  auth.header.mockReset().mockResolvedValue('Bearer synthetic-owner-a');
  vi.stubGlobal('fetch', vi.fn());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  expect(auth.listeners.size).toBe(0);
});

const frame: PilotLayoutFrame = {
  schema_version: 1,
  recorded_at: '2026-11-20T12:00:00Z',
  text: OPENUI487_RECORDED_LAYOUT,
};
const ACTIVE_ORDER = 'Figures>TaskStatus>ArtifactResults';
const FALLBACK_ORDER = 'TaskStatus>ArtifactResults>Figures';
const expectedFigureRows = [
  ['Monday', '3', 'deliveries'],
  ['Tuesday', '5', 'deliveries'],
  ['Wednesday', '2', 'deliveries'],
];

const figureRows = (table: HTMLElement) =>
  Array.from(table.querySelectorAll('tbody tr'), (row) =>
    Array.from(row.children, (cell) => cell.textContent),
  );

function renderPilot(
  overrides: Partial<Parameters<typeof Pilot>[0]> = {},
): ReturnType<typeof render> {
  const { layoutEnabled = true, ...rest } = overrides;
  return render(
    <Pilot
      fixture={OPENUI487_FIXTURE}
      session={OPENUI487_SESSION_A}
      status="completed"
      layout={{ frame, final: true }}
      layoutEnabled={layoutEnabled}
      {...rest}
    />,
  );
}

function pilot() {
  return screen.getByTestId('openui487-pilot');
}

function resultCard() {
  const card = screen.getByText('fictional-result.csv').closest('article');
  if (!card) throw new Error('result card missing');
  return card;
}

it('uses a deterministic 64-byte recording in four transport chunks and two pins', () => {
  expect(OPENUI487_RECORDED_LAYOUT).toBe(
    'root = PilotLayout([Figures(), TaskStatus(), ArtifactResults()])',
  );
  expect(new TextEncoder().encode(OPENUI487_RECORDED_LAYOUT).length).toBe(64);
  expect(OPENUI487_LAYOUT_CHUNKS).toHaveLength(4);
  expect(OPENUI487_LAYOUT_CHUNKS.join('')).toBe(OPENUI487_RECORDED_LAYOUT);
  expect(ACTIVE_ORDER).not.toBe(FALLBACK_ORDER);
  expect(OPENUI487_FIXTURE.envelope.items).toHaveLength(2);
  expect(
    new Set(
      OPENUI487_FIXTURE.envelope.items.map(
        (pin) => `${pin.resource_id}:${pin.version_id}`,
      ),
    ).size,
  ).toBe(2);
  expect(OPENUI487_FIXTURE.records.filter((r) => r.pinned)).toHaveLength(2);
  const pinned = OPENUI487_FIXTURE.records.filter((r) => r.pinned);
  expect(pinned.map((record) => record.item.measured_bytes)).toEqual([
    new TextEncoder().encode(OPENUI487_ALPHA_CSV).length,
    new TextEncoder().encode(OPENUI487_RESULT_CSV).length,
  ]);
  expect(
    pinned.every((record) => record.item.representation === 'generated_file'),
  ).toBe(true);
  expect(
    pinned.every((record) => record.item.reference?.role === 'output'),
  ).toBe(true);
});

describe('closed layout grammar', () => {
  const permutations: readonly (readonly PilotBlock[])[] = [
    ['Figures', 'TaskStatus', 'ArtifactResults'],
    ['Figures', 'ArtifactResults', 'TaskStatus'],
    ['TaskStatus', 'Figures', 'ArtifactResults'],
    ['TaskStatus', 'ArtifactResults', 'Figures'],
    ['ArtifactResults', 'Figures', 'TaskStatus'],
    ['ArtifactResults', 'TaskStatus', 'Figures'],
  ];

  it('accepts the canonical order and all permutations, with zero props', () => {
    for (const order of permutations) {
      const text = serializePilotLayout(order);
      expect(text).toBe(
        `root = PilotLayout([${order.map((block) => `${block}()`).join(', ')}])`,
      );
      expect(parsePilotLayout(text)).toEqual({ ok: true, order: [...order] });
    }
  });

  it.each([
    [
      'missing',
      'root = PilotLayout([TaskStatus(), ArtifactResults()])',
      'layout_missing_block',
    ],
    [
      'duplicate',
      'root = PilotLayout([TaskStatus(), TaskStatus(), ArtifactResults()])',
      'layout_duplicate_block',
    ],
    [
      'unknown',
      'root = PilotLayout([TaskStatus(), ArtifactResults(), Notes()])',
      'layout_unknown_block',
    ],
    [
      'props',
      'root = PilotLayout([TaskStatus(1), ArtifactResults(), Figures()])',
      'layout_forbidden_props',
    ],
    [
      'non-ASCII',
      'root = PilotLayout([TaskStátus(), ArtifactResults(), Figures()])',
      'layout_non_ascii',
    ],
    [
      'empty segment',
      'root = PilotLayout([TaskStatus(),,ArtifactResults(),Figures()])',
      'layout_invalid_syntax',
    ],
    [
      'trailing comma',
      'root = PilotLayout([TaskStatus(), ArtifactResults(), Figures(),])',
      'layout_invalid_syntax',
    ],
  ] as const)('rejects %s', (_name, text, reason) => {
    expect(parsePilotLayout(text)).toEqual({ ok: false, reason });
  });

  it('caps the frame and the accumulated chunks before join/parse', () => {
    const oversize = `${OPENUI487_RECORDED_LAYOUT}${'A'.repeat(
      PILOT_LAYOUT_MAX_BYTES,
    )}`;
    expect(parsePilotLayout(oversize)).toEqual({
      ok: false,
      reason: 'layout_too_large',
    });
    expect(
      resolvePilotLayout(
        { frame: { ...frame, text: oversize }, final: true },
        true,
      ).rejection,
    ).toBe('layout_too_large');
    expect(
      resolvePilotLayout(
        {
          frame,
          chunks: ['x'.repeat(PILOT_LAYOUT_MAX_BYTES + 1)],
          final: false,
        },
        true,
      ).rejection,
    ).toBe('layout_too_large');
    expect(
      resolvePilotLayout(
        {
          frame,
          chunks: Array<string>(PILOT_LAYOUT_MAX_CHUNKS + 1).fill(''),
          final: false,
        },
        true,
      ).rejection,
    ).toBe('layout_too_large');
  });

  it('never activates any partial split, and only a final complete snapshot', () => {
    const payload = OPENUI487_RECORDED_LAYOUT;
    for (let split = 1; split < payload.length; split += 1) {
      const prefix = resolvePilotLayout(
        {
          frame,
          chunks: [payload.slice(0, split)],
          final: false,
        },
        true,
      );
      expect(prefix.active).toBe(false);
      expect(prefix.rejection).toBe('layout_pending_final_bit');

      const streamed = resolvePilotLayout(
        {
          frame,
          chunks: [payload.slice(0, split), payload.slice(split)],
          final: false,
        },
        true,
      );
      expect(streamed.active).toBe(false);
      expect(streamed.rejection).toBe('layout_pending_final_bit');

      const complete = resolvePilotLayout(
        {
          frame,
          chunks: [payload.slice(0, split), payload.slice(split)],
          final: true,
        },
        true,
      );
      expect(complete.active).toBe(true);
      expect(complete.order).toEqual([
        'Figures',
        'TaskStatus',
        'ArtifactResults',
      ]);
    }

    expect(
      resolvePilotLayout({ frame, chunks: [payload], final: false }, true)
        .rejection,
    ).toBe('layout_pending_final_bit');
    expect(
      resolvePilotLayout(
        {
          frame,
          chunks: [payload.slice(0, -1)],
          final: true,
        },
        true,
      ).rejection,
    ).toBe('layout_chunk_mismatch');
  });

  it('rejects unversioned, unsupported and mismatched frames before parsing', () => {
    expect(
      resolvePilotLayout(
        {
          frame: {
            text: OPENUI487_RECORDED_LAYOUT,
          } as unknown as PilotLayoutFrame,
          final: true,
        },
        true,
      ).rejection,
    ).toBe('layout_unversioned');
    expect(
      resolvePilotLayout(
        {
          frame: {
            ...frame,
            schema_version: 99,
          } as unknown as PilotLayoutFrame,
          final: true,
        },
        true,
      ).rejection,
    ).toBe('layout_unsupported_version');
    expect(
      resolvePilotLayout(
        {
          frame,
          chunks: [OPENUI487_LAYOUT_CHUNKS[0]],
          final: true,
        },
        true,
      ).rejection,
    ).toBe('layout_chunk_mismatch');
  });
});

describe('fixture-only adapter', () => {
  it('exposes exactly the two approved generated outputs for owner A', () => {
    const view = createFixtureAdapter(
      OPENUI487_FIXTURE,
      OPENUI487_SESSION_A,
    ).view();
    expect(view.authorized).toBe(true);
    if (!view.authorized) throw new Error('owner A should be authorized');
    expect(view.items.map((item) => item.display.name)).toEqual([
      'fictional-alpha.csv',
      'fictional-result.csv',
    ]);
    expect(
      view.items.every(
        (item) =>
          item.representation === 'generated_file' && item.kind === 'file',
      ),
    ).toBe(true);
    expect(view.figures).toBe(OPENUI487_FIXTURE.figures);
  });

  it('denies owner B, stale generations and mismatched scopes', () => {
    const pin = OPENUI487_FIXTURE.envelope.items[0];
    for (const [session, reason] of [
      [OPENUI487_SESSION_B, 'account_mismatch'],
      [{ ...OPENUI487_SESSION_A, generation: 8 }, 'generation_mismatch'],
      [
        {
          ...OPENUI487_SESSION_A,
          taskId: 'c0000000-0000-4000-8000-000000000099',
        },
        'task_mismatch',
      ],
      [
        {
          ...OPENUI487_SESSION_A,
          conversationId: 'c0000000-0000-4000-8000-000000000099',
        },
        'conversation_mismatch',
      ],
      [
        { ...OPENUI487_SESSION_A, messageId: 'different-result' },
        'message_mismatch',
      ],
    ] as const) {
      const adapter = createFixtureAdapter(OPENUI487_FIXTURE, session);
      expect(adapter.view()).toMatchObject({ authorized: false, reason });
      expect(adapter.resolve(pin)).toMatchObject({ ok: false, reason });
    }
  });

  it('denies forged, unpinned and removed records but keeps native owner data when the envelope is unusable', () => {
    const adapter = createFixtureAdapter(
      OPENUI487_FIXTURE,
      OPENUI487_SESSION_A,
    );
    const forged = OPENUI487_FIXTURE.records.find((record) =>
      record.item.display.name?.startsWith('forged'),
    )!;
    expect(adapter.resolve(forged.item)).toMatchObject({
      ok: false,
      reason: 'not_pinned',
    });
    expect(adapter.resolve(forged.item)).toMatchObject({
      ok: false,
      reason: 'not_pinned',
    });
    expect(
      adapter.resolve({
        resource_id: OPENUI487_FIXTURE.envelope.items[1].resource_id,
        version_id: 'f0000000-0000-4000-8000-000000000099',
      }),
    ).toMatchObject({ ok: false, reason: 'not_pinned' });

    const removedPin = OPENUI487_FIXTURE.nativePins[0];
    const removedFixture = {
      ...OPENUI487_FIXTURE,
      records: OPENUI487_FIXTURE.records.slice(1),
    };
    const removed = createFixtureAdapter(removedFixture, OPENUI487_SESSION_A);
    expect(removed.resolve(removedPin)).toMatchObject({
      ok: false,
      reason: 'removed_record',
    });

    const duplicatedFixture: OpenUiFixture = {
      ...OPENUI487_FIXTURE,
      records: [
        ...OPENUI487_FIXTURE.records,
        {
          ...OPENUI487_FIXTURE.records[0],
          ownerId: OPENUI487_FIXTURE.accountB,
        },
      ],
    };
    expect(
      createFixtureAdapter(duplicatedFixture, OPENUI487_SESSION_A).resolve(
        OPENUI487_FIXTURE.envelope.items[0],
      ),
    ).toMatchObject({ ok: false, reason: 'forged_record' });

    const unsupported: OpenUiFixture = {
      ...OPENUI487_FIXTURE,
      records: [
        ...OPENUI487_FIXTURE.records.slice(0, 3),
        { ...forged, pinned: true },
      ],
      envelope: {
        ...OPENUI487_FIXTURE.envelope,
        component_library_version: 99,
      },
    };
    const native = createFixtureAdapter(
      unsupported,
      OPENUI487_SESSION_A,
    ).view();
    expect(native.authorized).toBe(true);
    if (!native.authorized)
      throw new Error('native owner provenance should remain');
    expect(native.items.map((item) => item.display.name)).toEqual([
      'fictional-alpha.csv',
      'fictional-result.csv',
    ]);
    expect(native.figures).toBe(OPENUI487_FIXTURE.figures);
  });

  it('rejects a pinned artifact whose embedded result reference is for another message', () => {
    const [first, ...rest] = OPENUI487_FIXTURE.records;
    expect(
      createFixtureAdapter(
        {
          ...OPENUI487_FIXTURE,
          records: [
            {
              ...first,
              item: {
                ...first.item,
                reference: { ...first.item.reference!, message_id: 'other' },
              },
            },
            ...rest,
          ],
        },
        OPENUI487_SESSION_A,
      ).resolve(OPENUI487_FIXTURE.nativePins[0]),
    ).toEqual({ ok: false, reason: 'message_mismatch' });
  });
});

const fallbackCases: {
  name: string;
  layout: PilotLayoutInput | null;
  layoutEnabled?: boolean;
  reason: PilotLayoutRejection;
}[] = [
  { name: 'absent', layout: null, reason: 'layout_absent' },
  {
    name: 'pending',
    layout: { frame, final: false },
    reason: 'layout_pending_final_bit',
  },
  {
    name: 'malformed block list',
    layout: {
      frame: {
        ...frame,
        text: 'root = PilotLayout([TaskStatus(),, ArtifactResults(), Figures()])',
      },
      final: true,
    },
    reason: 'layout_invalid_syntax',
  },
  {
    name: 'unknown block',
    layout: {
      frame: {
        ...frame,
        text: 'root = PilotLayout([TaskStatus(), ArtifactResults(), Notes()])',
      },
      final: true,
    },
    reason: 'layout_unknown_block',
  },
  {
    name: 'oversize',
    layout: {
      frame: {
        ...frame,
        text: `${OPENUI487_RECORDED_LAYOUT}${'A'.repeat(PILOT_LAYOUT_MAX_BYTES)}`,
      },
      final: true,
    },
    reason: 'layout_too_large',
  },
  {
    name: 'unversioned',
    layout: {
      frame: { text: OPENUI487_RECORDED_LAYOUT } as unknown as PilotLayoutFrame,
      final: true,
    },
    reason: 'layout_unversioned',
  },
  {
    name: 'unsupported',
    layout: {
      frame: { ...frame, schema_version: 99 } as unknown as PilotLayoutFrame,
      final: true,
    },
    reason: 'layout_unsupported_version',
  },
  {
    name: 'disabled',
    layout: { frame, final: true },
    layoutEnabled: false,
    reason: 'layout_disabled',
  },
];

describe('Pilot fallback and reveal', () => {
  it.each(fallbackCases)(
    'retains authorized app data and omits denied data for $name',
    ({ layout, layoutEnabled, reason }) => {
      renderPilot({ layout, layoutEnabled });
      expect(pilot().getAttribute('data-authorized')).toBe('true');
      expect(pilot().getAttribute('data-layout-rejection')).toBe(reason);
      expect(pilot().getAttribute('data-layout-order')).toBe(FALLBACK_ORDER);
      expect(pilot().getAttribute('data-layout-active')).toBe('false');
      expect(screen.getByTestId('openui487-fallback')).toBeTruthy();
      expect(screen.getByText('fictional-alpha.csv')).toBeTruthy();
      expect(screen.getByText('fictional-result.csv')).toBeTruthy();
      expect(screen.getByText(/Synthetic task finished/)).toBeTruthy();
      expect(screen.getByText(/App-supplied weekday counts/)).toBeTruthy();
      expect(screen.queryByText('account-b-not-authorized.csv')).toBeNull();
      expect(screen.queryByText('forged-unpinned.csv')).toBeNull();

      const chart = screen.getByTestId('openui487-figure-weekday-deliveries');
      expect(chart.tagName).toBe('FIGURE');
      expect(chart.querySelector('svg[aria-hidden="true"]')).toBeTruthy();
      expect(figureRows(within(chart).getByRole('table'))).toEqual(
        expectedFigureRows,
      );
      if (reason === 'layout_unknown_block') {
        expect(screen.queryByText(/Notes\(\)/)).toBeNull();
      }
    },
  );

  it('is disabled by default unless the fixture explicitly opts in', () => {
    render(
      <Pilot
        fixture={OPENUI487_FIXTURE}
        session={OPENUI487_SESSION_A}
        layout={{ frame, final: true }}
      />,
    );
    expect(pilot().getAttribute('data-layout-rejection')).toBe(
      'layout_disabled',
    );
    expect(screen.getByTestId('openui487-fallback')).toBeTruthy();
    expect(screen.getByRole('figure')).toBeTruthy();
  });

  it.each([
    ['library version', { component_library_version: 99 }],
    ['schema version', { schema_version: 99 }],
    ['component identity', { component: 'unknown' }],
    ['message identity', { messageId: 'different-result' }],
    ['artifact pins', { items: OPENUI487_FIXTURE.envelope.items.slice(0, 1) }],
  ] as const)(
    'suppresses generated layout for an unsupported %s but keeps native results',
    (_name, patch) => {
      const unsupported: OpenUiFixture = {
        ...OPENUI487_FIXTURE,
        envelope: { ...OPENUI487_FIXTURE.envelope, ...patch },
      };
      renderPilot({
        fixture: unsupported,
        layout: { frame, final: true },
      });
      expect(pilot().getAttribute('data-layout-active')).toBe('false');
      expect(pilot().getAttribute('data-layout-rejection')).toBe(
        'layout_envelope_unsupported',
      );
      expect(screen.getByTestId('openui487-fallback')).toBeTruthy();
      expect(screen.getByText('fictional-alpha.csv')).toBeTruthy();
      expect(screen.getByText('fictional-result.csv')).toBeTruthy();
      expect(screen.getByText('2 of 2', { exact: false })).toBeTruthy();
      expect(screen.getByTestId('openui487-status')).toBeTruthy();
      const chart = screen.getByTestId('openui487-figure-weekday-deliveries');
      expect(chart.querySelector('svg[aria-hidden="true"]')).toBeTruthy();
      expect(figureRows(within(chart).getByRole('table'))).toEqual(
        expectedFigureRows,
      );
    },
  );

  it('reveals the canonical permutation only after the independent final bit', () => {
    const view = renderPilot({
      layout: {
        frame,
        chunks: OPENUI487_LAYOUT_CHUNKS.slice(0, 2),
        final: false,
      },
    });
    expect(pilot().getAttribute('data-layout-active')).toBe('false');
    expect(pilot().getAttribute('data-layout-order')).toBe(FALLBACK_ORDER);
    const figure = screen.getByRole('figure');
    const svg = figure.querySelector('svg');
    const table = within(figure).getByRole('table');
    expect(svg).toBeTruthy();

    view.rerender(
      <Pilot
        fixture={OPENUI487_FIXTURE}
        session={OPENUI487_SESSION_A}
        status="completed"
        layout={{ frame, chunks: OPENUI487_LAYOUT_CHUNKS, final: true }}
        layoutEnabled
      />,
    );
    expect(pilot().getAttribute('data-layout-active')).toBe('true');
    expect(pilot().getAttribute('data-layout-rejection')).toBe('none');
    expect(pilot().getAttribute('data-layout-order')).toBe(ACTIVE_ORDER);
    expect(screen.queryByTestId('openui487-fallback')).toBeNull();
    expect(screen.getByRole('figure')).toBe(figure);
    expect(figure.querySelector('svg')).toBe(svg);
    expect(within(figure).getByRole('table')).toBe(table);
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it('reconnects and reopens a serialized layout in a fresh mount without file reads', () => {
    const first = renderPilot({
      layout: {
        frame,
        chunks: OPENUI487_LAYOUT_CHUNKS.slice(0, 1),
        final: false,
      },
    });
    first.unmount();
    const saved = JSON.stringify({ frame, final: true });
    const reopened = renderPilot({
      layout: JSON.parse(saved) as PilotLayoutInput,
    });
    expect(pilot().getAttribute('data-layout-order')).toBe(ACTIVE_ORDER);
    expect(screen.getByText('fictional-alpha.csv')).toBeTruthy();
    reopened.unmount();
    renderPilot({
      session: OPENUI487_SESSION_B,
      layout: JSON.parse(saved) as PilotLayoutInput,
    });
    expect(pilot().getAttribute('data-authorized')).toBe('false');
    expect(screen.queryByText('fictional-alpha.csv')).toBeNull();
    expect(screen.queryByRole('table')).toBeNull();
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it('keeps task lifecycle and connection display independent from layout reveal', () => {
    const view = renderPilot({
      status: 'queued',
      connection: 'loading',
      layout: { frame, final: false },
    });
    for (const status of [
      'queued',
      'running',
      'completed',
      'failed',
    ] as const) {
      view.rerender(
        <Pilot
          fixture={OPENUI487_FIXTURE}
          session={OPENUI487_SESSION_A}
          status={status}
          connection={status === 'running' ? 'reconnecting' : 'loading'}
          layout={{ frame, final: false }}
          layoutEnabled
        />,
      );
      expect(
        screen.getByTestId('openui487-status').getAttribute('data-status'),
      ).toBe(status);
      expect(screen.getByTestId('openui487-status-label').textContent).toBe(
        OPENUI487_FIXTURE.task.statuses[status].label,
      );
      expect(
        screen
          .getByTestId('openui487-connection')
          .getAttribute('data-connection'),
      ).toBe(status === 'running' ? 'reconnecting' : 'loading');
      expect(pilot().getAttribute('data-layout-rejection')).toBe(
        'layout_pending_final_bit',
      );
    }
    view.rerender(
      <Pilot
        fixture={OPENUI487_FIXTURE}
        session={OPENUI487_SESSION_A}
        status="completed"
        connection="live"
        layout={{ frame, final: true }}
        layoutEnabled
      />,
    );
    expect(pilot().getAttribute('data-layout-active')).toBe('true');
  });

  it('does not claim a task outcome when the app provides an unsupported status', () => {
    renderPilot({ status: 'cancelled' as never });
    expect(screen.getByTestId('openui487-status-label').textContent).toBe(
      'Status unavailable',
    );
    expect(screen.getByText('Unknown')).toBeTruthy();
    const time = screen.getByText(OPENUI487_FIXTURE.task.snapshotAt);
    expect(time.getAttribute('datetime')).toBe(
      OPENUI487_FIXTURE.task.snapshotAt,
    );
  });

  it('renders a named accessible table and hides the decorative SVG from AT', () => {
    renderPilot();
    const figure = screen.getByTestId('openui487-figure-weekday-deliveries');
    expect(figure.tagName).toBe('FIGURE');
    const svg = figure.querySelector('svg');
    expect(svg?.getAttribute('aria-hidden')).toBe('true');
    expect(svg?.getAttribute('class')).toContain('max-w-full');
    expect(within(figure).queryByRole('img')).toBeNull();
    const table = within(figure).getByRole('table');
    expect(table.querySelector('caption')?.textContent).toContain(
      'Synthetic weekday deliveries values supplied',
    );
    expect(figureRows(table)).toEqual(expectedFigureRows);
    expect(
      Array.from(svg!.querySelectorAll('text'), (node) => node.textContent),
    ).toEqual(expectedFigureRows.map(([label]) => label));
    const heights = Array.from(svg!.querySelectorAll('rect'), (node) =>
      Number(node.getAttribute('height')),
    );
    expect(heights).toHaveLength(3);
    [3, 5, 2].forEach((value, index) => {
      expect(heights[index] / Math.max(...heights)).toBeCloseTo(value / 5);
    });
    expect(table.parentElement?.getAttribute('class')).toContain(
      'overflow-x-auto',
    );
  });
});

describe('explicit file reads and synthetic session changes', () => {
  it('performs no request on mount/reopen and one authenticated preview on click', async () => {
    const view = renderPilot();
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
    view.unmount();
    renderPilot();
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();

    vi.mocked(fetch).mockResolvedValue(
      new Response(OPENUI487_RESULT_CSV, { status: 200 }),
    );
    fireEvent.click(
      within(resultCard()).getByRole('button', { name: 'Preview' }),
    );
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledOnce());
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe(
      `/api${resourcePath(
        OPENUI487_FIXTURE.envelope.items[1].resource_id,
        OPENUI487_FIXTURE.envelope.items[1].version_id,
      )}/preview`,
    );
    expect(vi.mocked(fetch).mock.calls[0][1]).toMatchObject({
      headers: { Authorization: 'Bearer synthetic-owner-a' },
      cache: 'no-store',
      redirect: 'error',
    });
    await waitFor(() => expect(screen.getByText(/beta,2/)).toBeTruthy());
  });

  it('surfaces an account/version read failure only after the click', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('{}', { status: 404 }));
    renderPilot();
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
    fireEvent.click(
      within(resultCard()).getByRole('button', { name: 'Preview' }),
    );
    await screen.findByText(
      'Could not read this version. Retry using Preview or Download.',
    );
    expect(vi.mocked(fetch)).toHaveBeenCalledOnce();
  });

  it('removes stale cards and preview state on account and generation changes', async () => {
    const view = renderPilot();
    vi.mocked(fetch).mockResolvedValue(
      new Response(OPENUI487_RESULT_CSV, { status: 200 }),
    );
    fireEvent.click(
      within(resultCard()).getByRole('button', { name: 'Preview' }),
    );
    await waitFor(() => expect(screen.getByText(/beta,2/)).toBeTruthy());

    act(() => {
      auth.generation += 1;
      for (const listener of auth.listeners) listener();
    });
    // The auth subscription changes first; no parent rerender is needed.
    expect(screen.queryByText('fictional-result.csv')).toBeNull();
    expect(screen.queryByTestId('openui487-status')).toBeNull();
    expect(screen.queryByTestId('openui487-figures')).toBeNull();
    expect(screen.queryByText(/beta,2/)).toBeNull();
    expect(vi.mocked(fetch)).toHaveBeenCalledOnce();

    view.rerender(
      <Pilot
        fixture={OPENUI487_FIXTURE}
        session={OPENUI487_SESSION_B}
        status="completed"
        layout={{ frame, final: true }}
        layoutEnabled
      />,
    );
    expect(screen.queryByText('fictional-result.csv')).toBeNull();
    expect(vi.mocked(fetch)).toHaveBeenCalledOnce();

    view.rerender(
      <Pilot
        fixture={OPENUI487_FIXTURE}
        session={{ ...OPENUI487_SESSION_A, generation: 8 }}
        status="completed"
        layout={{ frame, final: true }}
        layoutEnabled
      />,
    );
    expect(screen.queryByText('fictional-result.csv')).toBeNull();
    expect(vi.mocked(fetch)).toHaveBeenCalledOnce();

    // A fresh mount captures the current auth generation and reauthorizes by
    // the explicit fixture session, not by the stale mount.
    view.unmount();
    renderPilot();
    expect(screen.getByTestId('openui487-status')).toBeTruthy();
    expect(screen.getByText('fictional-alpha.csv')).toBeTruthy();
    expect(screen.getByText('fictional-result.csv')).toBeTruthy();
    expect(screen.getByTestId('openui487-figures')).toBeTruthy();
    expect(vi.mocked(fetch)).toHaveBeenCalledOnce();
  });
});
