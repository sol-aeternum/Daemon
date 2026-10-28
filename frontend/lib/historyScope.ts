/**
 * Request lifetimes for the single history owner (#230). No auth storage or
 * React state lives here. Call commitSelection only for committed navigation,
 * not for speculative renders; account generations come from lib/auth.
 */
export interface HistoryLease {
  readonly generation: number;
  readonly selectionEpoch: number | null;
  readonly signal: AbortSignal;
  isCurrent(): boolean;
  assertCurrent(): void;
  /** Caller releases after its last guarded publication, not after fetch. */
  release(): void;
}

function cancelled(): DOMException {
  return new DOMException('History request is no longer current', 'AbortError');
}

export class HistoryScope {
  private generation: number;
  private selectionId: string | null;
  private selectionEpoch = 0;
  private closed = false;
  private leases = new Set<{
    controller: AbortController;
    selected: boolean;
  }>();
  private families = new Map<string, AbortController>();

  constructor(
    private readonly getGeneration: () => number,
    selectionId: string | null,
  ) {
    this.generation = getGeneration();
    this.selectionId = selectionId;
  }

  /** Same-generation readiness notifications deliberately do nothing. */
  syncGeneration(): boolean {
    const generation = this.getGeneration();
    if (generation === this.generation) return false;
    this.generation = generation;
    this.selectionEpoch += 1;
    for (const { controller } of this.leases) controller.abort(cancelled());
    this.families.clear();
    return true;
  }

  commitSelection(id: string | null): void {
    this.syncGeneration();
    if (this.selectionId === id) return;
    this.selectionId = id;
    this.selectionEpoch += 1;
    for (const { controller, selected } of this.leases) {
      if (selected) controller.abort(cancelled());
    }
  }

  /** Invalidate a changed search immediately, before its debounce starts. */
  invalidateFamily(family: string): void {
    this.families.get(family)?.abort(cancelled());
    this.families.delete(family);
  }

  /** A family is latest-request-wins; separate jobs may omit it. */
  capture({
    selected = true,
    family,
    signal,
  }: {
    selected?: boolean;
    family?: string;
    signal?: AbortSignal;
  } = {}): HistoryLease {
    this.syncGeneration();
    const generation = this.generation;
    const selectionEpoch = selected ? this.selectionEpoch : null;
    const controller = new AbortController();
    const entry = { controller, selected };
    if (family !== undefined) {
      this.families.get(family)?.abort(cancelled());
      this.families.set(family, controller);
    }
    let released = false;
    const abort = () => controller.abort(cancelled());
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted || this.closed) abort();
    this.leases.add(entry);
    const isCurrent = () => {
      this.syncGeneration();
      return (
        !released &&
        !this.closed &&
        !controller.signal.aborted &&
        generation === this.generation &&
        (selectionEpoch === null || selectionEpoch === this.selectionEpoch)
      );
    };
    return {
      generation,
      selectionEpoch,
      signal: controller.signal,
      isCurrent,
      assertCurrent: () => {
        if (!isCurrent()) throw cancelled();
      },
      release: () => {
        if (released) return;
        released = true;
        abort();
        signal?.removeEventListener('abort', abort);
        this.leases.delete(entry);
        if (family !== undefined && this.families.get(family) === controller) {
          this.families.delete(family);
        }
      },
    };
  }

  dispose(): void {
    this.closed = true;
    for (const { controller } of this.leases) controller.abort(cancelled());
    this.leases.clear();
    this.families.clear();
  }
}

/**
 * GET-only history transport. Caller retains the lease through validation and
 * publication, and must check it again in deferred state updaters/consumers.
 * Timeout covers body parsing too; intentional cancellation never tries another
 * URL. It grants no authority to replay mutations.
 */
export async function readScopedHistory(
  lease: HistoryLease,
  candidates: readonly string[],
  {
    getAuthHeaders,
    fetch: fetcher = fetch,
    timeoutMs = 12000,
  }: {
    getAuthHeaders: () => Promise<Record<string, string>>;
    fetch?: typeof fetch;
    timeoutMs?: number;
  },
): Promise<{ status: number; data: unknown }> {
  lease.assertCurrent();
  const headers = await getAuthHeaders();
  lease.assertCurrent();
  for (let index = 0; index < candidates.length; index += 1) {
    lease.assertCurrent();
    const controller = new AbortController();
    const abort = () => controller.abort(lease.signal.reason);
    lease.signal.addEventListener('abort', abort, { once: true });
    const timeout = setTimeout(
      () =>
        controller.abort(
          new DOMException('History read timed out', 'TimeoutError'),
        ),
      timeoutMs,
    );
    try {
      // Covers cancellation racing listener installation as well.
      lease.assertCurrent();
      const response = await fetcher(candidates[index], {
        method: 'GET',
        headers,
        signal: controller.signal,
      });
      lease.assertCurrent();
      if (controller.signal.aborted) throw controller.signal.reason;
      if (response.status === 404 && index < candidates.length - 1) continue;
      const data: unknown = response.ok ? await response.json() : null;
      lease.assertCurrent();
      if (controller.signal.aborted) throw controller.signal.reason;
      return { status: response.status, data };
    } catch (error) {
      lease.assertCurrent();
      if (index === candidates.length - 1) throw error;
    } finally {
      clearTimeout(timeout);
      lease.signal.removeEventListener('abort', abort);
    }
  }
  throw new Error('No history URL available');
}
