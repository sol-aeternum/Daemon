'use client';

import { useState } from 'react';
import MarkdownMessage from '@/components/MarkdownMessage';
import { ResourceCard } from '@/components/ResourceCards';
import { useAuthGeneration } from '@/hooks/useAuthGeneration';
import type { ResourceItem } from '@/lib/resources';
import {
  createFixtureAdapter,
  layoutEnvelopeSupported,
  type OpenUiFixture,
  type PilotSession,
  type SyntheticFigure,
  type SyntheticPilotStatus,
  type SyntheticStatusSample,
} from './fixture';
import {
  PILOT_FALLBACK_ORDER,
  PILOT_LAYOUT_REASON_TEXT,
  resolvePilotLayout,
  type PilotBlock,
  type PilotLayoutInput,
  type PilotLayoutRejection,
  type PilotLayoutResolution,
} from './layout';

export type PilotConnection = 'idle' | 'loading' | 'reconnecting' | 'live';

export type PilotProps = {
  fixture: OpenUiFixture;
  session: PilotSession;
  status?: SyntheticPilotStatus;
  connection?: PilotConnection;
  layout?: PilotLayoutInput | null;
  layoutEnabled?: boolean;
};

const CONNECTION_TEXT: Record<PilotConnection, string> = {
  idle: 'Synthetic result stream idle.',
  loading: 'Loading synthetic results…',
  reconnecting: 'Reconnecting to synthetic results…',
  live: 'Synthetic result stream live.',
};

/**
 * Test-only pilot. Layout text selects only block order through the closed
 * parser. Task, files and figures are app-owned; mounting or reopening this
 * view performs no file request.
 */
export function Pilot({
  fixture,
  session,
  status = fixture.task.defaultStatus,
  connection = 'live',
  layout = null,
  layoutEnabled = false,
}: PilotProps) {
  const authGeneration = useAuthGeneration();
  const [mountAuthGeneration] = useState(authGeneration);
  const authChanged = authGeneration !== mountAuthGeneration;
  const view = createFixtureAdapter(fixture, session).view();
  const resolution: PilotLayoutResolution = layoutEnvelopeSupported(fixture)
    ? resolvePilotLayout(layout, layoutEnabled)
    : {
        active: false,
        order: PILOT_FALLBACK_ORDER,
        rejection: 'layout_envelope_unsupported',
      };

  if (authChanged || !view.authorized) {
    return (
      <section
        aria-label="Synthetic OpenUI pilot"
        data-testid="openui487-pilot"
        data-authorized="false"
        data-auth-changed={authChanged ? 'true' : 'false'}
        className="mx-auto grid min-w-0 max-w-3xl gap-3"
      >
        <p
          role="alert"
          className="rounded-xl border border-[var(--color-border-primary)] p-3 text-sm"
        >
          No authorized synthetic task, files or values are available for this
          account and scope.
        </p>
      </section>
    );
  }

  const sample: SyntheticStatusSample = Object.hasOwn(
    fixture.task.statuses,
    status,
  )
    ? fixture.task.statuses[status]
    : {
        status: 'unavailable',
        label: 'Status unavailable',
        detail: 'This fixture has no supported status for the task.',
        terminal: null,
      };
  const block = (id: PilotBlock) => {
    switch (id) {
      case 'TaskStatus':
        return (
          <TaskStatusBlock
            task={view.task}
            sample={sample}
            capturedAt={view.task.snapshotAt}
          />
        );
      case 'ArtifactResults':
        return (
          <ArtifactResultsBlock
            items={view.items}
            session={session}
            pinnedCount={fixture.nativePins.length}
          />
        );
      case 'Figures':
        return (
          <FiguresBlock figures={view.figures} showChart={resolution.active} />
        );
    }
  };

  return (
    <section
      aria-label="Synthetic OpenUI pilot"
      data-testid="openui487-pilot"
      data-authorized="true"
      data-layout-order={resolution.order.join('>')}
      data-layout-rejection={resolution.rejection ?? 'none'}
      data-layout-active={resolution.active ? 'true' : 'false'}
      className="mx-auto grid min-w-0 max-w-3xl gap-3"
    >
      <p
        role="status"
        aria-live="polite"
        data-testid="openui487-connection"
        data-connection={connection}
        className="rounded-xl border border-[var(--color-border-primary)] p-3 text-sm text-[var(--color-text-secondary)]"
      >
        {CONNECTION_TEXT[connection]}
      </p>
      {!resolution.active && resolution.rejection ? (
        <FallbackNotice rejection={resolution.rejection} />
      ) : null}
      {resolution.order.map((id) => (
        <div key={id} data-pilot-block={id} className="min-w-0">
          {block(id)}
        </div>
      ))}
    </section>
  );
}

function FallbackNotice({ rejection }: { rejection: PilotLayoutRejection }) {
  return (
    <p
      role="status"
      data-testid="openui487-fallback"
      data-layout-rejection={rejection}
      className="rounded-xl border border-[var(--color-border-primary)] p-3 text-sm text-[var(--color-text-secondary)]"
    >
      {PILOT_LAYOUT_REASON_TEXT[rejection]}
    </p>
  );
}

