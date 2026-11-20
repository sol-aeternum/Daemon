/**
 * Deterministic #487 synthetic fixture. Test-only; no production module
 * imports it. One owner A task/conversation, one denied owner B, two generated
 * CSV outputs pinned by version, and one app-supplied weekday chart.
 */

import type { PinnedEnvelope, ResourceItem } from '@/lib/resources';
import {
  serializePilotLayout,
  utf8ByteLength,
  type PilotBlock,
} from './layout';

export const OPENUI487_ALPHA_CSV = 'label,value\nalpha,1\n' as const;
export const OPENUI487_RESULT_CSV = 'label,value\nbeta,2\n' as const;

const ACCOUNT_A = 'a0000000-0000-4000-8000-000000000001';
const ACCOUNT_B = 'b0000000-0000-4000-8000-000000000001';
const TASK_ID = 'c0000000-0000-4000-8000-000000000001';
const CONVERSATION_ID = 'c0000000-0000-4000-8000-000000000002';
const RESULT_MESSAGE_ID = 'c0000000-0000-4000-8000-000000000004';
const GENERATION = 7;
const TIMESTAMP = '2026-11-20T12:00:00Z';

export type SyntheticPilotStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed';

export type SyntheticStatusSample = {
  status: SyntheticPilotStatus | 'unavailable';
  label: string;
  detail: string;
  terminal: boolean | null;
};

const STATUSES: Record<SyntheticPilotStatus, SyntheticStatusSample> = {
  queued: {
    status: 'queued',
    label: 'Queued',
    detail: 'Synthetic task accepted and waiting for a local worker.',
    terminal: false,
  },
  running: {
    status: 'running',
    label: 'Running',
    detail: 'Synthetic task is rendering app-owned results.',
    terminal: false,
  },
  completed: {
    status: 'completed',
    label: 'Completed',
    detail: 'Synthetic task finished with the pinned files below.',
    terminal: true,
  },
  failed: {
    status: 'failed',
    label: 'Failed',
    detail:
      'Synthetic task stopped. Authorized pinned files and figures remain readable.',
    terminal: true,
  },
};

export type SyntheticFigure = {
  id: string;
  title: string;
  caption: string;
  values: { label: string; value: number; unit: string }[];
};

const FIGURES: SyntheticFigure[] = [
  {
    id: 'weekday-deliveries',
    title: 'Synthetic weekday deliveries',
    caption:
      'App-supplied weekday counts and units. The recorded layout text chooses only the order of the three display blocks.',
    values: [
      { label: 'Monday', value: 3, unit: 'deliveries' },
      { label: 'Tuesday', value: 5, unit: 'deliveries' },
      { label: 'Wednesday', value: 2, unit: 'deliveries' },
    ],
  },
];

export type PilotRecord = {
  item: ResourceItem;
  ownerId: string;
  taskId: string;
  conversationId: string;
  generation: number;
  pinned: boolean;
};

const scope = {
  ownerId: ACCOUNT_A,
  taskId: TASK_ID,
  conversationId: CONVERSATION_ID,
  generation: GENERATION,
};

function generatedRecord(
  name: string,
  csv: string,
  resourceId: string,
  versionId: string,
  ordinal: number,
): PilotRecord {
  return {
    ...scope,
    pinned: true,
    item: {
      resource_id: resourceId,
      version_id: versionId,
      representation: 'generated_file',
      kind: 'file',
      mime: 'text/csv',
      measured_bytes: utf8ByteLength(csv),
      created_at: TIMESTAMP,
      availability: 'available',
      display: { name, title: null },
      reference: {
        reference_id: `d0000000-0000-4000-8000-00000000000${ordinal + 1}`,
        role: 'output',
        ordinal,
        task_id: TASK_ID,
        conversation_id: CONVERSATION_ID,
        message_id: RESULT_MESSAGE_ID,
        source_deleted: false,
      },
    },
  };
}

