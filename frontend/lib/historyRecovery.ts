/** Negative recovery outcomes only; none is authority to send or replace a key. */
export type HistoryRecoveryCode =
  | 'restore_recovery_required'
  | 'history_unavailable'
  | 'inference_dispatch_unresolved';

export function historyRecoveryCode(
  value: unknown,
): HistoryRecoveryCode | null {
  if (!value || typeof value !== 'object') return null;
  const detail = (value as { detail?: unknown }).detail;
  if (!detail || typeof detail !== 'object') return null;
  const body = detail as { code?: unknown; retry_safe?: unknown };
  return body.retry_safe === false &&
    (body.code === 'restore_recovery_required' ||
      body.code === 'history_unavailable')
    ? body.code
    : null;
}

export function historyRecoveryMessage(code: HistoryRecoveryCode): string {
  if (code === 'history_unavailable')
    return 'Task history is unavailable. Your original request is retained and will not be resent automatically.';
  if (code === 'inference_dispatch_unresolved')
    return 'This task needs attention: work may already have been sent. Your original request is retained; check its result before starting new work.';
  return 'This task requires restore recovery. Your original request is retained and will not be resent automatically.';
}
