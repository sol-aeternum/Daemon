import { expect, it } from 'vitest';
import {
  getDaemonTaskId,
  getDaemonTaskStatus,
  normalizeDaemonMessage,
  type DaemonMessage,
} from '@/lib/chatMessages';
import { parseMessageHistoryPage } from '@/lib/historyPages';
const task = '11111111-1111-4111-8111-111111111111';
const other = '22222222-2222-4222-8222-222222222222';
const message = {
  id: task,
  conversation_id: task,
  user_id: task,
  role: 'assistant',
  content: 'saved prose',
  created_at: '2026-01-01T00:00:00Z',
  task_id: task,
};
it('retains original history identity without manufacturing task status or events', () => {
  const normalized = normalizeDaemonMessage(message)!;
  expect(getDaemonTaskId(normalized)).toBe(task);
  expect(getDaemonTaskStatus(normalized)).toBeNull();
  expect(normalized.parts).toEqual([{ type: 'text', text: 'saved prose' }]);
  expect(normalized.metadata).toEqual({});
});
it('does not infer identities from metadata or legacy prose', () => {
  expect(
    getDaemonTaskId(
      normalizeDaemonMessage({
        ...message,
        task_id: null,
        metadata: { task_id: task },
      }),
    ),
  ).toBeNull();
  expect(
    getDaemonTaskId(
      normalizeDaemonMessage({ ...message, task_id: 'not a UUID' }),
    ),
  ).toBeNull();
});
it('fails closed when live events disagree with historical or each other', () => {
  const live = (id: string) =>
    ({
      type: 'data-event',
      data: { type: 'task', task_id: id },
    }) as DaemonMessage['parts'][number];
  const saved = normalizeDaemonMessage(message)!;
  expect(getDaemonTaskId({ ...saved, parts: [live(task)] })).toBe(task);
  expect(getDaemonTaskId({ ...saved, parts: [live(other)] })).toBeNull();
  expect(
    getDaemonTaskId({
      ...saved,
      task_id: null,
      parts: [live(task), live(other)],
    }),
  ).toBeNull();
});
it('cursor parsing preserves identity and rejects malformed non-null task ids', () => {
  const page = {
    id: task,
    user_id: task,
    messages: [message],
    message_page: {
      has_more: false,
      next_cursor: null,
      start_cursor: 'boundary',
      end_cursor: 'boundary',
    },
  };
  expect(getDaemonTaskId(parseMessageHistoryPage(page, task).messages[0])).toBe(
    task,
  );
  expect(() =>
    parseMessageHistoryPage(
      { ...page, messages: [{ ...message, task_id: false }] },
      task,
    ),
  ).toThrow();
});
