// Node-side RPC brackets, not browser CPU residency or precise frame boundaries.
import { performance } from 'node:perf_hooks';
import type { TestInfo } from '@playwright/test';

export async function recordScrollWindow(
  info: TestInfo,
  sample: () => Promise<number>,
) {
  const start_epoch_ms = Date.now();
  const start_monotonic_ms = performance.now();
  const fps = await sample();
  const end_monotonic_ms = performance.now();
  const end_epoch_ms = Date.now();
  await info.attach('issue490-window', {
    body: JSON.stringify({
      start_epoch_ms,
      end_epoch_ms,
      start_monotonic_ms,
      end_monotonic_ms,
      fps,
    }),
    contentType: 'application/json',
  });
  return fps;
}
