'use client';

import { DEFAULT_TTS_SETTINGS, type TtsSettings } from './constants';

/**
 * Active read-aloud preference source.
 *
 * The generic `useLocalStorage` hook keeps one private snapshot per component
 * instance, so an already-mounted read button never observes a settings change.
 * This store is a small validated external store instead: one shared snapshot,
 * same-tab notification from the setter, and browser `storage` notification for
 * other tabs. Reads never write, so a corrupt or partial value is normalized in
 * memory only.
 */

export const TTS_SETTINGS_STORAGE_KEY = 'tts_settings';

export const DEFAULT_TTS_VOICE = 'daemon-default';

const TTS_FORMATS = ['mp3', 'opus', 'wav'] as const;

export const MIN_TTS_SPEED = 0.5;
export const MAX_TTS_SPEED = 2.0;

/**
 * Mirrors the server's legacy voice migration in `orchestrator/speech/contracts.py`.
 * Older settings offered these names without a working voice catalogue; they map
 * centrally to the single default voice. Unknown values are not forwarded.
 */
const LEGACY_TTS_VOICES = new Set(
  (
    'Xb7hH8MSUJpSbSDYk0k2 allay amy aria ashley char emma josh rachel sage ' +
    'sam james ari adam drew clyde diana ellen fiona george grace henry io ' +
    'jenny kevin lily marcus michelle patrick sarah steve tiffany tim will'
  ).split(' '),
);

function normalizeVoice(value: unknown): string {
  if (value === DEFAULT_TTS_VOICE) return DEFAULT_TTS_VOICE;
  if (typeof value === 'string' && LEGACY_TTS_VOICES.has(value)) {
    return DEFAULT_TTS_VOICE;
  }
  return DEFAULT_TTS_SETTINGS.voice;
}

function normalizeSpeed(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return DEFAULT_TTS_SETTINGS.speed;
  }
  if (value < MIN_TTS_SPEED || value > MAX_TTS_SPEED) {
    return DEFAULT_TTS_SETTINGS.speed;
  }
  return value;
}

function normalizeFormat(value: unknown): string {
  if (
    typeof value === 'string' &&
    (TTS_FORMATS as readonly string[]).includes(value)
  ) {
    return value;
  }
  return DEFAULT_TTS_SETTINGS.format;
}

function normalizeBoolean(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

/**
 * Merge a partial, legacy or corrupt stored object onto the defaults.
 * Never throws and never preserves an unusable value.
 */
export function normalizeTtsSettings(raw: unknown): TtsSettings {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { ...DEFAULT_TTS_SETTINGS };
  }
  const source = raw as Record<string, unknown>;
  return {
    enabled: normalizeBoolean(source.enabled, DEFAULT_TTS_SETTINGS.enabled),
    autoPlay: normalizeBoolean(source.autoPlay, DEFAULT_TTS_SETTINGS.autoPlay),
    voice: normalizeVoice(source.voice),
    model:
      typeof source.model === 'string' && source.model.trim()
        ? source.model
        : DEFAULT_TTS_SETTINGS.model,
    speed: normalizeSpeed(source.speed),
    format: normalizeFormat(source.format),
  };
}

function sameTtsSettings(a: TtsSettings, b: TtsSettings): boolean {
  return (
    a.enabled === b.enabled &&
    a.autoPlay === b.autoPlay &&
    a.voice === b.voice &&
    a.model === b.model &&
    a.speed === b.speed &&
    a.format === b.format
  );
}

type TtsSettingsListener = () => void;

const listeners = new Set<TtsSettingsListener>();
let snapshot: TtsSettings | null = null;
let storageListenerAttached = false;

function readStoredTtsSettings(): TtsSettings {
  if (typeof window === 'undefined') return { ...DEFAULT_TTS_SETTINGS };
  try {
    const raw = window.localStorage.getItem(TTS_SETTINGS_STORAGE_KEY);
    if (raw == null) return { ...DEFAULT_TTS_SETTINGS };
    return normalizeTtsSettings(JSON.parse(raw));
  } catch {
    // Corrupt JSON, blocked storage or a non-object payload: use defaults
    // without rewriting the user's stored value.
    return { ...DEFAULT_TTS_SETTINGS };
  }
}

function handleStorageEvent(event: StorageEvent): void {
  if (event.storageArea && event.storageArea !== window.localStorage) return;
  // `key === null` means the whole storage area was cleared.
  if (event.key !== null && event.key !== TTS_SETTINGS_STORAGE_KEY) return;
  refreshTtsSettings();
}

function attachStorageListener(): void {
  if (storageListenerAttached) return;
  if (typeof window === 'undefined') return;
  window.addEventListener('storage', handleStorageEvent);
  storageListenerAttached = true;
}

function detachStorageListener(): void {
  if (!storageListenerAttached) return;
  if (typeof window === 'undefined') return;
  window.removeEventListener('storage', handleStorageEvent);
  storageListenerAttached = false;
}

function refreshTtsSettings(): TtsSettings {
  const next = readStoredTtsSettings();
  const current = snapshot;
  if (current && sameTtsSettings(current, next)) return current;
  snapshot = next;
  for (const listener of [...listeners]) listener();
  return next;
}

/**
 * Stable snapshot for `useSyncExternalStore`: identical object identity until
 * the stored preference actually changes.
 */
export function getTtsSettingsSnapshot(): TtsSettings {
  if (!snapshot) snapshot = readStoredTtsSettings();
  return snapshot;
}

export function subscribeTtsSettings(
  listener: TtsSettingsListener,
): () => void {
  // Changes while there were no consumers produced no storage events here.
  // Refresh before subscribing; useSyncExternalStore checks again after subscribe.
  if (listeners.size === 0) refreshTtsSettings();
  listeners.add(listener);
  attachStorageListener();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) detachStorageListener();
  };
}

/** Server snapshot used during SSR/hydration; never reads storage. */
export function getDefaultTtsSettingsSnapshot(): TtsSettings {
  return DEFAULT_TTS_SETTINGS;
}

export function setTtsSettings(
  update: TtsSettings | ((previous: TtsSettings) => TtsSettings),
): TtsSettings {
  const current = getTtsSettingsSnapshot();
  const requested = typeof update === 'function' ? update(current) : update;
  const next = normalizeTtsSettings(requested);
  if (typeof window !== 'undefined') {
    try {
      window.localStorage.setItem(
        TTS_SETTINGS_STORAGE_KEY,
        JSON.stringify(next),
      );
    } catch {
      // Private-mode/blocked storage still updates in-memory subscribers.
    }
  }
  const previous = snapshot;
  snapshot = next;
  if (previous && sameTtsSettings(previous, next)) return next;
  for (const listener of [...listeners]) listener();
  return next;
}
