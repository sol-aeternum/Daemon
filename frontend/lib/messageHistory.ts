import type { DaemonMessage } from './chatMessages';
import {
  compareMessageBoundary,
  type HistoryPage,
  type PersistedHistoryMessage,
  type MessageBoundary,
} from './historyPages';
import { HistoryScope, type HistoryLease } from './historyScope';

export interface MessageHistoryPage<T> {
  conversation: T;
  messages: PersistedHistoryMessage[];
  page: HistoryPage;
}

export interface MessageHistorySnapshot<T> {
  generation: number;
  id: string | null;
  scope: string;
  tail: T | null;
  messages: PersistedHistoryMessage[];
  hasOlder: boolean;
  loadingOlder: boolean;
  syncing: boolean;
  error: boolean;
  incomplete: boolean;
  retryKind: 'older' | 'range' | 'tail' | null;
  failure: 'permanent' | 'exhausted' | null;
}

/**
 * Selected persisted-history owner. Conversation.messages is ALWAYS a bounded
 * server tail, never this owner's accumulated display history. All reads within
 * one selection are serialized; a new auth/selection epoch aborts and detaches
 * the old queue, including transports that ignore cancellation.
 */
export class MessageHistory<
  T extends { id: string; messages: DaemonMessage[] },
> {
  private scope: HistoryScope;
  private generation: number;
  private id: string | null = null;
  private epoch = 0;
  private closed = false;
  private rows = new Map<string, PersistedHistoryMessage>();
  private olderCursor: string | null = null;
  private oldestCursor: string | null = null;
  private oldestBoundary: MessageBoundary | null = null;
  private gapBoundary: MessageBoundary | null = null;
  private rangePending = false;
  private initialStarted = false;
  private chain: Promise<unknown> = Promise.resolve();
  private tailRequest: Promise<T | null> | null = null;
  private olderRequest: Promise<void> | null = null;
  private rangeRequest: Promise<void> | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private listeners = new Set<() => void>();
  private tailScopes = new WeakMap<T, string>();
  private snapshot: MessageHistorySnapshot<T>;

  constructor(
    private readonly options: {
      generation: () => number;
      read: (
        id: string,
        bounds: { before?: string; through?: string },
        lease: HistoryLease,
      ) => Promise<{ status: number; value?: MessageHistoryPage<T> }>;
      retryDelays: readonly number[];
      yield?: () => Promise<void>;
    },
  ) {
    this.generation = options.generation();
    this.scope = new HistoryScope(options.generation, null);
    this.snapshot = this.empty();
  }

  private empty(): MessageHistorySnapshot<T> {
    return {
      generation: this.generation,
      id: this.id,
      scope: `${this.generation}:${this.epoch}:${this.id ?? ''}`,
      tail: null,
      messages: [],
      hasOlder: false,
      loadingOlder: false,
      syncing: false,
      error: false,
      incomplete: false,
      retryKind: null,
      failure: null,
    };
  }

  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  private publish(patch: Partial<MessageHistorySnapshot<T>> = {}): void {
    if (this.closed) return;
    this.snapshot = {
      ...this.snapshot,
      ...patch,
      incomplete: this.rangePending || this.gapBoundary !== null,
      messages: [...this.rows.values()].sort(compareMessageBoundary),
    };
    for (const listener of this.listeners) listener();
  }

  private reset(): void {
    this.epoch += 1;
    this.scope.dispose();
    this.scope = new HistoryScope(this.options.generation, this.id);
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.rows.clear();
    this.olderCursor = this.oldestCursor = null;
    this.oldestBoundary = null;
    this.gapBoundary = null;
    this.rangePending = false;
    this.initialStarted = false;
    this.chain = Promise.resolve();
    this.tailRequest = null;
    this.olderRequest = this.rangeRequest = null;
    this.snapshot = this.empty();
    this.publish();
  }

  select(id: string | null): void {
    const generation = this.options.generation();
    if (this.closed) {
      this.id = id;
      this.generation = generation;
      return;
    }
    if (this.id === id && this.generation === generation) return;
    this.id = id;
    this.generation = generation;
    this.reset();
  }

  resume(): void {
    if (!this.closed) return;
    this.closed = false;
    this.generation = this.options.generation();
    this.reset();
  }

  private current(epoch: number, generation: number): boolean {
    return (
      !this.closed &&
      this.epoch === epoch &&
      this.options.generation() === generation
    );
  }

  /** Consumers recheck immediately before adopting a returned SDK tail. */
  canUse = (conversation: T): boolean =>
    !this.closed &&
    this.snapshot.generation === this.options.generation() &&
    this.snapshot.tail === conversation &&
    this.tailScopes.get(conversation) === this.snapshot.scope;

  captureOperation(): HistoryLease {
    if (!this.closed) this.select(this.id);
    return this.scope.capture();
  }

  isScopeCurrent = (scope: string): boolean =>
    !this.closed &&
    this.snapshot.generation === this.options.generation() &&
    this.snapshot.scope === scope;

  private enqueue<R>(
    fallback: R,
    work: (lease: HistoryLease, id: string) => Promise<R>,
    kind: 'older' | 'range' | 'tail',
  ): Promise<R> {
    const epoch = this.epoch;
    const generation = this.generation;
    const id = this.id;
    const result = this.chain.then(async () => {
      if (id === null || !this.current(epoch, generation)) return fallback;
      const lease = this.scope.capture();
      try {
        const value = await work(lease, id);
        lease.assertCurrent();
        return this.current(epoch, generation) ? value : fallback;
      } catch {
        if (lease.isCurrent() && this.current(epoch, generation))
          this.publish({ error: true, syncing: false, retryKind: kind });
        return fallback;
      } finally {
        lease.release();
      }
    });
    this.chain = result.then(() => undefined);
    return result;
  }

  private merge(messages: PersistedHistoryMessage[]): void {
    for (const message of messages) this.rows.set(message.id, message);
  }

  private installTail(value: MessageHistoryPage<T>): T {
    const tail = { ...value.conversation, messages: value.messages };
    this.tailScopes.set(tail, this.snapshot.scope);
    this.publish({ tail, error: false, retryKind: null, failure: null });
    return tail;
  }

  private async page(
    id: string,
    bounds: { before?: string; through?: string },
    lease: HistoryLease,
  ) {
    const result = await this.options.read(id, bounds, lease);
    lease.assertCurrent();
    if (result.status === 403 || result.status === 404) {
      this.rows.clear();
      this.olderCursor = this.oldestCursor = null;
      this.oldestBoundary = null;
      this.gapBoundary = null;
      this.rangePending = false;
      this.publish({ tail: null, hasOlder: false, failure: 'permanent' });
      throw new Error('Conversation permanently unavailable');
    }
    if (result.status !== 200 || !result.value)
      throw new Error('History unavailable');
    return result.value;
  }

  /** Initial/follower read: return only the newest bounded transport window. */
  refresh = (): Promise<T | null> => {
    if (this.closed) return Promise.resolve(null);
    if (this.tailRequest) return this.tailRequest;
    const request = this.enqueue<T | null>(
      null,
      async (lease, id) => {
        const previousNewest =
          this.gapBoundary ?? this.snapshot.messages.at(-1);
        const initial = this.oldestCursor === null;
        const value = await this.page(id, {}, lease);
        this.merge(value.messages);
        if (initial) {
          this.olderCursor = value.page.next_cursor;
          this.oldestCursor = value.page.start_cursor;
          this.oldestBoundary = value.messages[0] ?? null;
          this.publish({ hasOlder: value.page.has_more });
        }
        const tail = this.installTail(value);
        let bridge = value;
        let requests = 1;
        const disconnected = () =>
          previousNewest !== undefined &&
          bridge.messages.length > 0 &&
          compareMessageBoundary(bridge.messages[0], previousNewest) > 0 &&
          bridge.page.has_more;
        if (disconnected()) {
          this.gapBoundary = previousNewest!;
          this.publish({ syncing: true });
        }
        while (disconnected()) {
          if (requests++ % 5 === 0) await this.yieldSlice(lease);
          bridge = await this.page(
            id,
            { before: bridge.page.next_cursor! },
            lease,
          );
          this.merge(bridge.messages);
          this.publish();
        }
        this.gapBoundary = null;
        this.publish({ syncing: false });
        return this.canUse(tail) ? tail : null;
      },
      'tail',
    );
    this.tailRequest = request;
    void request.finally(() => {
      if (this.tailRequest === request) this.tailRequest = null;
    });
    return request;
  };

  startInitial = (): void => {
    if (
      this.initialStarted ||
      this.id === null ||
      this.snapshot.tail !== null ||
      this.snapshot.failure !== null
    )
      return;
    this.initialStarted = true;
    const epoch = this.epoch;
    const generation = this.generation;
    const attempt = async (index: number) => {
      const tail = await this.refresh();
      if (
        !this.current(epoch, generation) ||
        tail ||
        this.snapshot.failure === 'permanent'
      )
        return;
      if (index >= this.options.retryDelays.length) {
        this.publish({ failure: 'exhausted' });
        return;
      }
      this.retryTimer = setTimeout(() => {
        this.retryTimer = null;
        void attempt(index + 1);
      }, this.options.retryDelays[index]);
    };
    void attempt(0);
  };

  loadOlder = (): Promise<void> => {
    if (this.closed) return Promise.resolve();
    if (this.olderRequest) return this.olderRequest;
    if (!this.snapshot.hasOlder || !this.olderCursor) return Promise.resolve();
    const request = this.enqueue(
      undefined,
      async (lease, id) => {
        this.publish({ loadingOlder: true, error: false, retryKind: null });
        try {
          const value = await this.page(
            id,
            { before: this.olderCursor! },
            lease,
          );
          this.merge(value.messages);
          this.olderCursor = value.page.next_cursor;
          this.oldestCursor = value.page.start_cursor ?? this.oldestCursor;
          this.oldestBoundary = value.messages[0] ?? this.oldestBoundary;
          this.publish({ hasOlder: value.page.has_more });
        } finally {
          if (lease.isCurrent()) this.publish({ loadingOlder: false });
        }
      },
      'older',
    );
    this.olderRequest = request;
    void request.finally(() => {
      if (this.olderRequest === request) this.olderRequest = null;
    });
    return request;
  };

  /** Covered-range absence is applied only after every bounded page succeeds. */
  refreshRange = (): Promise<void> => {
    if (this.closed) return Promise.resolve();
    if (this.rangeRequest) return this.rangeRequest;
    if (!this.oldestCursor) return this.refresh().then(() => undefined);
    const request = this.enqueue(
      undefined,
      async (lease, id) => {
        const through = this.oldestCursor!;
        const oldest = this.oldestBoundary;
        const seen = new Set<string>();
        this.rangePending = true;
        this.publish({ syncing: true, error: false, retryKind: null });
        let before: string | undefined;
        let first = true;
        let requests = 0;
        do {
          if (requests++ > 0 && requests % 5 === 1)
            await this.yieldSlice(lease);
          const value = await this.page(id, { before, through }, lease);
          for (const message of value.messages) seen.add(message.id);
          this.merge(value.messages);
          if (first) this.installTail(value);
          else this.publish();
          first = false;
          before = value.page.next_cursor ?? undefined;
        } while (before !== undefined);
        for (const [key, message] of this.rows) {
          if (
            oldest &&
            compareMessageBoundary(message, oldest) >= 0 &&
            !seen.has(key)
          )
            this.rows.delete(key);
        }
        this.rangePending = false;
        this.gapBoundary = null;
        this.publish({ syncing: false });
      },
      'range',
    );
    this.rangeRequest = request;
    void request.finally(() => {
      if (this.rangeRequest === request) this.rangeRequest = null;
    });
    return request;
  };

  private async yieldSlice(lease: HistoryLease): Promise<void> {
    await (this.options.yield?.() ??
      new Promise<void>((resolve) => setTimeout(resolve, 0)));
    lease.assertCurrent();
  }

  dispose(): void {
    this.closed = true;
    this.scope.dispose();
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.retryTimer = null;
  }
}
