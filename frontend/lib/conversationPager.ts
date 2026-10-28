import { type HistoryLease, HistoryScope } from './historyScope';
import type { HistoryPage } from './historyPages';

export interface ConversationPage<T> {
  rows: T[];
  total: number;
  page: HistoryPage;
}

export interface ConversationPaging {
  hasMore: boolean;
  loading: boolean;
  refreshing: boolean;
  updatesPending: boolean;
  error: boolean;
  loadMore: () => void;
  retry: () => void;
}

interface Snapshot<T> {
  generation: number;
  rows: T[];
  total: number;
  loaded: boolean;
  hasMore: boolean;
  loading: boolean;
  refreshing: boolean;
  updatesPending: boolean;
  error: boolean;
}

interface Sweep<T> {
  startedRevision: number;
  boundary: T | null;
  nextBoundary: T | null;
  traversalRevision: number;
  previous: Map<string, number>;
  seen: Set<string>;
  cursor: string | null;
  page: HistoryPage | null;
  phase: 'scan' | 'revalidate';
  missing: string[];
}

/**
 * Single provider-owned live list, never a transcript cache. Reads are serial;
 * refresh yields after five requests and prioritizes explicit Load more between
 * slices. Mutable ordering is reconciled eventually, not presented as a snapshot.
 */
export class ConversationPager<T extends { id: string }> {
  private scope: HistoryScope;
  private rows = new Map<string, T>();
  private revisions = new Map<string, number>();
  private revision = 0;
  private cursor: string | null = null;
  private boundary: T | null = null;
  private traversalRevision = 0;
  private sweep: Sweep<T> | null = null;
  private wantMore = false;
  private wantRefresh = false;
  private running = false;
  private disposed = false;
  private listeners = new Set<() => void>();
  private snapshot: Snapshot<T>;

  constructor(
    private readonly options: {
      generation: () => number;
      compare: (a: T, b: T) => number;
      readPage: (
        cursor: string | null,
        lease: HistoryLease,
      ) => Promise<ConversationPage<T>>;
      readOne: (
        id: string,
        lease: HistoryLease,
      ) => Promise<{ status: number; row?: T }>;
      yield?: () => Promise<void>;
    },
  ) {
    this.scope = new HistoryScope(options.generation, null);
    this.snapshot = this.empty();
  }

  private empty(): Snapshot<T> {
    return {
      generation: this.options.generation(),
      rows: [],
      total: 0,
      loaded: false,
      hasMore: false,
      loading: false,
      refreshing: false,
      updatesPending: false,
      error: false,
    };
  }

  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  private publish(patch: Partial<Snapshot<T>> = {}): void {
    if (this.disposed) return;
    this.snapshot = {
      ...this.snapshot,
      ...patch,
      rows: [...this.rows.values()].sort(this.options.compare),
    };
    for (const listener of this.listeners) listener();
  }

  /** Called by the auth subscriber; same-generation readiness retains history. */
  syncGeneration(): void {
    this.scope.syncGeneration();
    if (this.snapshot.generation === this.options.generation()) return;
    this.rows.clear();
    this.revisions.clear();
    this.cursor = null;
    this.boundary = null;
    this.traversalRevision += 1;
    this.sweep = null;
    this.wantMore = false;
    this.wantRefresh = false;
    this.snapshot = this.empty();
    this.publish();
  }

  /** Synchronous optimistic mutations share this owner; no parallel list cache. */
  update(updater: (rows: T[]) => T[]): void {
    this.syncGeneration();
    const old = this.rows;
    const next = new Map(
      updater([...old.values()]).map((row) => [row.id, row]),
    );
    for (const [id, row] of next) {
      if (old.get(id) !== row) this.revisions.set(id, ++this.revision);
    }
    for (const id of old.keys()) {
      if (!next.has(id)) this.revisions.set(id, ++this.revision);
    }
    this.rows = next;
    this.publish();
  }

  refresh = (): void => {
    this.syncGeneration();
    if (!this.sweep) this.wantRefresh = true;
    void this.pump();
  };

  loadMore = (): void => {
    this.syncGeneration();
    if (this.snapshot.loading || !this.snapshot.hasMore) return;
    this.wantMore = true;
    void this.pump();
  };

  retry = (): void => {
    // A failed partial sweep is restarted from head; content/cursor survive.
    if (this.snapshot.hasMore && !this.snapshot.updatesPending) this.loadMore();
    else this.refresh();
  };

  private merge(
    rows: T[],
    preserve?: Map<string, number>,
    startedRevision = Infinity,
  ): void {
    for (const row of rows) {
      if (
        preserve?.has(row.id) &&
        preserve.get(row.id) !== this.revisions.get(row.id)
      )
        continue;
      if (
        preserve &&
        !preserve.has(row.id) &&
        (this.revisions.get(row.id) ?? 0) > startedRevision
      )
        continue;
      this.rows.set(row.id, row);
      this.revisions.set(row.id, ++this.revision);
    }
  }