const alpha = generatedRecord(
  'fictional-alpha.csv',
  OPENUI487_ALPHA_CSV,
  '4870e000-0000-4000-8000-000000000001',
  '4870f000-0000-4000-8000-000000000001',
  0,
);
const beta = generatedRecord(
  'fictional-result.csv',
  OPENUI487_RESULT_CSV,
  '4870e000-0000-4000-8000-000000000002',
  '4870f000-0000-4000-8000-000000000002',
  1,
);
const denied: PilotRecord = {
  ...scope,
  ownerId: ACCOUNT_B,
  pinned: false,
  item: {
    ...beta.item,
    resource_id: 'e0000000-0000-4000-8000-000000000009',
    version_id: 'f0000000-0000-4000-8000-000000000009',
    display: { name: 'account-b-not-authorized.csv', title: null },
  },
};
const forged: PilotRecord = {
  ...alpha,
  pinned: false,
  item: {
    ...alpha.item,
    resource_id: 'e0000000-0000-4000-8000-000000000777',
    version_id: 'f0000000-0000-4000-8000-000000000777',
    display: { name: 'forged-unpinned.csv', title: null },
  },
};

const nativePins = [alpha, beta].map(({ item }) => ({
  resource_id: item.resource_id,
  version_id: item.version_id,
}));

export const OPENUI487_FIXTURE = {
  accountA: ACCOUNT_A,
  accountB: ACCOUNT_B,
  generation: GENERATION,
  task: {
    taskId: TASK_ID,
    conversationId: CONVERSATION_ID,
    messageId: RESULT_MESSAGE_ID,
    snapshotAt: TIMESTAMP,
    defaultStatus: 'completed' as const,
    statuses: STATUSES,
  },
  envelope: {
    schema_version: 1,
    component_library_version: 1,
    snapshot_at: TIMESTAMP,
    component: 'task-results',
    taskId: TASK_ID,
    conversationId: CONVERSATION_ID,
    messageId: RESULT_MESSAGE_ID,
    items: nativePins.map((pin) => ({ ...pin })),
  } satisfies PinnedEnvelope & {
    taskId: string;
    conversationId: string;
    messageId: string;
  },
  nativePins,
  records: [alpha, beta, denied, forged],
  figures: FIGURES,
};

export type OpenUiFixture = typeof OPENUI487_FIXTURE;

export type PilotSession = {
  accountId: string;
  taskId: string;
  conversationId: string;
  messageId: string;
  generation: number;
};

export const OPENUI487_SESSION_A: PilotSession = {
  accountId: ACCOUNT_A,
  taskId: TASK_ID,
  conversationId: CONVERSATION_ID,
  messageId: RESULT_MESSAGE_ID,
  generation: GENERATION,
};

export const OPENUI487_SESSION_B: PilotSession = {
  ...OPENUI487_SESSION_A,
  accountId: ACCOUNT_B,
};

export type PinnedReference = {
  resource_id?: string;
  version_id?: string;
};

export type PilotDenial =
  | 'account_mismatch'
  | 'generation_mismatch'
  | 'task_mismatch'
  | 'conversation_mismatch'
  | 'message_mismatch'
  | 'not_pinned'
  | 'forged_record'
  | 'removed_record';

export type PilotPinnedResolution =
  | { ok: true; item: ResourceItem }
  | { ok: false; reason: PilotDenial };

export type PilotAuthorizedView =
  | {
      authorized: true;
      task: OpenUiFixture['task'];
      items: ResourceItem[];
      figures: SyntheticFigure[];
    }
  | {
      authorized: false;
      reason: PilotDenial;
      task: null;
      items: [];
      figures: [];
    };

const key = (reference: PinnedReference) =>
  `${reference.resource_id ?? ''}:${reference.version_id ?? ''}`;
const recordKey = (record: PilotRecord) => key(record.item);

