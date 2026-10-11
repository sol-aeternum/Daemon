import { expect, vi } from 'vitest';
import { AUTH_COOKIE_LOCK } from '../../lib/authCookieCoordinator';

// FIFO Web Locks fixture: queued abort never releases a holder. A holder only
// releases when its callback (including owned native transport) terminates.
export class AuthCookieTestLocks {
  held = false;
  queue: Array<() => Promise<void>> = [];
  request = vi.fn(
    <T>(
      name: string,
      options: { signal: AbortSignal },
      action: () => Promise<T>,
    ) => {
      expect(name).toBe(AUTH_COOKIE_LOCK);
      return new Promise<T>((resolve, reject) => {
        const start = async () => {
          if (options.signal.aborted) {
            reject(options.signal.reason);
            return;
          }
          this.held = true;
          options.signal.removeEventListener('abort', cancel);
          try {
            resolve(await action());
          } catch (error) {
            reject(error);
          } finally {
            this.held = false;
            this.drain();
          }
        };
        const cancel = () => {
          this.queue = this.queue.filter((item) => item !== start);
          reject(options.signal.reason);
        };
        options.signal.addEventListener('abort', cancel, { once: true });
        this.queue.push(start);
        this.drain();
      });
    },
  );
  drain() {
    if (!this.held) void this.queue.shift()?.();
  }
}
