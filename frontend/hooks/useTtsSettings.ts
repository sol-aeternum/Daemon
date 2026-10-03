'use client';

import { useCallback, useSyncExternalStore } from 'react';
import type { TtsSettings } from '../lib/constants';
import {
  getDefaultTtsSettingsSnapshot,
  getTtsSettingsSnapshot,
  setTtsSettings,
  subscribeTtsSettings,
} from '../lib/ttsSettings';

/**
 * Subscribed read-aloud preferences.
 *
 * Mounted consumers observe same-tab writes from VoiceTab and cross-tab
 * `storage` changes/clears, unlike a per-instance `useLocalStorage` snapshot.
 */
export function useTtsSettings(): {
  value: TtsSettings;
  setValue: (
    update: TtsSettings | ((previous: TtsSettings) => TtsSettings),
  ) => void;
} {
  const value = useSyncExternalStore(
    subscribeTtsSettings,
    getTtsSettingsSnapshot,
    getDefaultTtsSettingsSnapshot,
  );

  const setValue = useCallback(
    (update: TtsSettings | ((previous: TtsSettings) => TtsSettings)) => {
      setTtsSettings(update);
    },
    [],
  );

  return { value, setValue };
}
