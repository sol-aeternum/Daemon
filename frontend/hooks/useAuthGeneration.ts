'use client';

import { useSyncExternalStore } from 'react';
import { getAuthGeneration, subscribeAuthGeneration } from '@/lib/auth';

export function useAuthGeneration() {
  return useSyncExternalStore(
    subscribeAuthGeneration,
    getAuthGeneration,
    () => 0,
  );
}
