'use client';

import { useSyncExternalStore } from 'react';

const subscribe = () => () => {};

export function useClientMounted() {
  return useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
}
