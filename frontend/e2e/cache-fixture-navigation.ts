// The bare esbuild fixture has no Next router. Supply only its pathname primitive
// from the actual browser URL; auth/cache/coordination stay production modules.
import { useSyncExternalStore } from 'react';

const listeners = new Set<() => void>();
let releaseHistory: (() => void) | undefined;

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) {
    const history = window.history;
    const push = history.pushState;
    const replace = history.replaceState;
    const notify = () => listeners.forEach((current) => current());
    const wrappedPush: History['pushState'] = function (
      this: History,
      ...args
    ) {
      push.apply(this, args);
      notify();
    };
    const wrappedReplace: History['replaceState'] = function (
      this: History,
      ...args
    ) {
      replace.apply(this, args);
      notify();
    };
    history.pushState = wrappedPush;
    history.replaceState = wrappedReplace;
    window.addEventListener('popstate', notify);
    releaseHistory = () => {
      window.removeEventListener('popstate', notify);
      // Never overwrite another participant's later history instrumentation.
      if (history.pushState === wrappedPush) history.pushState = push;
      if (history.replaceState === wrappedReplace)
        history.replaceState = replace;
    };
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      releaseHistory?.();
      releaseHistory = undefined;
    }
  };
}

export function usePathname(): string | null {
  return useSyncExternalStore(
    subscribe,
    () => window.location.pathname,
    () => null,
  );
}
