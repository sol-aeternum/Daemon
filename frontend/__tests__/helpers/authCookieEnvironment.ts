import { vi } from 'vitest';
import {
  AUTH_COORDINATION_KEY,
  type AuthCoordinationRecord,
} from '../../lib/authCookieCoordinator';
import { AuthCookieTestLocks } from './authCookieLocks';

/**
 * Owner-approved P0.2 legacy-fixture surface for suites that bind manual
 * tokens against the REAL auth/coordinator modules. Nonsecret, version-1
 * stable shared metadata (epoch/status only — never token or account
 * material) is seeded into the caller's storage so `setAccessToken` can bind
 * legitimate coordinator metadata through its expected-epoch third argument,
 * and the fake browser is qualified with the same native-model FIFO Web Locks
 * fixture (AuthCookieTestLocks) the coordinator suites use. Unsupported
 * environments fail closed; there is no lease/storage fallback to model.
 */

export const FIXTURE_AUTH_EPOCH = 'fictional-stable-epoch-1';

export function stableCoordinationRecord(): AuthCoordinationRecord {
  return { version: 1, epoch: FIXTURE_AUTH_EPOCH, status: 'stable' };
}

export function seedStableCoordination(
  storage: Pick<Storage, 'getItem' | 'setItem'>,
): void {
  storage.setItem(
    AUTH_COORDINATION_KEY,
    JSON.stringify(stableCoordinationRecord()),
  );
}

/**
 * Native-model same-name channel fan-out (the sender never receives its own
 * message). Cached auth-module channels survive setup cycles; temporary
 * channels leave the registry when closed.
 */
export class AuthTestBroadcastChannel
  extends EventTarget
  implements BroadcastChannel
{
  private static readonly channels = new Set<AuthTestBroadcastChannel>();
  private closed = false;
  readonly name: string;
  onmessage: ((this: BroadcastChannel, event: MessageEvent) => unknown) | null =
    null;
  onmessageerror:
    | ((this: BroadcastChannel, ev: MessageEvent) => unknown)
    | null = null;

  constructor(name: string) {
    super();
    this.name = name;
    AuthTestBroadcastChannel.channels.add(this);
  }

  postMessage(message: unknown): void {
    if (this.closed)
      throw new DOMException('Channel is closed', 'InvalidStateError');
    const data = structuredClone(message);
    for (const channel of AuthTestBroadcastChannel.channels) {
      if (channel === this || channel.name !== this.name) continue;
      // Delivery is deferred, and a channel closed before delivery receives
      // nothing. Each recipient gets its own event, as in a native channel.
      queueMicrotask(() => {
        if (channel.closed) return;
        const event = new MessageEvent('message', {
          data: structuredClone(data),
        });
        channel.dispatchEvent(event);
        channel.onmessage?.call(channel, event);
      });
    }
  }

  close(): void {
    this.closed = true;
    AuthTestBroadcastChannel.channels.delete(this);
  }
}

export interface AuthCookieTestEnvironment {
  /** FIFO Web Locks fixture; useful when a test must hold or queue a writer. */
  locks: AuthCookieTestLocks;
  /** The nonsecret epoch the manual token is bound to. */
  epoch: string;
  /** BroadcastChannel double, when auth-module events must be deliverable. */
  channelConstructor: typeof AuthTestBroadcastChannel;
}

export function currentStorage(): Pick<
  Storage,
  'getItem' | 'setItem' | 'removeItem'
> {
  for (const resolve of [
    () => (globalThis as { localStorage?: unknown }).localStorage,
    () =>
      (globalThis as { window?: { localStorage?: unknown } }).window
        ?.localStorage,
  ]) {
    try {
      const resolved = resolve() as Storage | undefined;
      if (
        resolved &&
        ['getItem', 'setItem', 'removeItem'].every(
          (method) => typeof resolved[method as 'getItem'] === 'function',
        )
      )
        return resolved;
    } catch {
      // A disabled/throwing storage getter is also an unavailable fixture.
    }
  }
  throw new Error(
    'No usable storage for shared auth coordination metadata in this fixture.',
  );
}

/** Explicit disposable browser storage; never an implicit runtime fallback. */
export function createAuthTestStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => {
      values.delete(key);
    },
    setItem: (key, value) => {
      values.set(key, String(value));
    },
  };
}

export function installAuthCookieTestEnvironment(
  storage?: Pick<Storage, 'getItem' | 'setItem'>,
  options: { broadcastChannel?: boolean } = {},
): AuthCookieTestEnvironment {
  const locks = new AuthCookieTestLocks();
  seedStableCoordination(storage ?? currentStorage());
  // Web Locks are unavailable in the test DOM; qualify the environment the
  // same way the coordinator suites do (secure-context-only cache clearing
  // is unaffected here and stays covered by the browser-cache suites).
  vi.stubGlobal('navigator', { locks });
  if (options.broadcastChannel) {
    // Do not discard cached auth-module channels between setup cycles.
    vi.stubGlobal('BroadcastChannel', AuthTestBroadcastChannel);
  }
  return {
    locks,
    epoch: FIXTURE_AUTH_EPOCH,
    channelConstructor: AuthTestBroadcastChannel,
  };
}
