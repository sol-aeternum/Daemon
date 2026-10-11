'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';
import {
  completeIdentityReadiness,
  getAuthGeneration,
  isAuthResultCurrent,
  revokeDevice,
  subscribeAuthGeneration,
  type RefreshResult,
} from '@/lib/auth';
import { subscribeAuthCoordination } from '@/lib/authCookieCoordinator';
import {
  getBrowserCacheState,
  subscribeBrowserCacheState,
} from '@/lib/browserCache';

// A public route registers its credential form after the initial startup gate.
// Only that public owner may survive a subsequent temporary privacy gate; this
// is not permission to retain a protected subtree (even hidden or inert).
export const PublicCredentialOwnerContext = createContext<
  (() => () => void) | null
>(null);

export function usePublicCredentialOwner() {
  const register = useContext(PublicCredentialOwnerContext);
  useEffect(() => register?.(), [register]);
  return useCallback(
    async (result: RefreshResult, signal: AbortSignal) => {
      // Standalone callers have no provider gate. The real root layout supplies
      // this context; isolated component-seam tests retain their caller contract.
      if (!register) return !signal.aborted && isAuthResultCurrent(result);
      return new Promise<boolean>((resolve) => {
        let done = false;
        const unsubscribes: Array<() => void> = [];
        const finish = (ready: boolean) => {
          if (done) return;
          done = true;
          unsubscribes.forEach((unsubscribe) => unsubscribe());
          signal.removeEventListener('abort', check);
          resolve(ready);
        };
        const check = () => {
          if (signal.aborted || !isAuthResultCurrent(result)) finish(false);
          else if (
            getBrowserCacheState().status === 'ready' &&
            completeIdentityReadiness(result, signal)
          )
            finish(true);
          // A failed cache attempt stays pending here. Only explicit gate Retry
          // can release this same successful intent; never resubmit credentials.
        };
        unsubscribes.push(
          subscribeBrowserCacheState(check),
          subscribeAuthGeneration(check),
          subscribeAuthCoordination(check),
        );
        signal.addEventListener('abort', check, { once: true });
        check();
      });
    },
    [register],
  );
}

interface DeviceTarget {
  id: string;
  current: boolean;
}
interface DeviceOutcome {
  id: string;
  result: RefreshResult;
}
interface DeviceActionState {
  pendingId: string | null;
  outcome: DeviceOutcome | null;
  revokedIds: string[];
  generation: number;
}

export function useOwnedDeviceAction(route?: string) {
  const [state, setState] = useState<DeviceActionState>(() => ({
    pendingId: null,
    outcome: null,
    revokedIds: [],
    generation: getAuthGeneration(),
  }));
  const operation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const routeRef = useRef(route);
  useEffect(() => {
    routeRef.current = route;
  }, [route]);
  const cancel = useCallback(() => {
    operation.current += 1;
    controller.current?.abort();
    controller.current = null;
    setState({
      pendingId: null,
      outcome: null,
      revokedIds: [],
      generation: getAuthGeneration(),
    });
  }, []);
  useEffect(
    () => () => {
      operation.current += 1;
      controller.current?.abort();
    },
    [],
  );
  const revoke = useCallback(async (device: DeviceTarget) => {
    controller.current?.abort();
    const ownedController = new AbortController();
    controller.current = ownedController;
    const ownedOperation = ++operation.current;
    const generation = getAuthGeneration();
    const ownedRoute = routeRef.current;
    const ownedWindowPath =
      typeof window !== 'undefined' ? window.location?.pathname : undefined;
    setState((previous) => ({
      pendingId: device.id,
      outcome: null,
      revokedIds: previous.generation === generation ? previous.revokedIds : [],
      generation,
    }));
    let result: RefreshResult;
    try {
      result = await revokeDevice(device.id, {
        current: device.current,
        signal: ownedController.signal,
      });
    } catch (error) {
      result = {
        success: false,
        error:
          error instanceof Error ? error.message : 'Failed to revoke device',
        generation,
      };
    }
    if (ownedController.signal.aborted || ownedOperation !== operation.current)
      return;
    if (
      routeRef.current !== ownedRoute ||
      (typeof window !== 'undefined' &&
        window.location?.pathname !== ownedWindowPath)
    ) {
      setState({
        pendingId: null,
        outcome: null,
        revokedIds: [],
        generation: getAuthGeneration(),
      });
      return;
    }
    controller.current = null;
    if (!isAuthResultCurrent(result)) {
      setState({
        pendingId: null,
        outcome: null,
        revokedIds: [],
        generation: getAuthGeneration(),
      });
      return;
    }
    // The helper may itself commit a signed-out generation. Qualify its
    // terminal result, not the entry generation, before navigating/reporting.
    setState((previous) => ({
      pendingId: null,
      outcome: { id: device.id, result },
      revokedIds: result.success
        ? [...previous.revokedIds, device.id]
        : previous.revokedIds,
      generation: result.generation!,
    }));
    if (result.success && device.current && typeof window !== 'undefined')
      window.location.replace('/setup');
  }, []);
  return { ...state, revoke, cancel };
}

export type DeviceActionOwner = ReturnType<typeof useOwnedDeviceAction> & {
  releaseView: () => void;
};
export const DeviceActionOwnerContext = createContext<DeviceActionOwner | null>(
  null,
);

export function useDeviceActionOwner() {
  const shared = useContext(DeviceActionOwnerContext);
  const local = useOwnedDeviceAction();
  // Outside the root layout, the view itself remains the action owner. In the
  // actual layout only the provider-owned operation survives gated unmounts.
  return shared ?? { ...local, releaseView: local.cancel };
}