  private startSweep(): void {
    this.wantRefresh = false;
    this.sweep = {
      startedRevision: this.revision,
      boundary: this.boundary,
      nextBoundary: this.boundary,
      traversalRevision: this.traversalRevision,
      previous: new Map(
        [...this.rows.keys()].map((id) => [id, this.revisions.get(id) ?? 0]),
      ),
      seen: new Set(),
      cursor: null,
      page: null,
      phase: 'scan',
      missing: [],
    };
    this.publish({
      refreshing: true,
      updatesPending: this.snapshot.loaded,
      error: false,
    });
  }

  private async step(lease: HistoryLease): Promise<void> {
    if (this.wantMore) {
      this.wantMore = false;
      this.publish({ loading: true, error: false });
      const startedRevision = this.revision;
      const previous = new Map(this.revisions);
      const page = await this.options.readPage(this.cursor, lease);
      lease.assertCurrent();
      this.merge(page.rows, previous, startedRevision);
      this.cursor = page.page.next_cursor;
      this.boundary = page.rows.at(-1) ?? this.boundary;
      this.traversalRevision += 1;
      this.publish({
        total: page.total,
        hasMore: page.page.has_more,
        loading: false,
        loaded: true,
      });
      return;
    }
    if (!this.sweep) this.startSweep();
    const sweep = this.sweep!;
    if (sweep.phase === 'scan') {
      const page = await this.options.readPage(sweep.cursor, lease);
      lease.assertCurrent();
      for (const row of page.rows) sweep.seen.add(row.id);
      // Do not overwrite an optimistic/newer update since this sweep started.
      this.merge(page.rows, sweep.previous, sweep.startedRevision);
      for (const row of page.rows) {
        if (this.rows.get(row.id) === row)
          sweep.previous.set(row.id, this.revisions.get(row.id)!);
      }
      sweep.page = page.page;
      sweep.cursor = page.page.next_cursor;
      const last = page.rows.at(-1);
      const covered =
        !page.page.has_more ||
        sweep.boundary === null ||
        (last !== undefined && this.options.compare(last, sweep.boundary) >= 0);
      this.publish({ total: page.total, loaded: true });
      if (!covered) return;
      sweep.phase = 'revalidate';
      sweep.missing = [...sweep.previous.keys()].filter(
        (id) => !sweep.seen.has(id),
      );
      // Freeze the new actual traversal key, independent of sorted display rows.
      sweep.nextBoundary = last ?? sweep.boundary;
    } else {
      const id = sweep.missing.shift();
      if (id !== undefined) {
        const revision = this.revisions.get(id);
        const result = await this.options.readOne(id, lease);
        lease.assertCurrent();
        if (
          revision === sweep.previous.get(id) &&
          revision === this.revisions.get(id)
        ) {
          if (result.status === 403 || result.status === 404) {
            this.rows.delete(id);
            this.revisions.set(id, ++this.revision);
          } else if (result.status === 200 && result.row) {
            this.merge([result.row]);
          } else {
            throw new Error('Conversation revalidation unavailable');
          }
          this.publish();
        }
      }
    }
    if (sweep.phase === 'revalidate' && sweep.missing.length === 0) {
      if (sweep.traversalRevision === this.traversalRevision) {
        this.cursor = sweep.page!.next_cursor;
        this.boundary = sweep.nextBoundary;
        this.publish({ hasMore: sweep.page!.has_more });
      }
      this.sweep = null;
      this.publish({ refreshing: false, updatesPending: false });
    }
  }

  private async pump(): Promise<void> {
    if (this.running || this.disposed) return;
    this.running = true;
    const lease = this.scope.capture({ selected: false });
    try {
      let requests = 0;
      while (this.wantMore || this.wantRefresh || this.sweep) {
        lease.assertCurrent();
        await this.step(lease);
        lease.assertCurrent();
        if (
          ++requests === 5 &&
          (this.wantMore || this.wantRefresh || this.sweep)
        ) {
          requests = 0;
          await (this.options.yield?.() ??
            new Promise<void>((resolve) => setTimeout(resolve, 0)));
        }
      }
    } catch {
      if (lease.isCurrent()) {
        const pending = this.sweep !== null;
        this.sweep = null;
        this.wantMore = false;
        this.wantRefresh = false;
        this.publish({
          loading: false,
          refreshing: false,
          loaded: true,
          error: true,
          updatesPending: pending,
        });
      }
    } finally {
      lease.release();
      this.running = false;
      if (!this.disposed && (this.wantMore || this.wantRefresh))
        void this.pump();
    }
  }

  /** React StrictMode may reconnect the same owner after effect cleanup. */
  resume(): void {
    if (!this.disposed) return;
    this.disposed = false;
    this.scope = new HistoryScope(this.options.generation, null);
    this.syncGeneration();
    this.publish({ loading: false, refreshing: false });
  }

  dispose(): void {
    this.disposed = true;
    this.scope.dispose();
    this.wantMore = this.wantRefresh = false;
    this.sweep = null;
  }
}
