import type { DaemonMessage } from './chatMessages';
import { historyTimestamp, type MessageBoundary } from './historyPages';

/** Presentation metadata only: no persisted-message cache or SDK message copy. */
export class TailAdoption {
  private snapshot: { scope: string; oldest: MessageBoundary | null };
  private listeners = new Set<() => void>();

  constructor(scope: string, messages: readonly DaemonMessage[]) {
    this.snapshot = { scope, oldest: this.boundary(messages) };
  }

  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  private boundary(messages: readonly DaemonMessage[]): MessageBoundary | null {
    const first = messages[0];
    if (!first || typeof first.created_at !== 'string') return null;
    historyTimestamp(first.created_at);
    return { id: first.id, created_at: first.created_at };
  }

  adopt(
    scope: string,
    messages: readonly DaemonMessage[],
    current: () => boolean,
    apply: () => void,
  ): boolean {
    if (!current()) return false;
    const next = { scope, oldest: this.boundary(messages) };
    if (!current()) return false;
    const previous = this.snapshot;
    // The cut must already be visible if the SDK's external-store setter
    // synchronously starts a render. React then observes both current snapshots.
    this.snapshot = next;
    try {
      apply();
    } catch (error) {
      this.snapshot = previous;
      throw error;
    } finally {
      for (const listener of this.listeners) listener();
    }
    return true;
  }
}