export function layoutEnvelopeSupported(fixture: OpenUiFixture): boolean {
  const { envelope, task, nativePins } = fixture;
  if (
    envelope?.schema_version !== 1 ||
    envelope.component_library_version !== 1 ||
    envelope.component !== 'task-results' ||
    envelope.taskId !== task.taskId ||
    envelope.conversationId !== task.conversationId ||
    envelope.messageId !== task.messageId ||
    envelope.snapshot_at !== task.snapshotAt ||
    !Array.isArray(envelope.items) ||
    envelope.items.length !== nativePins.length
  ) {
    return false;
  }
  const pins = new Set(envelope.items.map((pin) => pin && key(pin)));
  return (
    pins.size === nativePins.length &&
    nativePins.every((pin) => pins.has(key(pin)))
  );
}

export function createFixtureAdapter(
  fixture: OpenUiFixture,
  session: PilotSession,
) {
  const pins = new Set(fixture.nativePins.map(key));

  const sessionDenial = (): PilotDenial | null => {
    if (session.accountId !== fixture.accountA) return 'account_mismatch';
    if (session.taskId !== fixture.task.taskId) return 'task_mismatch';
    if (session.conversationId !== fixture.task.conversationId) {
      return 'conversation_mismatch';
    }
    if (session.messageId !== fixture.task.messageId) return 'message_mismatch';
    if (session.generation !== fixture.generation) return 'generation_mismatch';
    return null;
  };

  const recordDenial = (record: PilotRecord): PilotDenial | null => {
    if (record.ownerId !== fixture.accountA) return 'account_mismatch';
    if (record.taskId !== fixture.task.taskId) return 'task_mismatch';
    if (record.conversationId !== fixture.task.conversationId) {
      return 'conversation_mismatch';
    }
    if (record.generation !== fixture.generation) return 'generation_mismatch';
    const reference = record.item.reference;
    if (reference?.task_id !== session.taskId) return 'task_mismatch';
    if (reference.conversation_id !== session.conversationId) {
      return 'conversation_mismatch';
    }
    if (reference.message_id !== session.messageId) return 'message_mismatch';
    if (reference.role !== 'output') return 'not_pinned';
    return null;
  };

  const resolve = (reference: PinnedReference): PilotPinnedResolution => {
    const deniedSession = sessionDenial();
    if (deniedSession) return { ok: false, reason: deniedSession };

    const referenceId = key(reference);
    const matches = fixture.records.filter(
      (record) => recordKey(record) === referenceId,
    );
    if (matches.length === 0) {
      return {
        ok: false,
        reason: pins.has(referenceId) ? 'removed_record' : 'not_pinned',
      };
    }
    if (matches.length > 1) return { ok: false, reason: 'forged_record' };

    const record = matches[0];
    const deniedRecord = recordDenial(record);
    if (deniedRecord) return { ok: false, reason: deniedRecord };
    const approved = record.pinned && pins.has(referenceId);
    if (!approved) return { ok: false, reason: 'not_pinned' };
    return { ok: true, item: record.item };
  };

  const view = (): PilotAuthorizedView => {
    const deniedSession = sessionDenial();
    if (deniedSession) {
      return {
        authorized: false,
        reason: deniedSession,
        task: null,
        items: [],
        figures: [],
      };
    }
    const items = fixture.nativePins.flatMap((pin) => {
      const resolution = resolve(pin);
      return resolution.ok ? [resolution.item] : [];
    });
    return {
      authorized: true,
      task: fixture.task,
      items,
      figures: fixture.figures,
    };
  };

  return { view, resolve };
}

export type PilotFixtureAdapter = ReturnType<typeof createFixtureAdapter>;

export const OPENUI487_CANONICAL_ORDER: readonly PilotBlock[] = [
  'Figures',
  'TaskStatus',
  'ArtifactResults',
];
export const OPENUI487_RECORDED_LAYOUT = serializePilotLayout(
  OPENUI487_CANONICAL_ORDER,
);
export const OPENUI487_LAYOUT_CHUNKS = [
  'root = ',
  'PilotLayout([',
  'Figures(), TaskStatus(), ',
  'ArtifactResults()])',
] as const;