function TaskStatusBlock({
  task,
  sample,
  capturedAt,
}: {
  task: OpenUiFixture['task'];
  sample: SyntheticStatusSample;
  capturedAt: string;
}) {
  return (
    <article
      aria-label="Synthetic task status"
      data-testid="openui487-status"
      data-status={sample.status}
      className="min-w-0 rounded-xl border border-[var(--color-border-primary)] p-3"
    >
      <h2 className="font-semibold">Task status</h2>
      <p
        role="status"
        aria-live="polite"
        data-testid="openui487-status-label"
        className="font-medium"
      >
        {sample.label}
      </p>
      <div className="min-w-0 break-words text-sm">
        <MarkdownMessage content={`**${sample.label}.** ${sample.detail}`} />
      </div>
      <dl className="mt-2 grid gap-1 text-xs text-[var(--color-text-secondary)] sm:grid-cols-2">
        <div className="min-w-0">
          <dt className="font-medium">Task</dt>
          <dd className="break-all">{task.taskId}</dd>
        </div>
        <div className="min-w-0">
          <dt className="font-medium">Conversation</dt>
          <dd className="break-all">{task.conversationId}</dd>
        </div>
        <div>
          <dt className="font-medium">Fixture snapshot</dt>
          <dd>
            <time dateTime={capturedAt}>{capturedAt}</time>
          </dd>
        </div>
        <div>
          <dt className="font-medium">Lifecycle</dt>
          <dd>
            {sample.terminal === null
              ? 'Unknown'
              : sample.terminal
                ? 'Terminal'
                : 'Non-terminal'}
          </dd>
        </div>
      </dl>
      <p className="mt-2 text-xs text-[var(--color-text-secondary)]">
        The app owns this status. Layout text cannot change it or reveal a later
        state early.
      </p>
    </article>
  );
}

function ArtifactResultsBlock({
  items,
  session,
  pinnedCount,
}: {
  items: ResourceItem[];
  session: PilotSession;
  pinnedCount: number;
}) {
  return (
    <section
      aria-label="Authorized pinned files"
      data-testid="openui487-artifacts"
      className="min-w-0 rounded-xl border border-[var(--color-border-primary)] p-3"
    >
      <h2 className="font-semibold">Files</h2>
      <p className="mb-2 text-xs text-[var(--color-text-secondary)]">
        {items.length} of {pinnedCount} app-pinned references are authorized.
        Preview and download are explicit reads.
      </p>
      {items.length ? (
        <div className="grid min-w-0 gap-2">
          {items.map((item) => (
            <ResourceCard
              key={`${session.accountId}:${session.generation}:${item.version_id}`}
              item={item}
            />
          ))}
        </div>
      ) : (
        <p>No authorized pinned file references.</p>
      )}
    </section>
  );
}

function FiguresBlock({
  figures,
  showChart,
}: {
  figures: SyntheticFigure[];
  showChart: boolean;
}) {
  return (
    <section
      aria-label="App-supplied figures"
      data-testid="openui487-figures"
      className="min-w-0 rounded-xl border border-[var(--color-border-primary)] p-3"
    >
      <h2 className="font-semibold">Figures</h2>
      {figures.length ? (
        figures.map((figure) => (
          <Figure key={figure.id} figure={figure} showChart={showChart} />
        ))
      ) : (
        <p>No authorized app-supplied figures.</p>
      )}
    </section>
  );
}

function Figure({
  figure,
  showChart,
}: {
  figure: SyntheticFigure;
  showChart: boolean;
}) {
  const max = Math.max(1, ...figure.values.map((value) => value.value));
  const width = 320;
  const height = 120;
  const padding = 12;
  const plotHeight = 82;
  const gap = 8;
  const barWidth = Math.max(
    8,
    (width - padding * 2 - gap * Math.max(0, figure.values.length - 1)) /
      Math.max(1, figure.values.length),
  );

  const prose = (
    <>
      <h3 className="font-medium">{figure.title}</h3>
      <div className="min-w-0 break-words text-sm text-[var(--color-text-secondary)]">
        <MarkdownMessage content={figure.caption} />
      </div>
    </>
  );
  const table = (
    <div className="mt-2 w-full overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm">
        <caption className="pb-2 text-left text-xs text-[var(--color-text-secondary)]">
          {figure.title} values supplied by the app fixture.
        </caption>
        <thead>
          <tr>
            <th scope="col" className="border-b py-1 pr-2">
              Label
            </th>
            <th scope="col" className="border-b py-1 pr-2">
              Value
            </th>
            <th scope="col" className="border-b py-1">
              Unit
            </th>
          </tr>
        </thead>
        <tbody>
          {figure.values.map((value) => (
            <tr key={value.label}>
              <th scope="row" className="py-1 pr-2 font-normal">
                {value.label}
              </th>
              <td className="py-1 pr-2">{value.value}</td>
              <td className="py-1">{value.unit}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  if (!showChart) {
    return (
      <section
        aria-label={`${figure.title} table`}
        data-testid={`openui487-figure-${figure.id}`}
        className="mt-2 min-w-0"
      >
        {prose}
        {table}
      </section>
    );
  }

  return (
    <figure
      aria-label={figure.title}
      data-testid={`openui487-figure-${figure.id}`}
      className="mt-2 min-w-0"
    >
      <figcaption className="min-w-0">{prose}</figcaption>
      <svg
        aria-hidden="true"
        focusable="false"
        viewBox={`0 0 ${width} ${height}`}
        className="mt-2 h-auto w-full max-w-full"
      >
        {figure.values.map((value, index) => {
          const barHeight = Math.max(1, (value.value / max) * plotHeight);
          const x = padding + index * (barWidth + gap);
          return (
            <g key={value.label}>
              <rect
                x={x}
                y={padding + plotHeight - barHeight}
                width={barWidth}
                height={barHeight}
                rx={2}
                className="fill-[var(--color-accent-primary)]"
              />
              <text
                x={x + barWidth / 2}
                y={padding + plotHeight + 12}
                textAnchor="middle"
                fontSize={9}
                className="fill-[var(--color-text-secondary)]"
              >
                {value.label}
              </text>
            </g>
          );
        })}
      </svg>
      {table}
    </figure>
  );
}
